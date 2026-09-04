"""WS-1: history walks must cost one git process, not one per merge.

`Repo.walk` asks git for every commit and its files in a single
`git log --name-status`, then throws most of that away: git prints no file list
for a merge, so each merge falls back to an explicit `git diff`. That fallback is
not the exception. Measured on flask, `series(limit=100)` spawned 42 git
processes, 41 of them the fallback.

`git log --diff-merges=first-parent` lists a merge's files against its first
parent, which removes the fallback entirely.

These tests assert on process counts because WS-1 changes cost and not output.
A test that only checked the answer would pass before and after.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gita.history import series
from gita.vcs.git import Repo


def _merge_shas(repo: Repo) -> list[str]:
    return repo.text("rev-list", "--merges", "HEAD").split()


# --- cycle 1: the measuring instrument ------------------------------------

def test_git_calls_fixture_records_argv(repo, git_calls):
    """A miscounting fixture would make every later assertion meaningless."""
    repo.changed_files("HEAD^", "HEAD")

    assert git_calls.of("diff") == 1
    assert git_calls.of("log") == 0
    assert git_calls.argv[0][0] == "diff"


def test_git_calls_fixture_counts_each_call(repo, git_calls):
    repo.changed_files("HEAD^", "HEAD")
    repo.changed_files("HEAD^", "HEAD")

    assert git_calls.of("diff") == 2


# --- cycle 2: merges enumerate without a fallback -------------------------

def test_merge_repo_is_actually_merge_heavy(merge_repo):
    """Guards the fixture: a linear history would make the rest vacuous."""
    assert len(_merge_shas(merge_repo)) == 3


def test_walk_enumerates_merges_in_one_call(merge_repo, git_calls):
    records = merge_repo.walk(limit=20)

    merges = [r for r in records if r.is_merge]
    assert len(merges) == 3
    assert all(r.files for r in merges), "a merge with no files means no diff was seen"
    assert git_calls.of("log") == 1
    assert git_calls.of("diff") == 0


def test_walk_costs_one_process_regardless_of_merge_count(merge_repo, git_calls):
    merge_repo.walk(limit=20)

    assert len(git_calls.argv) == 1


# --- cycle 3: the flag must not change the answer -------------------------

def test_merge_files_match_first_parent_diff(merge_repo):
    """`--diff-merges=first-parent` must equal the diff it replaces.

    Starts green, via the fallback, and must stay green once the fallback is
    gone. Its value is as a regression guard on a substitution.
    """
    records = {r.sha: r for r in merge_repo.walk(limit=20)}

    for sha in _merge_shas(merge_repo):
        record = records[sha]
        expected = merge_repo.changed_files(record.parent, sha, supported_only=False)
        assert {f.path for f in record.files} == {f.path for f in expected}
        assert {(f.status, f.path) for f in record.files} \
            == {(f.status, f.path) for f in expected}


def test_non_merge_files_unchanged(merge_repo):
    records = [r for r in merge_repo.walk(limit=20) if not r.is_merge]

    for record in records:
        expected = merge_repo.changed_files(record.parent, record.sha,
                                            supported_only=False)
        assert {f.path for f in record.files} == {f.path for f in expected}


# --- cycle 4: old git still works -----------------------------------------

def test_falls_back_when_diff_merges_unsupported(merge_repo, git_calls, monkeypatch):
    """`--diff-merges` landed in git 2.31. The fallback stays, and stays tested."""
    monkeypatch.setattr(type(merge_repo), "supports_diff_merges",
                        property(lambda self: False))

    records = merge_repo.walk(limit=20)

    merges = [r for r in records if r.is_merge]
    assert all(r.files for r in merges)
    assert git_calls.of("diff") == len(merges)


def test_fallback_produces_the_same_files(merge_repo, monkeypatch):
    modern = {r.sha: {f.path for f in r.files} for r in merge_repo.walk(limit=20)}

    monkeypatch.setattr(type(merge_repo), "supports_diff_merges",
                        property(lambda self: False))
    legacy = {r.sha: {f.path for f in r.files} for r in merge_repo.walk(limit=20)}

    assert modern == legacy


# --- cycle 5: capability detection ----------------------------------------

def test_supports_diff_merges_is_detected(repo):
    assert isinstance(repo.supports_diff_merges, bool)


def test_supports_diff_merges_costs_no_process_per_walk(merge_repo, git_calls):
    """Probing per walk would reintroduce the process this workstream removes."""
    merge_repo.supports_diff_merges
    git_calls.clear()

    merge_repo.walk(limit=20)
    merge_repo.walk(limit=20)

    assert git_calls.of("version") == 0
    assert len(git_calls.argv) == 2


@pytest.mark.parametrize("version,expected", [
    ("git version 2.55.0.windows.4", True),
    ("git version 2.31.0", True),
    ("git version 2.30.9", False),
    ("git version 1.9.1", False),
    ("git version 3.0.0", True),
    ("nonsense", False),
])
def test_diff_merges_version_threshold(version, expected):
    from gita.vcs.git import _supports_diff_merges

    assert _supports_diff_merges(version) is expected


# --- cycle 6: history reuses the enumeration ------------------------------

def test_series_costs_one_enumeration_call(merge_repo, git_calls):
    series(merge_repo, limit=20)

    assert git_calls.of("log") == 1
    assert git_calls.of("diff") == 0


def test_entity_history_spawns_no_diff(merge_repo, git_calls):
    from gita.history import entity_history

    entity_history(merge_repo, "handle", limit=20)

    assert git_calls.of("diff") == 0


# --- cycle 8: no regression in what users see -----------------------------

def test_series_batched_matches_unbatched(merge_repo):
    """The unbatched path asks git per commit. Both must agree, exactly."""
    batched = series(merge_repo, limit=20)
    unbatched = series(merge_repo, limit=20, batched=False)

    assert [s.sha for s in batched] == [s.sha for s in unbatched]
    for fast, slow in zip(batched, unbatched):
        assert [str(c) for c in fast.changes] == [str(c) for c in slow.changes]


def test_entity_history_unchanged_on_plain_repo(repo):
    from gita.history import entity_history

    events = entity_history(repo, "handle", limit=10)

    assert [e.kind.value for e in events] == ["signature_changed", "added"]
    assert {e.entity_id for e in events} == {"app.py::handle"}


# --- cycle 7: equivalence on real history ---------------------------------

CORPUS = Path("spikes/attribution/corpus")


@pytest.mark.slow
@pytest.mark.skipif(not CORPUS.exists(), reason="corpus not checked out")
@pytest.mark.parametrize("name", ["flask", "express", "gin", "got", "ripgrep"])
def test_walk_equals_per_commit_on_corpus(name):
    """Real history: renames, mode changes, binary files, merges of merges."""
    root = CORPUS / name
    if not root.exists():
        pytest.skip(f"{name} not present")
    repo = Repo(root)

    for record in repo.walk(limit=40):
        expected = repo.changed_files(record.parent, record.sha,
                                      supported_only=False)
        assert {(f.status, f.path) for f in record.files} \
            == {(f.status, f.path) for f in expected}, record.sha


def test_git_version_string_is_parseable():
    raw = subprocess.run(["git", "--version"], capture_output=True,
                         text=True).stdout
    from gita.vcs.git import _supports_diff_merges

    assert _supports_diff_merges(raw) is True, f"unexpected git version: {raw!r}"


def test_git_version_probed_once_per_process():
    """One subprocess per run, not one per Repo -- the binary cannot change."""
    from gita.vcs.git import _git_version

    _git_version.cache_clear()
    _git_version()
    _git_version()

    assert _git_version.cache_info().misses == 1
    assert _git_version.cache_info().hits == 1


def test_missing_git_binary_degrades_to_fallback(monkeypatch):
    """No git on PATH must not raise out of a capability probe."""
    from gita.vcs import git as git_module

    git_module._git_version.cache_clear()
    monkeypatch.setattr(git_module.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    try:
        assert git_module._git_version() == ""
        assert git_module._supports_diff_merges("") is False
    finally:
        git_module._git_version.cache_clear()


# --- production edge cases ------------------------------------------------

def test_octopus_merge_files_match_first_parent(tmp_path):
    """A merge may have more than two parents; we diff against the first."""

    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "base.py").write_text("def base():\n    return 0\n")
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("add", "-A")
    run("commit", "-q", "-m", "base")

    for n in (1, 2):
        run("checkout", "-q", "-b", f"side-{n}", "main")
        (tmp_path / f"side{n}.py").write_text(f"def side_{n}():\n    return {n}\n")
        run("add", "-A")
        run("commit", "-q", "-m", f"side {n}")
    run("checkout", "-q", "main")
    run("merge", "-q", "--no-ff", "-m", "octopus", "side-1", "side-2")

    repo = Repo(tmp_path)
    head = repo.text("rev-parse", "HEAD").strip()
    parents = repo.text("rev-list", "--parents", "-n1", head).split()
    assert len(parents) == 4, "expected a three-parent merge"

    record = next(r for r in repo.walk(limit=10) if r.sha == head)
    expected = repo.changed_files(parents[1], head, supported_only=False)
    assert {(f.status, f.path) for f in record.files} \
        == {(f.status, f.path) for f in expected}


def test_walk_on_repository_without_commits(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True,
                   capture_output=True)

    assert Repo(tmp_path).walk(limit=10) == []


def test_first_parent_skips_side_branch_commits(merge_repo):
    """WS-3 walks first-parent only; the flag has to actually restrict."""
    everything = {r.sha for r in merge_repo.walk(limit=50)}
    mainline = {r.sha for r in merge_repo.walk(limit=50, first_parent=True)}

    assert mainline < everything
    assert len(everything) - len(mainline) == 3


def test_first_parent_still_carries_merge_files(merge_repo):
    records = merge_repo.walk(limit=50, first_parent=True)

    assert all(r.files for r in records if r.is_merge)
