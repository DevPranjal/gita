"""Tests for ``gita.hooks`` and auto-prove config in ``gita.proofs``.

Phase 3 ships a post-commit hook that re-runs configured checks against the
new HEAD, so long-running agents accumulate proofs without manual prodding.
The hook is a thin shim that calls ``python -m gita _auto-prove-hook``; the
real work is :func:`gita.proofs.auto_prove_for_head`, which reads
``.git/gita/auto.json`` and records proofs for every enabled check.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gita import hooks as hooks_mod
from gita import proofs as proofs_mod

from conftest import commit_file


# ---------------------------------------------------------------------------
# hook installation
# ---------------------------------------------------------------------------


def test_install_creates_managed_block(git_repo: Path) -> None:
    path = hooks_mod.install(git_repo)
    assert path == git_repo / ".git" / "hooks" / "post-commit"
    text = path.read_text(encoding="utf-8")
    assert hooks_mod.MARKER_START in text
    assert hooks_mod.MARKER_END in text
    assert "_auto-prove-hook" in text
    # Shebang line should be first.
    assert text.splitlines()[0].startswith("#!")


def test_install_is_idempotent(git_repo: Path) -> None:
    hooks_mod.install(git_repo)
    hooks_mod.install(git_repo)
    text = (git_repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
    assert text.count(hooks_mod.MARKER_START) == 1
    assert text.count(hooks_mod.MARKER_END) == 1


def test_install_preserves_pre_existing_hook_body(git_repo: Path) -> None:
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'user hook'\n", encoding="utf-8")
    hooks_mod.install(git_repo)
    text = hook.read_text(encoding="utf-8")
    assert "echo 'user hook'" in text
    assert hooks_mod.MARKER_START in text


def test_install_sets_executable_on_posix(git_repo: Path) -> None:
    path = hooks_mod.install(git_repo)
    if os.name == "posix":
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR


def test_uninstall_strips_managed_block(git_repo: Path) -> None:
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho keep-me\n", encoding="utf-8")
    hooks_mod.install(git_repo)
    hooks_mod.uninstall(git_repo)
    text = hook.read_text(encoding="utf-8")
    assert hooks_mod.MARKER_START not in text
    assert hooks_mod.MARKER_END not in text
    assert "echo keep-me" in text


def test_uninstall_when_not_installed_is_noop(git_repo: Path) -> None:
    # Should not raise even with no hook file.
    hooks_mod.uninstall(git_repo)
    assert not (git_repo / ".git" / "hooks" / "post-commit").exists()


def test_is_installed_reflects_state(git_repo: Path) -> None:
    assert hooks_mod.is_installed(git_repo) is False
    hooks_mod.install(git_repo)
    assert hooks_mod.is_installed(git_repo) is True
    hooks_mod.uninstall(git_repo)
    assert hooks_mod.is_installed(git_repo) is False


# ---------------------------------------------------------------------------
# auto-prove config (lives on proofs module)
# ---------------------------------------------------------------------------


def test_set_auto_round_trips(git_repo: Path) -> None:
    proofs_mod.set_auto(git_repo, "pytest", cmd=["python", "-m", "pytest"])
    cfg = proofs_mod.read_auto_config(git_repo)
    assert cfg["checks"]["pytest"]["enabled"] is True
    assert cfg["checks"]["pytest"]["cmd"] == ["python", "-m", "pytest"]


def test_disable_auto_marks_disabled(git_repo: Path) -> None:
    proofs_mod.set_auto(git_repo, "pytest", cmd=["python", "-c", "pass"])
    proofs_mod.disable_auto(git_repo, "pytest")
    cfg = proofs_mod.read_auto_config(git_repo)
    assert cfg["checks"]["pytest"]["enabled"] is False


def test_read_auto_config_when_missing(git_repo: Path) -> None:
    cfg = proofs_mod.read_auto_config(git_repo)
    assert cfg == {"checks": {}}


# ---------------------------------------------------------------------------
# auto_prove_for_head
# ---------------------------------------------------------------------------


def test_auto_prove_records_enabled_checks(git_repo: Path) -> None:
    sha = commit_file(git_repo, "a.py", "x = 1\n", "init")
    proofs_mod.set_auto(git_repo, "always", cmd=[sys.executable, "-c", "pass"])
    results = proofs_mod.auto_prove_for_head(git_repo)
    assert [r.name for r in results] == ["always"]
    assert results[0].ok is True
    stored = proofs_mod.read(git_repo, sha)
    assert stored is not None
    assert stored["checks"]["always"]["ok"] is True


def test_auto_prove_skips_disabled(git_repo: Path) -> None:
    commit_file(git_repo, "a.py", "x = 1\n", "init")
    proofs_mod.set_auto(git_repo, "off", cmd=[sys.executable, "-c", "pass"])
    proofs_mod.disable_auto(git_repo, "off")
    results = proofs_mod.auto_prove_for_head(git_repo)
    assert results == []


def test_auto_prove_dirty_tree_returns_empty(git_repo: Path) -> None:
    commit_file(git_repo, "a.py", "x = 1\n", "init")
    (git_repo / "untracked.txt").write_text("dirty", encoding="utf-8")
    proofs_mod.set_auto(git_repo, "always", cmd=[sys.executable, "-c", "pass"])
    # Should silently skip, not raise: the user just committed and may have
    # other in-flight edits; hooks must never break the commit.
    results = proofs_mod.auto_prove_for_head(git_repo)
    assert results == []


def test_auto_prove_no_config_is_noop(git_repo: Path) -> None:
    commit_file(git_repo, "a.py", "x = 1\n", "init")
    results = proofs_mod.auto_prove_for_head(git_repo)
    assert results == []


def test_auto_prove_runs_failing_check_too(git_repo: Path) -> None:
    sha = commit_file(git_repo, "a.py", "x = 1\n", "init")
    proofs_mod.set_auto(
        git_repo, "fails", cmd=[sys.executable, "-c", "import sys; sys.exit(7)"]
    )
    results = proofs_mod.auto_prove_for_head(git_repo)
    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].exit_code == 7
    stored = proofs_mod.read(git_repo, sha)
    assert stored["checks"]["fails"]["ok"] is False


# ---------------------------------------------------------------------------
# end-to-end: hook runs via real git commit
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="post-commit hooks need a POSIX shell on Windows CI")
def test_end_to_end_hook_records_on_commit(git_repo: Path) -> None:
    proofs_mod.set_auto(git_repo, "always", cmd=[sys.executable, "-c", "pass"])
    hooks_mod.install(git_repo)
    sha = commit_file(git_repo, "a.py", "x = 1\n", "init")
    stored = proofs_mod.read(git_repo, sha)
    assert stored is not None
    assert stored["checks"]["always"]["ok"] is True
