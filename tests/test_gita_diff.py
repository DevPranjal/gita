"""Diff-layer tests for phase 1 — symbol filtering and non-Python files.

The CLI surface for these features is exercised in
``tests/test_gita_cli.py``; this file pins the underlying manifest shape so
the rendering / MCP layers can rely on it directly.
"""

from __future__ import annotations

from pathlib import Path

from gita.cli import _filter_manifest
from gita.diff import build_for_refs, build_for_working_tree

from conftest import commit_file


# ---------------------------------------------------------------------------
# --symbol filter
# ---------------------------------------------------------------------------


def _manifest_with_two_symbols(git_repo: Path) -> dict:
    s1 = commit_file(
        git_repo,
        "m.py",
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n",
        "init",
    )
    s2 = commit_file(
        git_repo,
        "m.py",
        "def foo():\n    return 11\n\ndef bar():\n    return 22\n",
        "edit both",
    )
    return build_for_refs(git_repo, s1, s2)


def test_diff_filters_to_one_symbol(git_repo: Path) -> None:
    manifest = _manifest_with_two_symbols(git_repo)
    filtered = _filter_manifest(manifest, only=None, exclude=None, symbol="foo")
    names_seen: set[str] = set()
    for fe in filtered["files"]:
        for op in fe["ops"]:
            if "name" in op:
                names_seen.add(op["name"])
    assert names_seen == {"foo"}


def test_diff_filter_drops_empty_file_entries(git_repo: Path) -> None:
    """Files whose ops all get filtered out vanish from the result."""
    s1 = commit_file(
        git_repo, "a.py", "def foo():\n    return 1\n", "init a"
    )
    commit_file(
        git_repo, "b.py", "def bar():\n    return 2\n", "init b"
    )
    s3 = commit_file(
        git_repo,
        "b.py",
        "def bar():\n    return 22\n",
        "edit b",
    )
    manifest = build_for_refs(git_repo, s1, s3)
    filtered = _filter_manifest(manifest, only=None, exclude=None, symbol="foo")
    # b.py has no `foo` ops → dropped entirely.
    paths = {fe["path"] for fe in filtered["files"]}
    assert "b.py" not in paths


def test_diff_filter_keeps_added_and_deleted_files_if_symbol_matches(
    git_repo: Path,
) -> None:
    s1 = commit_file(git_repo, "seed.py", "x = 1\n", "seed")
    s2 = commit_file(
        git_repo,
        "u.py",
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n",
        "add file with foo and bar",
    )
    manifest = build_for_refs(git_repo, s1, s2)

    keep_foo = _filter_manifest(manifest, only=None, exclude=None, symbol="foo")
    paths = {fe["path"] for fe in keep_foo["files"]}
    assert "u.py" in paths
    op_names = [op.get("name") for fe in keep_foo["files"] for op in fe["ops"]]
    assert "foo" in op_names and "bar" not in op_names

    drop_other = _filter_manifest(manifest, only=None, exclude=None, symbol="nope")
    assert all(fe["path"] != "u.py" for fe in drop_other["files"])


def test_diff_filter_keeps_rename_when_either_side_matches(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "u.py", "def old_name():\n    return 1\n", "init")
    s2 = commit_file(git_repo, "u.py", "def new_name():\n    return 1\n", "rename")
    manifest = build_for_refs(git_repo, s1, s2)

    by_from = _filter_manifest(manifest, only=None, exclude=None, symbol="old_name")
    by_to = _filter_manifest(manifest, only=None, exclude=None, symbol="new_name")
    for filtered in (by_from, by_to):
        assert any(
            op["op"] == "rename_symbol"
            for fe in filtered["files"]
            for op in fe["ops"]
        )


# ---------------------------------------------------------------------------
# parseable: false for non-Python files
# ---------------------------------------------------------------------------


def test_nonpy_file_marked_parseable_false(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "config.yaml", "name: alpha\nport: 80\n", "init")
    s2 = commit_file(git_repo, "config.yaml", "name: alpha\nport: 8080\n", "edit")
    manifest = build_for_refs(git_repo, s1, s2)
    entries = [fe for fe in manifest["files"] if fe["path"] == "config.yaml"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["parseable"] is False
    assert "textual_diff" in entry and entry["textual_diff"]
    assert "8080" in entry["textual_diff"]
    assert "ops" not in entry


def test_nonpy_file_unchanged_is_omitted(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "config.yaml", "name: alpha\n", "init")
    # Touch an unrelated python file so commit-to-commit actually exists.
    s2 = commit_file(git_repo, "m.py", "x = 1\n", "add m")
    manifest = build_for_refs(git_repo, s1, s2)
    assert all(fe["path"] != "config.yaml" for fe in manifest["files"])


def test_nonpy_added_file_carries_textual_diff(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "x = 1\n", "seed")
    s2 = commit_file(git_repo, "notes.md", "hello\n", "add notes")
    manifest = build_for_refs(git_repo, s1, s2)
    entry = next(fe for fe in manifest["files"] if fe["path"] == "notes.md")
    assert entry["status"] == "added"
    assert entry["parseable"] is False
    assert "hello" in entry["textual_diff"]


def test_symbol_filter_drops_nonpy_entries(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "def foo():\n    return 1\n", "init")
    s2 = commit_file(git_repo, "config.yaml", "port: 80\n", "add yaml")
    manifest = build_for_refs(git_repo, s1, s2)
    filtered = _filter_manifest(manifest, only=None, exclude=None, symbol="foo")
    assert all(fe["path"] != "config.yaml" for fe in filtered["files"])


def test_manifest_schema_bumped_to_2(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "x = 1\n", "one")
    s2 = commit_file(git_repo, "m.py", "x = 2\n", "two")
    manifest = build_for_refs(git_repo, s1, s2)
    assert manifest["schema"] == 2


def test_working_tree_collects_nonpy(git_repo: Path) -> None:
    commit_file(git_repo, "config.yaml", "port: 80\n", "init")
    (git_repo / "config.yaml").write_text("port: 8080\n", encoding="utf-8")
    manifest = build_for_working_tree(git_repo)
    entry = next(fe for fe in manifest["files"] if fe["path"] == "config.yaml")
    assert entry["parseable"] is False
    assert "8080" in entry["textual_diff"]
