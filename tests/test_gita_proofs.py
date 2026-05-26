"""Tests for ``gita.proofs`` — the ``prove`` / ``last-proven`` pillar.

Proofs are commit-keyed records of named checks (pytest, mypy, etc.). The
storage lives at ``.git/gita/proofs/<sha>.json`` and is queried by
``last_proven`` to answer "what's the last commit where this check passed?"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from gita import git as gx
from gita import proofs

from conftest import commit_file


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def _python() -> str:
    return sys.executable


def test_prove_records_success(git_repo: Path) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    result = proofs.record(git_repo, "pytest", cmd=[_python(), "-c", "import sys; sys.exit(0)"])
    assert result.ok is True
    assert result.exit_code == 0

    payload = json.loads((git_repo / ".git" / "gita" / "proofs" / f"{sha}.json").read_text())
    assert payload["commit"] == sha
    assert payload["checks"]["pytest"]["ok"] is True
    assert payload["checks"]["pytest"]["exit_code"] == 0
    assert payload["checks"]["pytest"]["cmd"] == [_python(), "-c", "import sys; sys.exit(0)"]


def test_prove_records_failure(git_repo: Path) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    result = proofs.record(git_repo, "pytest", cmd=[_python(), "-c", "import sys; sys.exit(1)"])
    assert result.ok is False
    assert result.exit_code == 1

    stored = proofs.read(git_repo, sha)
    assert stored is not None
    assert stored["checks"]["pytest"]["ok"] is False
    assert stored["checks"]["pytest"]["exit_code"] == 1


def test_prove_truncates_output(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    # ~100 KB of stdout
    script = "print('x' * 100_000)"
    proofs.record(git_repo, "noisy", cmd=[_python(), "-c", script])
    sha = gx.rev_parse(git_repo, "HEAD")
    stored = proofs.read(git_repo, sha)
    check = stored["checks"]["noisy"]
    assert check["truncated"] is True
    assert len(check["stdout_head"]) <= 4096
    assert len(check["stdout_tail"]) <= 4096


def test_prove_merges_into_existing_proofs(git_repo: Path) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    proofs.record(git_repo, "pytest", cmd=[_python(), "-c", "pass"])
    proofs.record(git_repo, "mypy", cmd=[_python(), "-c", "pass"])
    stored = proofs.read(git_repo, sha)
    assert set(stored["checks"]) == {"pytest", "mypy"}


def test_prove_overwrites_same_named_check(git_repo: Path) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    proofs.record(git_repo, "pytest", cmd=[_python(), "-c", "import sys; sys.exit(1)"])
    proofs.record(git_repo, "pytest", cmd=[_python(), "-c", "pass"])
    stored = proofs.read(git_repo, sha)
    # Only the latest result is retained — proofs are "latest known", not a log.
    assert stored["checks"]["pytest"]["ok"] is True
    assert stored["checks"]["pytest"]["exit_code"] == 0


def test_prove_refuses_dirty_tree(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    (git_repo / "u.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(proofs.DirtyTree):
        proofs.record(git_repo, "pytest", cmd=[_python(), "-c", "pass"])
    sha = gx.rev_parse(git_repo, "HEAD")
    assert proofs.read(git_repo, sha) is None


# ---------------------------------------------------------------------------
# last_proven
# ---------------------------------------------------------------------------


def test_last_proven_returns_latest_green(git_repo: Path) -> None:
    sha_a = commit_file(git_repo, "u.py", "x = 1\n", "a")
    sha_b = commit_file(git_repo, "u.py", "x = 2\n", "b")
    sha_c = commit_file(git_repo, "u.py", "x = 3\n", "c")
    _write_proof(git_repo, sha_a, {"pytest": _ok()})
    _write_proof(git_repo, sha_b, {"pytest": _ok()})
    _write_proof(git_repo, sha_c, {"pytest": _fail()})
    # Walk is newest-first; first ok pytest is sha_b.
    assert proofs.last_proven(git_repo, "pytest") == sha_b


def test_last_proven_unfiltered_requires_all_recorded_checks_ok(
    git_repo: Path,
) -> None:
    sha_a = commit_file(git_repo, "u.py", "x = 1\n", "a")
    sha_b = commit_file(git_repo, "u.py", "x = 2\n", "b")
    _write_proof(git_repo, sha_a, {"pytest": _ok(), "mypy": _fail()})
    _write_proof(git_repo, sha_b, {"pytest": _ok()})
    # A has a failing recorded check; B's only recorded check passes.
    assert proofs.last_proven(git_repo) == sha_b


def test_last_proven_with_symbol_skips_commits_where_symbol_absent(
    git_repo: Path,
) -> None:
    sha_a = commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "a")
    sha_b = commit_file(git_repo, "u.py", "x = 1\n", "b: drop foo")
    sha_c = commit_file(git_repo, "u.py", "def foo():\n    return 2\n", "c: re-add")
    _write_proof(git_repo, sha_a, {"pytest": _ok()})
    _write_proof(git_repo, sha_b, {"pytest": _ok()})
    _write_proof(git_repo, sha_c, {"pytest": _ok()})
    assert proofs.last_proven(git_repo, "pytest", symbol="foo") == sha_c


def test_last_proven_with_no_recorded_proofs_raises(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    with pytest.raises(proofs.NoProofs):
        proofs.last_proven(git_repo, "pytest")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ok() -> dict:
    return {
        "ok": True,
        "exit_code": 0,
        "duration_ms": 1,
        "ran_at": "2026-01-01T00:00:00Z",
        "cmd": ["true"],
        "stdout_head": "",
        "stdout_tail": "",
        "truncated": False,
    }


def _fail() -> dict:
    return {**_ok(), "ok": False, "exit_code": 1}


def _write_proof(root: Path, sha: str, checks: dict) -> None:
    pdir = root / ".git" / "gita" / "proofs"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{sha}.json").write_text(
        json.dumps({"commit": sha, "checks": checks}, indent=2),
        encoding="utf-8",
    )
