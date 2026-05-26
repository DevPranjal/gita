"""Manage the ``post-commit`` git hook that drives auto-prove.

The hook is a small shim with a recognizable marker block so we can install
and uninstall idempotently without trampling user-written hook content. The
shim invokes :func:`gita.proofs.auto_prove_for_head` via
``python -m gita _auto-prove-hook`` so the hook stays language-portable
(it's posix-sh on POSIX, a ``.cmd`` would be needed for native Windows git
hooks — we ship the sh form and let Git for Windows' bundled bash run it).

We deliberately do NOT make the hook fail the commit — auto-prove is a
convenience, never a gate. If proofs fail or can't run, the commit still
succeeds; the failure shows up later as a ``✗`` on the proof glyph.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from . import git as gx


MARKER_START = "# >>> gita managed: post-commit auto-prove >>>"
MARKER_END = "# <<< gita managed: post-commit auto-prove <<<"


def hook_path(root: Path) -> Path:
    return root / ".git" / "hooks" / "post-commit"


def _managed_block(python: str) -> str:
    # Two-newline padding so we never glue to user content.
    return (
        f"\n{MARKER_START}\n"
        f'"{python}" -m gita _auto-prove-hook || true\n'
        f"{MARKER_END}\n"
    )


def _strip_block(text: str) -> str:
    if MARKER_START not in text:
        return text
    before, _, rest = text.partition(MARKER_START)
    _, _, after = rest.partition(MARKER_END)
    # Drop a single trailing newline left behind by the block.
    if after.startswith("\n"):
        after = after[1:]
    if before.endswith("\n\n"):
        before = before[:-1]
    return before + after


def install(root: Path, *, python: str | None = None) -> Path:
    """Install (or refresh) the managed auto-prove block in ``post-commit``.

    Preserves any non-managed content already in the file. Idempotent.
    """
    if not gx.is_git_dir(root):
        raise FileNotFoundError(f"not a git repository: {root}")
    py = python or sys.executable
    path = hook_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        existing = _strip_block(existing)
    else:
        existing = "#!/bin/sh\n"
    if not existing.startswith("#!"):
        existing = "#!/bin/sh\n" + existing
    if not existing.endswith("\n"):
        existing += "\n"

    new_text = existing + _managed_block(py)
    path.write_text(new_text, encoding="utf-8")

    if os.name == "posix":
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return path


def uninstall(root: Path) -> None:
    """Remove the managed block; leave user content intact. No-op if absent."""
    path = hook_path(root)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if MARKER_START not in text:
        return
    new_text = _strip_block(text)
    # If we'd be left with only a shebang and whitespace, remove the file
    # so we don't leave an empty hook lying around.
    stripped = "\n".join(
        line for line in new_text.splitlines() if line.strip() and not line.startswith("#!")
    )
    if not stripped:
        path.unlink()
        return
    path.write_text(new_text, encoding="utf-8")


def is_installed(root: Path) -> bool:
    path = hook_path(root)
    if not path.exists():
        return False
    return MARKER_START in path.read_text(encoding="utf-8")


__all__ = [
    "MARKER_START",
    "MARKER_END",
    "hook_path",
    "install",
    "uninstall",
    "is_installed",
]
