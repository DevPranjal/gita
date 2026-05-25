"""Shared pytest fixtures for gita tests.

Centralizes git-repo creation so the per-test setup doesn't drown the tests
themselves. Every fixture returns the repo root as :class:`Path`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gita import git as gx


def _run_git(root: Path, *args: str, input_: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, input=input_, encoding="utf-8",
        check=True,
    )
    return proc.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Empty git repo with deterministic user identity, main branch, no commits."""
    gx.init(tmp_path)
    _run_git(tmp_path, "config", "user.email", "gita-tests@example.invalid")
    _run_git(tmp_path, "config", "user.name", "Gita Tests")
    _run_git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


def _commit_file(root: Path, path: str, content: str, message: str) -> str:
    """Write a file, ``git add`` + ``git commit``, return new HEAD sha."""
    file_path = root / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    _run_git(root, "add", "--", path)
    _run_git(root, "commit", "-m", message, "--no-verify")
    return _run_git(root, "rev-parse", "HEAD").strip()


# Public alias — tests import this via ``from conftest import commit_file``.
commit_file = _commit_file


@pytest.fixture
def commit():
    """Fixture form of :func:`commit_file` for tests that prefer DI."""
    return _commit_file
