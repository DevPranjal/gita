"""Commit-keyed JSON sidecar notes.

Stored at ``.git/gita/notes/<sha>.json`` — one file per commit, overwritten
on each ``write``. Used by ``gita commit-note`` to record agent identity
(model, session, prompt hash, etc.) for the current ``HEAD``.

Notes are intentionally a free-form ``dict`` — the schema is determined by
whoever writes the note. ``gita who`` surfaces ``model`` and ``session``
fields specially, but any other keys round-trip untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import gita_dir


def notes_dir(root: Path) -> Path:
    return gita_dir(root) / "notes"


def note_path(root: Path, sha: str) -> Path:
    return notes_dir(root) / f"{sha}.json"


def write(root: Path, sha: str, data: dict[str, Any]) -> None:
    d = notes_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    note_path(root, sha).write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def read(root: Path, sha: str) -> dict[str, Any] | None:
    p = note_path(root, sha)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


__all__ = ["notes_dir", "note_path", "write", "read"]
