"""Tests for ``gita.context`` — composite read over symbol/callers/log/proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gita import context, lookup, proofs

from conftest import commit_file


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _write_proof_file(root: Path, sha: str, checks: dict) -> None:
    pdir = root / ".git" / "gita" / "proofs"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{sha}.json").write_text(
        json.dumps({"commit": sha, "checks": checks}, indent=2),
        encoding="utf-8",
    )


def _ok_check() -> dict:
    return {
        "ok": True, "exit_code": 0, "duration_ms": 1,
        "ran_at": "2026-01-01T00:00:00Z", "cmd": ["true"],
        "stdout_head": "", "stdout_tail": "", "truncated": False,
    }


@pytest.fixture
def repo_with_symbol(git_repo: Path) -> dict[str, str]:
    """A repo with `fetch_user` defined, called from another module, with a
    proof on the latest commit and at least two commits touching the symbol."""
    s1 = commit_file(
        git_repo, "users.py",
        "def fetch_user(uid):\n    return {'id': uid}\n",
        "add fetch_user",
    )
    s2 = commit_file(
        git_repo, "api.py",
        "from users import fetch_user\n\ndef route(u):\n    return fetch_user(u)\n",
        "call fetch_user from api",
    )
    s3 = commit_file(
        git_repo, "users.py",
        "def fetch_user(uid):\n    row = {'id': uid}\n    return row\n",
        "tweak fetch_user body",
    )
    _write_proof_file(git_repo, s3, {"pytest": _ok_check()})
    return {"s1": s1, "s2": s2, "s3": s3}


# ---------------------------------------------------------------------------
# build() — happy paths
# ---------------------------------------------------------------------------


def test_context_returns_symbol_callers_log_proven(
    git_repo: Path, repo_with_symbol
) -> None:
    rec = context.build(git_repo, "fetch_user")
    assert rec.symbol.name == "fetch_user"
    assert rec.symbol.signature.startswith("def fetch_user(")
    assert any(c["caller"] == "route" for c in rec.callers)
    assert len(rec.log) >= 2
    assert rec.last_proven == repo_with_symbol["s3"]


def test_context_symbol_not_found_raises(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    with pytest.raises(lookup.NotFound):
        context.build(git_repo, "nonexistent_symbol")


def test_context_no_callers_returns_empty_list(git_repo: Path) -> None:
    commit_file(git_repo, "lone.py", "def solo():\n    return 1\n", "add solo")
    rec = context.build(git_repo, "solo")
    assert rec.callers == []


def test_context_no_proofs_returns_none_for_last_proven(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "def f():\n    return 1\n", "add f")
    rec = context.build(git_repo, "f")
    # No proofs recorded — context is best-effort, swallows NoProofs.
    assert rec.last_proven is None


def test_context_history_limit_default(git_repo: Path) -> None:
    # 12 commits touching the same symbol; default log_limit is 10.
    for i in range(12):
        commit_file(
            git_repo, "m.py",
            f"def f():\n    return {i}\n",
            f"bump f to {i}",
        )
    rec = context.build(git_repo, "f")
    assert len(rec.log) == 10


def test_context_log_limit_explicit(git_repo: Path) -> None:
    for i in range(5):
        commit_file(
            git_repo, "m.py",
            f"def f():\n    return {i}\n",
            f"bump f to {i}",
        )
    rec = context.build(git_repo, "f", log_limit=2)
    assert len(rec.log) == 2


def test_context_at_rev(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    commit_file(git_repo, "m.py", "def f():\n    return 2\n", "bump")
    rec = context.build(git_repo, "f", rev=s1)
    assert rec.symbol.body.strip().endswith("return 1")


# ---------------------------------------------------------------------------
# _fit() — budget reduction
# ---------------------------------------------------------------------------


def test_context_budget_none_keeps_everything(
    git_repo: Path, repo_with_symbol
) -> None:
    rec = context.build(git_repo, "fetch_user", budget=None)
    assert rec.dropped == []


def test_context_budget_drops_oldest_log_first(git_repo: Path) -> None:
    for i in range(6):
        commit_file(
            git_repo, "m.py",
            f"def f():\n    return {i}\n",
            f"bump f to {i}",
        )
    # Pick a budget that's smaller than full record but larger than minimal.
    full = context.build(git_repo, "f")
    full_size = len(json.dumps(full.to_dict()))
    rec = context.build(git_repo, "f", budget=full_size // 2)
    # Signature + body always survive.
    assert rec.symbol.signature.startswith("def f(")
    assert rec.symbol.body  # non-empty
    # Some log entries got dropped; survivors are the most recent.
    assert len(rec.log) < len(full.log)
    assert "log" in rec.dropped or len(rec.log) < len(full.log)


def test_context_budget_drops_callers_after_log_exhausted(
    git_repo: Path, repo_with_symbol
) -> None:
    full = context.build(git_repo, "fetch_user")
    # Budget large enough only for signature + body.
    body_only = len(full.symbol.signature) + len(full.symbol.body) + 200
    rec = context.build(git_repo, "fetch_user", budget=body_only)
    assert rec.callers == []
    assert "callers" in rec.dropped


def test_context_budget_zero_keeps_signature(
    git_repo: Path, repo_with_symbol
) -> None:
    rec = context.build(git_repo, "fetch_user", budget=0)
    # Degenerate case: signature always survives.
    assert rec.symbol.signature.startswith("def fetch_user(")
    # Body may be dropped under brutal budgets.
    assert "body" in rec.dropped or rec.symbol.body == ""


def test_context_dropped_is_stable_list(
    git_repo: Path, repo_with_symbol
) -> None:
    rec = context.build(git_repo, "fetch_user", budget=0)
    # `dropped` is a list of section names, never None.
    assert isinstance(rec.dropped, list)
    for s in rec.dropped:
        assert s in {"log", "callers", "last_proven", "body"}


# ---------------------------------------------------------------------------
# to_dict / serialization shape
# ---------------------------------------------------------------------------


def test_context_to_dict_has_stable_keys(
    git_repo: Path, repo_with_symbol
) -> None:
    rec = context.build(git_repo, "fetch_user")
    d = rec.to_dict()
    assert set(d.keys()) >= {
        "symbol", "callers", "log", "last_proven", "rev", "dropped",
    }
    assert isinstance(d["callers"], list)
    assert isinstance(d["log"], list)
