"""Wire the git layer to the entity engine: two revisions in, a ChangeSet out."""

from __future__ import annotations

from pathlib import Path

from .diff.changes import ChangeSet
from .diff.differ import RENAME_THRESHOLD, diff_trees, reconcile_moves
from .entities.extractor import extract_path
from .entities.model import EntityTree
from .vcs.git import STAGED, WORKTREE, Repo

_BINARY_SNIFF = 8000


def _tree_at(repo: Repo, rev: str | None, path: str) -> EntityTree | None:
    blob = repo.blob(rev, path)
    if blob is None or b"\x00" in blob[:_BINARY_SNIFF]:
        return None
    try:
        return extract_path(blob, path)
    except (ValueError, RecursionError):
        return None


def diff_revisions(repo: str | Path | Repo, base: str = "HEAD",
                   head: str | None = WORKTREE,
                   rename_threshold: float = RENAME_THRESHOLD) -> ChangeSet:
    """Context diff between two revisions.

    ``head`` may be a revision, ``None`` for the working tree, or ``STAGED``
    for the index -- matching what `git diff` and `git diff --cached` compare.
    """
    repo = repo if isinstance(repo, Repo) else Repo(repo)
    changeset = ChangeSet()
    collected = []

    # Every text file counts: unparseable types fall back to a whole-file entity
    # rather than vanishing, because a silent omission reads as "unchanged".
    for changed in repo.changed_files(base, head, supported_only=False):
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


__all__ = ["STAGED", "WORKTREE", "diff_revisions"]
