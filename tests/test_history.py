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
