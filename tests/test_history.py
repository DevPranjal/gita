"""Tests for Pillar 3: queryable history (gitpp symbol-log / gitpp callers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gitpp.repo import Repo


SCENARIOS = Path(__file__).resolve().parent / "scenarios"


@pytest.fixture
def repo_with_rename(tmp_path: Path) -> Repo:
    """Repo with one commit at base and one commit after applying ours (rename)."""
    repo = Repo.init(tmp_path)
    users = tmp_path / "users.py"
    users.write_text((SCENARIOS / "rename-vs-edit" / "base.py").read_text("utf-8"), "utf-8")
    repo.add(users)
    repo.commit("initial users module")

    users.write_text((SCENARIOS / "rename-vs-edit" / "ours.py").read_text("utf-8"), "utf-8")
    repo.add(users)
    repo.commit("rename get_user to fetch_user")
    return repo


def test_symbol_log_finds_rename_under_either_name(repo_with_rename: Repo) -> None:
    """symbol-log matches both the old and new name of a renamed symbol."""
    by_new = repo_with_rename.symbol_log("fetch_user")
    by_old = repo_with_rename.symbol_log("get_user")

    # fetch_user only appears in the rename commit.
    assert len(by_new) == 1
    assert by_new[0]["message"] == "rename get_user to fetch_user"

    # get_user appears in BOTH the rename (as `from`) and the initial commit
    # (as the added symbol) — that's exactly the history a user wants.
    assert len(by_old) == 2
    assert by_old[0]["sha"] == by_new[0]["sha"]      # newest = rename
    assert by_old[1]["message"] == "initial users module"

    op = by_new[0]["ops"][0]
    assert op["op"] == "rename_symbol"
    assert op["from"] == "get_user"
    assert op["to"] == "fetch_user"


def test_symbol_log_returns_empty_for_unknown_symbol(repo_with_rename: Repo) -> None:
    assert repo_with_rename.symbol_log("not_a_symbol") == []


def test_callers_finds_call_sites_at_head(repo_with_rename: Repo) -> None:
    """At HEAD (post-rename), fetch_user is called from greet/audit/is_known."""
    hits = repo_with_rename.find_callers("fetch_user")

    # All hits in users.py.
    assert {h["file"] for h in hits} == {"users.py"}
    callers = sorted(h["caller"] for h in hits)
    # The base fixture has greet, audit, is_known each calling get_user once.
    assert callers == ["audit", "greet", "is_known"]


def test_callers_at_old_ref_uses_old_name(repo_with_rename: Repo) -> None:
    """At the initial commit, the symbol was still named get_user."""
    log = repo_with_rename.log()
    initial_sha = log[-1]["sha"]  # oldest first-parent
    hits_old = repo_with_rename.find_callers("get_user", ref=initial_sha)
    hits_new = repo_with_rename.find_callers("fetch_user", ref=initial_sha)

    assert len(hits_old) == 3
    assert hits_new == []
