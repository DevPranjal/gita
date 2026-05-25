"""Tests for the git subprocess wrappers."""

from __future__ import annotations

from pathlib import Path

import pytest

from gita import git as gx

from conftest import commit_file


def test_discover_root_finds_enclosing_repo(git_repo: Path, tmp_path: Path) -> None:
    sub = git_repo / "pkg" / "sub"
    sub.mkdir(parents=True)
    assert gx.discover_root(sub) == git_repo


def test_discover_root_raises_outside_repo(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(FileNotFoundError):
        gx.discover_root(outside)


def test_head_sha_none_on_unborn(git_repo: Path) -> None:
    assert gx.head_sha(git_repo) is None


def test_head_sha_after_commit(git_repo: Path) -> None:
    sha = commit_file(git_repo, "a.py", "x = 1\n", "first")
    assert gx.head_sha(git_repo) == sha


def test_log_first_parent_returns_shas_newest_first(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "a.py", "x = 1\n", "one")
    s2 = commit_file(git_repo, "a.py", "x = 2\n", "two")
    s3 = commit_file(git_repo, "a.py", "x = 3\n", "three")
    assert gx.log_shas(git_repo) == [s3, s2, s1]


def test_ls_tree_lists_blobs_with_paths(git_repo: Path) -> None:
    commit_file(git_repo, "src/a.py", "a = 1\n", "a")
    commit_file(git_repo, "src/b.py", "b = 1\n", "b")
    entries = gx.ls_tree(git_repo, "HEAD")
    paths = sorted(e.path for e in entries)
    assert paths == ["src/a.py", "src/b.py"]
    for e in entries:
        assert len(e.blob_sha) == 40


def test_cat_blob_roundtrips_unicode(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "name = 'गीता'\n", "unicode")
    entry = next(e for e in gx.ls_tree(git_repo, "HEAD") if e.path == "u.py")
    assert gx.cat_blob(git_repo, entry.blob_sha) == "name = 'गीता'\n"


def test_status_reports_modified_and_untracked(git_repo: Path) -> None:
    commit_file(git_repo, "a.py", "x = 1\n", "init")
    (git_repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    (git_repo / "b.py").write_text("y = 1\n", encoding="utf-8")
    entries = {e.path: e for e in gx.status(git_repo)}
    assert entries["a.py"].work_status == "M"
    assert entries["b.py"].index_status == "?"


def test_commit_meta_captures_message_and_parents(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "a.py", "x = 1\n", "first commit\n\nbody line")
    s2 = commit_file(git_repo, "a.py", "x = 2\n", "second")
    m1 = gx.commit_meta(git_repo, s1)
    m2 = gx.commit_meta(git_repo, s2)
    assert m1.parents == []
    assert m2.parents == [s1]
    assert m1.message.startswith("first commit")
    assert "body line" in m1.message
    assert m2.author_email == "gita-tests@example.invalid"


def test_rev_parse_resolves_branch_and_sha(git_repo: Path) -> None:
    sha = commit_file(git_repo, "a.py", "x = 1\n", "first")
    assert gx.rev_parse(git_repo, "HEAD") == sha
    assert gx.rev_parse(git_repo, "main") == sha
    assert gx.rev_parse(git_repo, sha) == sha


def test_staged_and_working_text(git_repo: Path) -> None:
    commit_file(git_repo, "a.py", "x = 1\n", "init")
    (git_repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    # Stage one variant, leave another in working tree.
    import subprocess
    subprocess.run(["git", "-C", str(git_repo), "add", "a.py"], check=True)
    (git_repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    assert gx.staged_text(git_repo, "a.py") == "x = 2\n"
    assert gx.working_tree_text(git_repo, "a.py") == "x = 3\n"
