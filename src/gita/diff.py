"""Building structural manifests over a real git tree.

This module is a thin orchestration layer:

* :func:`build_for_trees` — given two ``{path: text}`` mappings, build a
  manifest using the (improved) symbol diff in :mod:`gita.symdiff`.
* :func:`build_for_refs` — same, but pulls the trees out of git.
* :func:`build_for_working_tree` — HEAD (or staged) vs working tree.

The actual symbol-level op extraction lives in :mod:`gita.symdiff`. We re-use
:func:`gitpp.manifest.build_manifest` (the summary roll-up + schema envelope)
unchanged, because it's purely data shaping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gita._manifest import build_manifest

from . import git as gx
from .symdiff import diff_sources


def _is_python(path: str) -> bool:
    return path.endswith(".py")


def _collect_tree_text(root: Path, ref: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in gx.ls_tree(root, ref):
        if not _is_python(entry.path):
            continue
        try:
            out[entry.path] = gx.cat_blob(root, entry.blob_sha)
        except UnicodeDecodeError:
            continue
    return out


def _collect_working_tree(root: Path) -> dict[str, str]:
    """All tracked + untracked .py files in the working tree."""
    out: dict[str, str] = {}
    # Tracked files
    proc = gx._run(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    for path in proc.stdout.split("\x00"):
        if not path or not _is_python(path):
            continue
        text = gx.working_tree_text(root, path)
        if text is not None:
            out[path] = text
    return out


def _collect_staged_tree(root: Path) -> dict[str, str]:
    """Snapshot of the index (what would be committed) — .py only."""
    out: dict[str, str] = {}
    proc = gx._run(root, ["ls-files", "-z", "--cached"])
    for path in proc.stdout.split("\x00"):
        if not path or not _is_python(path):
            continue
        text = gx.staged_text(root, path)
        if text is not None:
            out[path] = text
    return out


def build_for_trees(
    prev: dict[str, str],
    curr: dict[str, str],
    *,
    from_sha: str | None,
    to_sha: str | None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(set(prev) | set(curr)):
        files.append(diff_sources(prev.get(path), curr.get(path), path=path))
    return build_manifest(files, from_sha=from_sha, to_sha=to_sha)


def build_for_refs(root: Path, from_ref: str | None, to_ref: str) -> dict[str, Any]:
    """Manifest for ``from_ref → to_ref``. ``from_ref=None`` uses the empty tree."""
    to_sha = gx.rev_parse(root, to_ref)
    if from_ref is None:
        prev: dict[str, str] = {}
        from_sha = None
    else:
        from_sha = gx.rev_parse(root, from_ref)
        prev = _collect_tree_text(root, from_sha)
    curr = _collect_tree_text(root, to_sha)
    return build_for_trees(prev, curr, from_sha=from_sha, to_sha=to_sha)


def build_for_working_tree(root: Path, *, staged: bool = False) -> dict[str, Any]:
    """Manifest from HEAD to working tree (or to index if ``staged``)."""
    from_sha = gx.head_sha(root)
    prev = _collect_tree_text(root, from_sha) if from_sha else {}
    curr = _collect_staged_tree(root) if staged else _collect_working_tree(root)
    return build_for_trees(prev, curr, from_sha=from_sha, to_sha=None)


def build_for_commit(root: Path, sha: str) -> dict[str, Any]:
    """Manifest for ``parent(sha) → sha`` (the change introduced by ``sha``)."""
    meta = gx.commit_meta(root, sha)
    parent = meta.parents[0] if meta.parents else None
    return build_for_refs(root, parent, sha)
