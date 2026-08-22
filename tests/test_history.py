"""Series-of-events view: how entities changed across a range of commits.

A cumulative diff between two revisions loses the sequence. Answering "how did
this evolve, and when did the behaviour actually change" needs per-commit
narrative, which is what this covers.
"""

from __future__ import annotations

import subprocess

import pytest

from gita.history import entity_history, series
from gita.vcs.git import Repo


@pytest.fixture
def evolving(tmp_path):
    """Four commits: introduce, change body, change signature, leave alone."""

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    def commit(message, source, extra=None):
        (tmp_path / "svc.py").write_text(source)
        if extra:
            (tmp_path / "other.py").write_text(extra)
        git("add", "-A")
        git("commit", "-q", "-m", message)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")

    commit("add fetch", "def fetch(url):\n    return get(url)\n")
    commit("retry on failure",
           "def fetch(url):\n    for _ in range(3):\n        return get(url)\n")
    commit("add timeout parameter",
           "def fetch(url, timeout):\n    for _ in range(3):\n        return get(url, timeout)\n")
    commit("unrelated helper",
           "def fetch(url, timeout):\n    for _ in range(3):\n        return get(url, timeout)\n",
           extra="def helper():\n    return 1\n")
    return Repo(tmp_path)


class TestSeries:
    def test_one_entry_per_commit(self, evolving):
        assert len(series(evolving, limit=4)) == 4

    def test_newest_first(self, evolving):
        subjects = [c.subject for c in series(evolving, limit=4)]
        assert subjects[0] == "unrelated helper"
        assert subjects[-1] == "add fetch"

    def test_each_entry_carries_its_changes(self, evolving):
        latest = series(evolving, limit=1)[0]
        assert latest.sha and latest.date
        assert any("helper" in c.entity.id for c in latest.changes)

    def test_limit_is_respected(self, evolving):
        assert len(series(evolving, limit=2)) == 2

    def test_commits_that_touch_nothing_relevant_are_still_listed(self, evolving):
        assert all(c.subject for c in series(evolving, limit=4))


class TestEntityHistory:
    def test_follows_one_entity_only(self, evolving):
        events = entity_history(evolving, "svc.py::fetch", limit=10)
        assert events
        assert all(e.entity_id == "svc.py::fetch" for e in events)

    def test_reports_what_happened_in_each_commit(self, evolving):
        kinds = [e.kind.value for e in entity_history(evolving, "svc.py::fetch", limit=10)]
        assert "signature_changed" in kinds
        assert "body_changed" in kinds
        assert "added" in kinds

    def test_ordered_newest_first(self, evolving):
        events = entity_history(evolving, "svc.py::fetch", limit=10)
        assert events[0].kind.value == "signature_changed"
        assert events[-1].kind.value == "added"

    def test_commits_that_left_it_alone_are_omitted(self, evolving):
        events = entity_history(evolving, "svc.py::fetch", limit=10)
        assert "unrelated helper" not in [e.subject for e in events]

    def test_unknown_entity_has_no_history(self, evolving):
        assert entity_history(evolving, "svc.py::nope", limit=10) == []

    def test_carries_commit_identity(self, evolving):
        event = entity_history(evolving, "svc.py::fetch", limit=10)[0]
        assert event.sha and event.subject and event.date


class TestPruningByPath:
    """git answers `git log -- <path>` in milliseconds because it compares tree
    hashes and never opens a file that cannot have changed. gita walked every
    file of every commit to answer a question about one entity: 9.75s and 65
    file parses to produce a single event, against 0.078s for git's equivalent.

    Pruning must make it cheaper without making it different.
    """

    def noisy(self, tmp_path):
        """One commit touching the entity, many touching everything else."""
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        (tmp_path / "svc.py").write_text("def fetch(url):\n    return get(url)\n")
        git("add", "-A")
        git("commit", "-q", "-m", "add fetch")

        for i in range(8):
            (tmp_path / f"noise{i}.py").write_text(f"def noise{i}():\n    return {i}\n")
            git("add", "-A")
            git("commit", "-q", "-m", f"unrelated {i}")

        (tmp_path / "svc.py").write_text(
            "def fetch(url, timeout=5):\n    return get(url, timeout)\n")
        git("add", "-A")
        git("commit", "-q", "-m", "add timeout")
        return Repo(tmp_path)

    def test_pruning_does_not_change_the_answer(self, tmp_path):
        repo = self.noisy(tmp_path)
        pruned = entity_history(repo, "svc.py::fetch", limit=20)
        full = entity_history(repo, "svc.py::fetch", limit=20, prune=False)
        assert [(e.short, e.kind) for e in pruned] == [(e.short, e.kind) for e in full]

    def test_a_bare_name_is_pruned_too(self, tmp_path):
        """Agents type `fetch`, so the fast path cannot depend on the full id."""
        repo = self.noisy(tmp_path)
        assert [e.kind for e in entity_history(repo, "fetch", limit=20)] == \
               [e.kind for e in entity_history(repo, "fetch", limit=20, prune=False)]

    def test_files_that_cannot_contain_the_entity_are_never_parsed(self, tmp_path):
        repo = self.noisy(tmp_path)
        from gita import revisions
        seen = []
        original = revisions.extract_path

        def spy(blob, path, *args, **kwargs):
            seen.append(path)
            return original(blob, path, *args, **kwargs)

        revisions.extract_path = spy
        try:
            entity_history(repo, "svc.py::fetch", limit=20)
        finally:
            revisions.extract_path = original
        assert seen, "expected at least one parse"
        assert not [p for p in seen if p.startswith("noise")]

    def test_an_unknown_name_still_returns_nothing(self, tmp_path):
        repo = self.noisy(tmp_path)
        assert entity_history(repo, "not_a_function", limit=20) == []


class TestOneWalkNotOnePerCommit:
    """`series` asked git for the parent and the file list of every commit
    separately: 20 commits meant 40 processes. `git log --name-status` answers
    both in one. The walk must stay identical, merges included.
    """

    def test_walk_reports_parent_and_files(self, evolving):
        records = evolving.walk(limit=4)
        assert len(records) == 4
        assert all(r.sha and r.subject for r in records)
        newest = records[0]
        assert newest.parent
        assert any(f.path.endswith(".py") for f in newest.files)

    def test_walk_matches_per_commit_queries(self, evolving):
        for record in evolving.walk(limit=4):
            assert record.parent == evolving.base_of(record.sha)
            expected = {(f.status, f.path)
                        for f in evolving.changed_files(record.parent, record.sha,
                                                        supported_only=False)}
            assert {(f.status, f.path) for f in record.files} == expected

    def test_a_root_commit_has_no_parent_commit(self, evolving):
        oldest = evolving.walk(limit=50)[-1]
        assert oldest.parent == evolving.base_of(oldest.sha)

    def test_paths_restrict_the_walk(self, evolving):
        restricted = evolving.walk(limit=50, paths=["other.py"])
        assert restricted
        assert all(any(f.path == "other.py" for f in r.files) for r in restricted)

    def test_series_is_unchanged_by_batching(self, evolving):
        from gita.history import series
        batched = series(evolving, limit=4)
        single = series(evolving, limit=4, batched=False)
        assert [(s.sha, sorted(c.entity.id for c in s.changes)) for s in batched] == \
               [(s.sha, sorted(c.entity.id for c in s.changes)) for s in single]
