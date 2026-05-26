"""Tests for ``gita bisect-proven`` (phase 2 of v0.3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gita import bisect as bisect_mod
from gita import git as gx
from gita import proofs as proofs_mod

from conftest import commit_file


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_proof(root: Path, sha: str, name: str, ok: bool) -> None:
    """Directly write a cached proof file (bypasses subprocess)."""
    p = proofs_mod.proof_path(root, sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({
            "commit": sha,
            "checks": {
                name: {
                    "ok": ok,
                    "exit_code": 0 if ok else 1,
                    "duration_ms": 1,
                    "ran_at": "2026-01-01T00:00:00Z",
                    "cmd": ["fake"],
                    "stdout_head": "",
                    "stdout_tail": "",
                    "truncated": False,
                }
            },
        }),
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    return proc.stdout


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def linear_repo(git_repo: Path) -> dict:
    """A→B→C→D linear history; A has ok proof, B/C/D have none.

    Tests can write fail/ok proofs as needed.
    """
    a = commit_file(git_repo, "s.py", "def foo():\n    return 1\n", "init foo")
    b = commit_file(git_repo, "s.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n", "add bar")
    c = commit_file(git_repo, "s.py", "def foo():\n    return 99\n\ndef bar():\n    return 2\n", "break foo")
    d = commit_file(git_repo, "s.py", "def foo():\n    return 99\n\ndef bar():\n    return 2\n\ndef baz():\n    return 3\n", "add baz")
    _write_proof(git_repo, a, "pytest", ok=True)
    return {"root": git_repo, "A": a, "B": b, "C": c, "D": d}


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_bisect_no_baseline_raises(git_repo: Path) -> None:
    commit_file(git_repo, "s.py", "x = 1\n", "init")
    with pytest.raises(bisect_mod.NoBaseline):
        bisect_mod.run(git_repo, "pytest")


def test_bisect_head_is_baseline_returns_clean(git_repo: Path) -> None:
    sha = commit_file(git_repo, "s.py", "x = 1\n", "init")
    _write_proof(git_repo, sha, "pytest", ok=True)
    result = bisect_mod.run(git_repo, "pytest")
    assert result.suspect is None
    assert result.reason == "head_is_proven"
    assert result.from_sha == sha
    assert result.to_sha == sha


def test_bisect_head_passes_with_cmd_records_and_returns_clean(git_repo: Path) -> None:
    a = commit_file(git_repo, "s.py", "x = 1\n", "init")
    _write_proof(git_repo, a, "pytest", ok=True)
    b = commit_file(git_repo, "s.py", "x = 2\n", "tweak")
    # cmd 'true' equivalent on Windows: python -c "pass"
    import sys
    result = bisect_mod.run(
        git_repo, "pytest", cmd=[sys.executable, "-c", "pass"],
    )
    assert result.suspect is None
    assert result.reason == "head_is_proven"
    # Now B should have a recorded proof.
    proof = proofs_mod.read(git_repo, b)
    assert proof is not None
    assert proof["checks"]["pytest"]["ok"] is True


def test_bisect_over_cached_proofs_narrows_to_first_failure(linear_repo: dict) -> None:
    # A=ok, B=ok, C=fail, D=fail → suspect is C.
    root = linear_repo["root"]
    _write_proof(root, linear_repo["B"], "pytest", ok=True)
    _write_proof(root, linear_repo["C"], "pytest", ok=False)
    _write_proof(root, linear_repo["D"], "pytest", ok=False)
    result = bisect_mod.run(root, "pytest")
    assert result.suspect == linear_repo["C"]
    assert result.reason == "first_failure"
    assert result.from_sha == linear_repo["B"]
    assert result.to_sha == linear_repo["D"]


def test_bisect_no_cmd_with_gaps_returns_gaps_reason(linear_repo: dict) -> None:
    # A=ok, B/C/D have no proofs. No cmd → gaps.
    root = linear_repo["root"]
    result = bisect_mod.run(root, "pytest")
    assert result.suspect is None
    assert result.reason == "gaps"
    assert set(result.missing) == {linear_repo["B"], linear_repo["C"], linear_repo["D"]}


def test_bisect_fills_gaps_with_cmd(linear_repo: dict, tmp_path_factory) -> None:
    """cmd fills missing proofs; narrows to first failing."""
    root = linear_repo["root"]
    import sys
    # Script must live OUTSIDE the repo or it appears as untracked → dirty.
    script_dir = tmp_path_factory.mktemp("bisect_script")
    script = script_dir / "check.py"
    script.write_text(
        "import pathlib, sys\n"
        "src = pathlib.Path('s.py').read_text()\n"
        "sys.exit(1 if 'return 99' in src else 0)\n",
        encoding="utf-8",
    )
    result = bisect_mod.run(
        root, "pytest", cmd=[sys.executable, str(script)],
    )
    assert result.suspect == linear_repo["C"]
    assert result.reason == "first_failure"
    # B should now have an ok proof; C+D should have fail proofs (or at least C).
    proof_b = proofs_mod.read(root, linear_repo["B"])
    proof_c = proofs_mod.read(root, linear_repo["C"])
    assert proof_b is not None and proof_b["checks"]["pytest"]["ok"] is True
    assert proof_c is not None and proof_c["checks"]["pytest"]["ok"] is False


def test_bisect_reports_ops_from_suspect_manifest(linear_repo: dict) -> None:
    root = linear_repo["root"]
    _write_proof(root, linear_repo["B"], "pytest", ok=True)
    _write_proof(root, linear_repo["C"], "pytest", ok=False)
    result = bisect_mod.run(root, "pytest")
    # C modified body of foo.
    op_names = [op.get("name") for op in result.ops]
    assert "foo" in op_names


def test_bisect_symbol_filter_drops_unrelated_ops(linear_repo: dict) -> None:
    root = linear_repo["root"]
    _write_proof(root, linear_repo["B"], "pytest", ok=True)
    _write_proof(root, linear_repo["C"], "pytest", ok=False)
    # Filter to symbol 'bar' — C doesn't touch bar, so ops should be empty.
    result = bisect_mod.run(root, "pytest", symbol="bar")
    assert result.suspect == linear_repo["C"]
    assert result.ops == []


def test_bisect_dirty_tree_with_cmd_refuses(linear_repo: dict) -> None:
    root = linear_repo["root"]
    (root / "dirty.py").write_text("garbage\n", encoding="utf-8")
    import sys
    with pytest.raises(proofs_mod.DirtyTree):
        bisect_mod.run(root, "pytest", cmd=[sys.executable, "-c", "pass"])


def test_bisect_restores_head_after_running_cmd(linear_repo: dict, tmp_path_factory) -> None:
    root = linear_repo["root"]
    branch_before = gx.current_branch(root)
    head_before = gx.head_sha(root)
    import sys
    # Script that always fails so we walk all commits.
    script = tmp_path_factory.mktemp("bisect_fail") / "fail.py"
    script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    bisect_mod.run(root, "pytest", cmd=[sys.executable, str(script)])
    assert gx.current_branch(root) == branch_before
    assert gx.head_sha(root) == head_before


def test_bisect_records_proofs_for_commits_it_ran(linear_repo: dict, tmp_path_factory) -> None:
    root = linear_repo["root"]
    import sys
    script = tmp_path_factory.mktemp("bisect_ok") / "ok.py"
    script.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    bisect_mod.run(root, "pytest", cmd=[sys.executable, str(script)])
    # HEAD passes → short-circuited; only HEAD (D) got recorded.
    assert proofs_mod.read(root, linear_repo["D"]) is not None


def test_bisect_merge_commit_recurses_one_hop(git_repo: Path) -> None:
    """Merge commit fails; recurse into side branch to find true suspect."""
    a = commit_file(git_repo, "s.py", "def foo():\n    return 1\n", "init")
    _write_proof(git_repo, a, "pytest", ok=True)
    # Create side branch with two commits.
    _git(git_repo, "checkout", "-b", "side")
    c = commit_file(git_repo, "side.py", "def side_a():\n    return 1\n", "side a")
    d = commit_file(git_repo, "side.py", "def side_a():\n    raise RuntimeError\n", "side b breaks")
    # Back to main and merge.
    _git(git_repo, "checkout", "main")
    _git(git_repo, "merge", "--no-ff", "-m", "merge side", "side")
    m = gx.head_sha(git_repo)
    assert m is not None
    # Proofs: merge fails; on side, C ok, D fail.
    _write_proof(git_repo, m, "pytest", ok=False)
    _write_proof(git_repo, c, "pytest", ok=True)
    _write_proof(git_repo, d, "pytest", ok=False)
    result = bisect_mod.run(git_repo, "pytest")
    assert result.suspect == d
    assert result.via_merge == m
