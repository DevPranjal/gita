"""Is this used anywhere?

On the uncommitted-work task the agent asked "is anything incomplete or unwired?"
and answered it with `git status` plus `git diff -U15` -- reaching for surrounding
context because gita could not say whether a new function was referenced. gita
became an extra call rather than a replacement.

Dead-code detection is the smallest genuinely useful slice of blast radius, and
it is the question a reviewer actually asks about an addition.
"""

from __future__ import annotations

import subprocess

import pytest

from gita import diff_revisions
from gita.context.references import reference_counts, unreferenced
from gita.vcs.git import Repo


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "core.py").write_bytes(
        b"def used_helper():\n    return 1\n\n\n"
        b"def caller():\n    return used_helper()\n")
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    return Repo(tmp_path)


class TestReferenceCounts:
    def test_counts_uses_excluding_the_definition(self, repo):
        counts = reference_counts(repo, ["used_helper"])
        assert counts["used_helper"] >= 1

    def test_unused_name_scores_zero(self, repo):
        (repo.root / "core.py").write_bytes(
            (repo.root / "core.py").read_bytes()
            + b"\n\ndef orphan():\n    return 2\n")
        assert reference_counts(repo, ["orphan"])["orphan"] == 0

    def test_unknown_name_scores_zero(self, repo):
        assert reference_counts(repo, ["nowhere_at_all"])["nowhere_at_all"] == 0

    def test_handles_many_names_at_once(self, repo):
        counts = reference_counts(repo, ["used_helper", "caller", "missing"])
        assert set(counts) == {"used_helper", "caller", "missing"}

    def test_empty_input_is_safe(self, repo):
        assert reference_counts(repo, []) == {}

    def test_regex_metacharacters_are_literal(self, repo):
        """A name like `Iterator for Walk` must not be treated as a pattern."""
        assert reference_counts(repo, ["a.*b"])["a.*b"] == 0


class TestUnreferenced:
    def test_flags_an_added_but_unused_entity(self, repo):
        (repo.root / "core.py").write_bytes(
            (repo.root / "core.py").read_bytes()
            + b"\n\ndef orphan():\n    return 2\n")
        changeset = diff_revisions(repo, "HEAD", None)
        added = unreferenced(repo, changeset)
        assert any("orphan" in name for name in added)

    def test_does_not_flag_a_used_entity(self, repo):
        (repo.root / "core.py").write_bytes(
            b"def used_helper():\n    return 1\n\n\n"
            b"def caller():\n    return used_helper()\n\n\n"
            b"def wrapper():\n    return caller()\n")
        changeset = diff_revisions(repo, "HEAD", None)
        assert not any("caller" in n for n in unreferenced(repo, changeset))

    def test_only_considers_additions(self, repo):
        """A modified function is obviously referenced or the caller knows why."""
        (repo.root / "core.py").write_bytes(
            b"def used_helper():\n    return 99\n\n\n"
            b"def caller():\n    return used_helper()\n")
        changeset = diff_revisions(repo, "HEAD", None)
        assert unreferenced(repo, changeset) == []

    def test_empty_changeset_is_safe(self, repo):
        assert unreferenced(repo, diff_revisions(repo, "HEAD", None)) == []


class TestTestsAreNotDeadCode:
    """A test is called by its runner, never by name.

    On `got-new-option` every one of the 25 lookups we can afford was spent on
    added test cases, all of which were then reported as unreferenced. That is a
    false alarm that also starved the source changes of any check at all.
    """

    def test_added_tests_are_never_reported(self, repo):
        (repo.root / "test").mkdir()
        (repo.root / "test" / "core_test.py").write_bytes(
            b"def test_orphan():\n    assert True\n")
        found = unreferenced(repo, diff_revisions(repo, "HEAD", None))
        assert not any("core_test" in entity_id for entity_id in found)

    def test_source_is_still_checked_when_tests_dominate(self, repo):
        (repo.root / "test").mkdir()
        (repo.root / "test" / "core_test.py").write_bytes(
            b"".join(f"def test_case_{i}():\n    assert True\n\n\n".encode()
                     for i in range(60)))
        (repo.root / "core.py").write_bytes(
            (repo.root / "core.py").read_bytes()
            + b"\n\ndef orphan():\n    return 2\n")
        found = unreferenced(repo, diff_revisions(repo, "HEAD", None))
        assert any("orphan" in entity_id for entity_id in found)

    def test_the_same_name_is_not_reported_twice(self, repo):
        (repo.root / "core.py").write_bytes(
            (repo.root / "core.py").read_bytes()
            + b"\n\ndef orphan():\n    return 2\n")
        found = unreferenced(repo, diff_revisions(repo, "HEAD", None))
        assert len(found) == len(set(found))


class TestLookupsAreBatched:
    """One process per name was the last O(n) spawn pattern left.

    On a 13-file change `gita diff` spent 0.93s of 2.59s in 13 `git grep`
    processes. git can take every pattern in one call; the counting is ours to
    do afterwards.
    """

    def many(self, repo):
        (repo.root / "core.py").write_bytes(
            b"def used_helper():\n    return 1\n\n\n"
            b"def caller():\n    return used_helper()\n\n\n"
            b"def orphan_one():\n    return 2\n\n\n"
            b"def orphan_two():\n    return 3\n")
        return ["used_helper", "caller", "orphan_one", "orphan_two", "missing_name"]

    def test_batched_counts_match_one_call_per_name(self, repo):
        names = self.many(repo)
        batched = reference_counts(repo, names)
        one_by_one = {n: reference_counts(repo, [n])[n] for n in names}
        assert batched == one_by_one

    def test_all_names_are_answered_in_one_process(self, repo, monkeypatch):
        names = self.many(repo)
        greps = []
        original = type(repo)._run

        def counting(self, *args, **kwargs):
            if args and args[0] == "grep":
                greps.append(args)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(type(repo), "_run", counting)
        reference_counts(repo, names)
        assert len(greps) == 1

    def test_metacharacters_stay_literal_when_batched(self, repo):
        counts = reference_counts(repo, ["a.*b", "used_helper"])
        assert counts["a.*b"] == 0

    def test_short_names_are_still_skipped(self, repo):
        assert reference_counts(repo, ["ab", "used_helper"])["ab"] == 0

    def test_no_names_makes_no_call(self, repo, monkeypatch):
        calls = []
        original = type(repo)._run
        monkeypatch.setattr(type(repo), "_run",
                            lambda self, *a, **k: (calls.append(a), original(self, *a, **k))[1])
        assert reference_counts(repo, []) == {}
        assert not calls
