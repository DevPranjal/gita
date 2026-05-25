"""Tests for the diff / store / history layers against real git repos."""

from __future__ import annotations

from pathlib import Path

from gita import store
from gita.diff import build_for_commit, build_for_refs, build_for_working_tree
from gita.history import manifest_for, reindex, symbol_log

from conftest import commit_file


def test_build_for_commit_initial_commit_adds_all_symbols(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    manifest = build_for_commit(git_repo, sha)
    kinds = {op["op"] for fe in manifest["files"] for op in fe["ops"]}
    assert kinds == {"add_symbol"}
    assert manifest["summary"]["logic_ops"] == 1


def test_build_for_refs_between_two_commits(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "first")
    s2 = commit_file(git_repo, "m.py", "def f():\n    return 2\n", "second")
    manifest = build_for_refs(git_repo, s1, s2)
    assert manifest["from"] == s1
    assert manifest["to"] == s2
    ops = [op for fe in manifest["files"] for op in fe["ops"]]
    assert any(op["op"] == "modify_body" and op["name"] == "f" for op in ops)


def test_build_for_working_tree_against_head(git_repo: Path) -> None:
    commit_file(git_repo, "m.py", "def f():\n    return 1\n", "first")
    (git_repo / "m.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    manifest = build_for_working_tree(git_repo)
    ops = [op for fe in manifest["files"] for op in fe["ops"]]
    assert any(op["op"] == "modify_body" for op in ops)
    assert manifest["to"] is None


def test_store_roundtrip(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "first")
    store.init(git_repo)
    manifest = build_for_commit(git_repo, sha)
    store.write(git_repo, sha, manifest)
    assert store.has(git_repo, sha)
    assert store.read(git_repo, sha) == manifest
    assert sha in store.list_commits_with_manifests(git_repo)


def test_manifest_for_caches_when_initialized(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "first")
    store.init(git_repo)
    assert not store.has(git_repo, sha)
    manifest_for(git_repo, sha)
    assert store.has(git_repo, sha)


def test_symbol_log_finds_rename_under_both_names(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "u.py", "def get_user(uid):\n    return uid\n", "init")
    s2 = commit_file(git_repo, "u.py", "def fetch_user(uid):\n    return uid\n", "rename")
    by_new = symbol_log(git_repo, "fetch_user")
    by_old = symbol_log(git_repo, "get_user")
    assert [e["sha"] for e in by_new] == [s2]
    assert [e["sha"] for e in by_old] == [s2, s1]
    rename_op = by_new[0]["ops"][0]
    assert rename_op["op"] == "rename_symbol"
    assert rename_op["from"] == "get_user"
    assert rename_op["to"] == "fetch_user"


def test_symbol_log_works_without_init(git_repo: Path) -> None:
    """Even without ``gita init`` we still answer queries (just don't cache)."""
    commit_file(git_repo, "u.py", "def alpha():\n    return 1\n", "init")
    commit_file(git_repo, "u.py", "def alpha():\n    return 2\n", "edit")
    entries = symbol_log(git_repo, "alpha")
    assert len(entries) == 2
    assert not store.is_initialized(git_repo)


def test_reindex_computes_all_commits(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "x = 1\n", "one")
    s2 = commit_file(git_repo, "m.py", "x = 2\n", "two")
    result = reindex(git_repo)
    assert result["computed"] == 2
    assert result["skipped"] == 0
    assert store.has(git_repo, s1) and store.has(git_repo, s2)
    # Idempotent on a second run.
    result2 = reindex(git_repo)
    assert result2["computed"] == 0
    assert result2["skipped"] == 2
