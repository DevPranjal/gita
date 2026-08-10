"""Wire the git layer to the entity engine: two revisions in, a ChangeSet out."""

from __future__ import annotations

from pathlib import Path

from .diff.changes import ChangeSet
from .diff.differ import RENAME_THRESHOLD, diff_trees, reconcile_moves
from .entities.extractor import extract_path
from .entities.model import EntityTree
from .vcs.git import Repo


def _tree_at(repo: Repo, rev: str, path: str) -> EntityTree | None:
    blob = repo.blob(rev, path)
    if blob is None or b"\x00" in blob[:8000]:
        return None
    try:
        return extract_path(blob, path)
    except (ValueError, RecursionError):
        return None


def diff_revisions(repo: str | Path | Repo, base: str, head: str,
                   rename_threshold: float = RENAME_THRESHOLD) -> ChangeSet:
    """Context diff between two git revisions."""
    repo = repo if isinstance(repo, Repo) else Repo(repo)
    changeset = ChangeSet()
    collected = []

    for changed in repo.changed_files(base, head):
        previous = None if changed.is_added else _tree_at(repo, base, changed.source_path)
        current = None if changed.is_deleted else _tree_at(repo, head, changed.path)

        if previous is None and current is None:
            changeset.files_skipped += 1
            continue

        changeset.files_changed += 1
        for tree in (previous, current):
            if tree is not None and tree.parse_error:
                changeset.parse_errors += 1
                break

        collected += diff_trees(previous, current, rename_threshold)

    changeset.extend(reconcile_moves(collected))
    return changeset
