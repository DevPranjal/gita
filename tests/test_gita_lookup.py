"""Tests for ``gita.lookup`` — the ``gita get <symbol>[@rev]`` substrate.

Covers single-rev resolution (HEAD and pinned), qualified vs bare lookups,
collision handling, and one-hop backward rename walking.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gita import lookup

from conftest import commit_file


# ---------------------------------------------------------------------------
# single-rev resolution
# ---------------------------------------------------------------------------


def test_get_function_at_head(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "src/users.py",
        "def fetch_user(uid):\n    return uid\n",
        "init",
    )
    sym = lookup.get(git_repo, "fetch_user")
    assert sym.name == "fetch_user"
    assert sym.kind == "function"
    assert sym.path == "src/users.py"
    assert sym.line_start == 1
    assert sym.line_end == 2
    assert sym.signature.startswith("def fetch_user")
    assert "return uid" in sym.body
    assert sym.requested_as == "fetch_user"


def test_get_class_at_head(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "u.py",
        "class User:\n    x = 1\n",
        "init",
    )
    sym = lookup.get(git_repo, "User")
    assert sym.kind == "class"
    assert sym.name == "User"


def test_get_method_qualified(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "u.py",
        (
            "def get():\n"
            "    return 0\n"
            "\n"
            "class UserHandler:\n"
            "    def get(self):\n"
            "        return 1\n"
        ),
        "init",
    )
    sym = lookup.get(git_repo, "UserHandler.get")
    assert sym.kind == "method"
    assert sym.name == "get"
    assert sym.parent == "UserHandler"


def test_get_method_bare_when_unique(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "u.py",
        (
            "class User:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
        ),
        "init",
    )
    sym = lookup.get(git_repo, "__init__")
    assert sym.kind == "method"
    assert sym.parent == "User"


def test_get_ambiguous_bare_name_lists_candidates(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "u.py",
        (
            "class User:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "\n"
            "class Admin:\n"
            "    def __init__(self):\n"
            "        pass\n"
        ),
        "init",
    )
    with pytest.raises(lookup.Ambiguous) as exc:
        lookup.get(git_repo, "__init__")
    assert sorted(exc.value.candidates) == ["u.py:Admin.__init__", "u.py:User.__init__"]


def test_get_ambiguous_across_files(git_repo: Path) -> None:
    commit_file(git_repo, "a.py", "def helper():\n    return 1\n", "a")
    commit_file(git_repo, "b.py", "def helper():\n    return 2\n", "b")
    with pytest.raises(lookup.Ambiguous) as exc:
        lookup.get(git_repo, "helper")
    # Cross-file ambiguity surfaces both paths so the human can disambiguate.
    cand = exc.value.candidates
    assert any("a.py" in c for c in cand)
    assert any("b.py" in c for c in cand)


def test_get_unknown_symbol_raises_not_found(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    with pytest.raises(lookup.NotFound) as exc:
        lookup.get(git_repo, "wat")
    assert exc.value.name == "wat"


# ---------------------------------------------------------------------------
# revs
# ---------------------------------------------------------------------------


def test_get_at_rev(git_repo: Path) -> None:
    sha_a = commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "add foo")
    sha_b = commit_file(git_repo, "u.py", "x = 1\n", "delete foo")
    sym = lookup.get(git_repo, "foo", rev=sha_a)
    assert sym.name == "foo"
    assert sym.rev == sha_a
    with pytest.raises(lookup.NotFound):
        lookup.get(git_repo, "foo", rev=sha_b)


def test_get_walks_rename_backward(git_repo: Path) -> None:
    sha_a = commit_file(
        git_repo, "u.py", "def get_user():\n    return 1\n", "add get_user"
    )
    commit_file(git_repo, "u.py", "def fetch_user():\n    return 1\n", "rename")
    sym = lookup.get(git_repo, "fetch_user", rev=sha_a)
    assert sym.name == "get_user"          # found under its old name at rev A
    assert sym.requested_as == "fetch_user"
    assert sym.rev == sha_a


def test_get_does_not_walk_forward_renames(git_repo: Path) -> None:
    commit_file(
        git_repo, "u.py", "def get_user():\n    return 1\n", "add get_user"
    )
    sha_b = commit_file(
        git_repo, "u.py", "def fetch_user():\n    return 1\n", "rename"
    )
    with pytest.raises(lookup.NotFound):
        lookup.get(git_repo, "get_user", rev=sha_b)


def test_get_at_head_does_not_walk_renames(git_repo: Path) -> None:
    """Forward walking from HEAD has no destination — should raise immediately."""
    commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "init")
    with pytest.raises(lookup.NotFound):
        lookup.get(git_repo, "bar")
