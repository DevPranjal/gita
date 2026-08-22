"""Wire the git layer to the entity engine: two revisions in, a ChangeSet out."""

from __future__ import annotations

from pathlib import Path

from .diff.changes import ChangeSet
from .diff.differ import RENAME_THRESHOLD, diff_trees, reconcile_moves
from .entities.extractor import extract_path
from .entities.model import EntityTree
from .entities.store import TREES
from .vcs.git import STAGED, WORKTREE, ChangedFile, Repo

_BINARY_SNIFF = 8000


def _parse(blob: bytes, path: str) -> EntityTree | None:
    try:
        return extract_path(blob, path)
    except (ValueError, RecursionError):
        return None


def _tree_at(repo: Repo, rev: str | None, path: str) -> EntityTree | None:
    blob = repo.blob(rev, path)
    if blob is None or b"\x00" in blob[:_BINARY_SNIFF]:
        return None
    return TREES.get(blob, path, _parse)


def diff_revisions(repo: str | Path | Repo, base: str = "HEAD",
                   head: str | None = WORKTREE,
                   rename_threshold: float = RENAME_THRESHOLD,
                   paths: list[str] | None = None,
                   changed: list[ChangedFile] | None = None) -> ChangeSet:
    """Context diff between two revisions.

    ``head`` may be a revision, ``None`` for the working tree, or ``STAGED``
    for the index -- matching what `git diff` and `git diff --cached` compare.

    ``paths`` restricts the comparison before any file is read, so git can prune
    by tree hash rather than gita parsing files it will only discard. ``changed``
    supplies that file list when a caller already has it, saving a git call.
    """
    repo = repo if isinstance(repo, Repo) else Repo(repo)
    changeset = ChangeSet()
    collected = []

    if changed is None:
        changed = repo.changed_files(base, head, supported_only=False, paths=paths)
    else:
        changed = list(changed)

    # `git diff HEAD` cannot see untracked files, so a module an agent has just
    # written would not appear at all. Only the working tree has them.
    if head is WORKTREE:
        known = {c.path for c in changed}
        wanted = set(paths) if paths else None
        changed += [ChangedFile("?", path) for path in repo.untracked()
                    if path not in known and (wanted is None or path in wanted)]

    # Every text file counts: unparseable types fall back to a whole-file entity
    # rather than vanishing, because a silent omission reads as "unchanged".
    for changed_file in changed:
        previous = (None if changed_file.is_added
                    else _tree_at(repo, base, changed_file.source_path))
        current = (None if changed_file.is_deleted
                   else _tree_at(repo, head, changed_file.path))

        if previous is None and current is None:
            changeset.files_skipped += 1
            continue

        changeset.files_changed += 1
        changeset.file_status[changed_file.path] = changed_file.status
        for tree in (previous, current):
            if tree is not None and tree.parse_error:
                changeset.parse_errors += 1
                break

        collected += diff_trees(previous, current, rename_threshold)

    changeset.extend(reconcile_moves(collected))
    return changeset


__all__ = ["STAGED", "WORKTREE", "diff_revisions"]
