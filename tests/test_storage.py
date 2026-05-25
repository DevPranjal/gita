"""End-to-end storage + merge test.

Drives the public CLI surface through :class:`Repo` rather than subprocess,
so failures land in a readable stack trace. The flow models a tiny "branch"
without actually adding branch primitives in v0.0 — we hand-roll the second
line of history by rewinding the branch ref to the base commit before
committing ``theirs``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gitpp.repo import Repo

SCENARIO = Path(__file__).parent / "scenarios" / "parallel-methods"


def _layout(tmp_path: Path, source: str) -> Path:
    """Write ``inventory.py`` at the repo root with the given source."""
    p = tmp_path / "inventory.py"
    p.write_text(source, encoding="utf-8")
    return p


def test_init_add_commit_log(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    assert (tmp_path / ".gitpp" / "objects").is_dir()
    assert repo.current_branch() == "main"
    assert repo.head_commit() is None
    assert repo.log() == []

    _layout(tmp_path, (SCENARIO / "base.py").read_text(encoding="utf-8"))
    file_sha = repo.add(tmp_path / "inventory.py")
    assert len(file_sha) == 64

    commit_sha = repo.commit("base")
    log = repo.log()
    assert len(log) == 1
    assert log[0]["sha"] == commit_sha
    assert log[0]["message"] == "base"
    assert log[0]["parents"] == []


def test_add_rejects_unparseable(tmp_path: Path) -> None:
    Repo.init(tmp_path)
    repo = Repo.discover(tmp_path)
    bad = tmp_path / "bad.py"
    bad.write_text("def (oops:\n", encoding="utf-8")
    with pytest.raises(Exception):
        repo.add(bad)


def test_objects_are_content_addressed(tmp_path: Path) -> None:
    """Same source -> same sha; both commits' tree dedups the file object."""
    repo = Repo.init(tmp_path)
    src = (SCENARIO / "base.py").read_text(encoding="utf-8")
    _layout(tmp_path, src)

    sha1 = repo.add(tmp_path / "inventory.py")
    repo.commit("first")
    sha2 = repo.add(tmp_path / "inventory.py")
    assert sha1 == sha2
    # And only one object file on disk for that content.
    obj_path = tmp_path / ".gitpp" / "objects" / sha1[:2] / sha1[2:]
    assert obj_path.exists()


def test_three_way_merge_via_repo(tmp_path: Path) -> None:
    """The full parallel-methods scenario, but driven through the storage layer.

    Sequence:
      1. init + commit base on `main`
      2. commit ours on `main`  (HEAD)
      3. rewind `main` to base, commit theirs as `feature`
      4. switch HEAD back to ours, merge feature -> verify expected output
    """
    repo = Repo.init(tmp_path)
    base_src = (SCENARIO / "base.py").read_text(encoding="utf-8")
    ours_src = (SCENARIO / "ours.py").read_text(encoding="utf-8")
    theirs_src = (SCENARIO / "theirs.py").read_text(encoding="utf-8")
    expected_src = (SCENARIO / "expected.py").read_text(encoding="utf-8")

    # 1. base commit
    _layout(tmp_path, base_src)
    repo.add(tmp_path / "inventory.py")
    base_commit = repo.commit("base")

    # 2. ours commit on main
    _layout(tmp_path, ours_src)
    repo.add(tmp_path / "inventory.py")
    ours_commit = repo.commit("add remove()")

    # 3. fork: write a `feature` branch ref pointing at base, point HEAD at it,
    # commit theirs there.
    feature_ref = tmp_path / ".gitpp" / "refs" / "heads" / "feature"
    feature_ref.write_text(base_commit + "\n", encoding="utf-8")
    (tmp_path / ".gitpp" / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    # Index needs to reflect the base tree so commit produces a tree-on-top-of-base.
    repo._checkout_tree(repo._tree_of(base_commit))
    _layout(tmp_path, theirs_src)
    repo.add(tmp_path / "inventory.py")
    theirs_commit = repo.commit("add count()")
    assert theirs_commit != ours_commit

    # 4. switch back to main, restore ours working tree, merge feature.
    (tmp_path / ".gitpp" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    repo._checkout_tree(repo._tree_of(ours_commit))

    base_lca = repo.merge_base(ours_commit, theirs_commit)
    assert base_lca == base_commit

    result = repo.merge("feature", message="merge feature")
    assert result.status == "merged", result.conflicts
    assert result.commit is not None

    # Working tree should now match expected.
    on_disk = (tmp_path / "inventory.py").read_text(encoding="utf-8")
    assert on_disk == expected_src

    # The merge commit has two parents.
    merge_obj = repo.log()[0]
    assert merge_obj["sha"] == result.commit
    assert set(merge_obj["parents"]) == {ours_commit, theirs_commit}


def test_fast_forward(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    base_src = (SCENARIO / "base.py").read_text(encoding="utf-8")
    ours_src = (SCENARIO / "ours.py").read_text(encoding="utf-8")

    _layout(tmp_path, base_src)
    repo.add(tmp_path / "inventory.py")
    base_commit = repo.commit("base")

    # feature is ahead of main; main should fast-forward.
    feature_ref = tmp_path / ".gitpp" / "refs" / "heads" / "feature"
    feature_ref.write_text(base_commit + "\n", encoding="utf-8")
    (tmp_path / ".gitpp" / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    _layout(tmp_path, ours_src)
    repo.add(tmp_path / "inventory.py")
    feature_commit = repo.commit("add remove()")

    # Back to main (still at base) and merge feature.
    (tmp_path / ".gitpp" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    repo._checkout_tree(repo._tree_of(base_commit))

    result = repo.merge("feature")
    assert result.status == "fast-forward"
    assert result.commit == feature_commit
    # main now points at feature_commit
    assert repo.head_commit() == feature_commit
