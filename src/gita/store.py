"""Persist manifests under ``.git/gita/`` so they live with the repo.

Layout::

    .git/gita/manifests/<commit_sha>.json     # one file per commit

Storing inside ``.git/`` means manifests are local to the clone (not pushed
by default). That's a deliberate v1 trade: it makes the first user experience
zero-friction (no extra remotes, no notes ref to fetch) and we can add a
``gita push-manifests`` later that writes ``refs/notes/gita`` for sharing.

A manifest file is just the JSON returned by :mod:`gita.diff`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import git as gx


def gita_dir(root: Path) -> Path:
    return root / ".git" / "gita"


def manifests_dir(root: Path) -> Path:
    return gita_dir(root) / "manifests"


def _ensure(root: Path) -> Path:
    d = manifests_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(root: Path, commit_sha: str) -> Path:
    return manifests_dir(root) / f"{commit_sha}.json"


def write(root: Path, commit_sha: str, manifest: dict[str, Any]) -> Path:
    """Write ``manifest`` for ``commit_sha``. Returns the file path."""
    _ensure(root)
    p = manifest_path(root, commit_sha)
    p.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def read(root: Path, commit_sha: str) -> dict[str, Any] | None:
    p = manifest_path(root, commit_sha)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def has(root: Path, commit_sha: str) -> bool:
    return manifest_path(root, commit_sha).is_file()


def list_commits_with_manifests(root: Path) -> list[str]:
    d = manifests_dir(root)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))


def is_initialized(root: Path) -> bool:
    """``gita init`` has been run for this repo (gita dir exists)."""
    return gita_dir(root).is_dir()


def init(root: Path) -> Path:
    """Create ``.git/gita/`` inside an existing git repo."""
    if not gx.is_git_dir(root):
        raise FileNotFoundError(f"not a git repository: {root}")
    return _ensure(root)
