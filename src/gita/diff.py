"""Building structural manifests over a real git tree.

This module is a thin orchestration layer:

* :func:`build_for_trees` — given two ``{path: text}`` mappings, build a
  manifest using the (improved) symbol diff in :mod:`gita.symdiff`.
* :func:`build_for_refs` — same, but pulls the trees out of git.
* :func:`build_for_working_tree` — HEAD (or staged) vs working tree.

Phase 1 of v0.2 extended the tree collectors to keep non-Python files too.
They are marked ``parseable=False`` and routed to a textual-diff path
instead of through :mod:`gita.symdiff`, so YAML / Dockerfile / Markdown
edits still show up in the manifest (just without symbol-level ops).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gita._manifest import build_manifest

from . import git as gx
from .symdiff import diff_sources


def _is_python(path: str) -> bool:
    return path.endswith(".py")


@dataclass(frozen=True)
class FileBlob:
    """A single file's contents plus whether it goes through symbol parsing."""

    text: str
    parseable: bool


# ---------------------------------------------------------------------------
# tree collectors
# ---------------------------------------------------------------------------


def _collect_tree_files(root: Path, ref: str) -> dict[str, FileBlob]:
    out: dict[str, FileBlob] = {}
    for entry in gx.ls_tree(root, ref):
        try:
            text = gx.cat_blob(root, entry.blob_sha)
        except UnicodeDecodeError:
            continue
        out[entry.path] = FileBlob(text=text, parseable=_is_python(entry.path))
    return out


def _collect_working_tree(root: Path) -> dict[str, FileBlob]:
    """All tracked + untracked files in the working tree."""
    out: dict[str, FileBlob] = {}
    proc = gx._run(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    for path in proc.stdout.split("\x00"):
        if not path:
            continue
        text = gx.working_tree_text(root, path)
        if text is None:
            continue
        out[path] = FileBlob(text=text, parseable=_is_python(path))
    return out


def _collect_staged_tree(root: Path) -> dict[str, FileBlob]:
    """Snapshot of the index (what would be committed)."""
    out: dict[str, FileBlob] = {}
    proc = gx._run(root, ["ls-files", "-z", "--cached"])
    for path in proc.stdout.split("\x00"):
        if not path:
            continue
        text = gx.staged_text(root, path)
        if text is None:
            continue
        out[path] = FileBlob(text=text, parseable=_is_python(path))
    return out


# ---------------------------------------------------------------------------
# manifest assembly
# ---------------------------------------------------------------------------


def _as_blob(value: str | FileBlob | None) -> FileBlob | None:
    """Normalize the union accepted by :func:`build_for_trees`.

    In-process callers (tests, history.walk) can still pass a plain ``str``
    for convenience — it's treated as a parseable .py blob.
    """
    if value is None:
        return None
    if isinstance(value, FileBlob):
        return value
    return FileBlob(text=value, parseable=True)


def _textual_diff(prev: str, curr: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            prev.splitlines(keepends=True),
            curr.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _entry_for_non_parseable(
    path: str, prev: FileBlob | None, curr: FileBlob | None
) -> dict[str, Any] | None:
    """File entry for a non-Python file. ``None`` if unchanged (omitted)."""
    prev_text = prev.text if prev is not None else ""
    curr_text = curr.text if curr is not None else ""
    if prev is None and curr is None:
        return None
    if prev is None:
        status = "added"
    elif curr is None:
        status = "deleted"
    else:
        if prev_text == curr_text:
            return None
        status = "modified"
    return {
        "path": path,
        "status": status,
        "parseable": False,
        "textual_diff": _textual_diff(prev_text, curr_text, path),
    }


def build_for_trees(
    prev: dict[str, str | FileBlob],
    curr: dict[str, str | FileBlob],
    *,
    from_sha: str | None,
    to_sha: str | None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(set(prev) | set(curr)):
        p = _as_blob(prev.get(path))
        c = _as_blob(curr.get(path))
        parseable = (p is None or p.parseable) and (c is None or c.parseable)
        if parseable:
            files.append(
                diff_sources(
                    p.text if p else None,
                    c.text if c else None,
                    path=path,
                )
            )
        else:
            entry = _entry_for_non_parseable(path, p, c)
            if entry is not None:
                files.append(entry)
    return build_manifest(files, from_sha=from_sha, to_sha=to_sha)


def build_for_refs(root: Path, from_ref: str | None, to_ref: str) -> dict[str, Any]:
    """Manifest for ``from_ref → to_ref``. ``from_ref=None`` uses the empty tree."""
    to_sha = gx.rev_parse(root, to_ref)
    if from_ref is None:
        prev: dict[str, FileBlob] = {}
        from_sha = None
    else:
        from_sha = gx.rev_parse(root, from_ref)
        prev = _collect_tree_files(root, from_sha)
    curr = _collect_tree_files(root, to_sha)
    return build_for_trees(prev, curr, from_sha=from_sha, to_sha=to_sha)


def build_for_working_tree(root: Path, *, staged: bool = False) -> dict[str, Any]:
    """Manifest from HEAD to working tree (or to index if ``staged``)."""
    from_sha = gx.head_sha(root)
    prev = _collect_tree_files(root, from_sha) if from_sha else {}
    curr = _collect_staged_tree(root) if staged else _collect_working_tree(root)
    return build_for_trees(prev, curr, from_sha=from_sha, to_sha=None)


def build_for_commit(root: Path, sha: str) -> dict[str, Any]:
    """Manifest for ``parent(sha) → sha`` (the change introduced by ``sha``)."""
    meta = gx.commit_meta(root, sha)
    parent = meta.parents[0] if meta.parents else None
    return build_for_refs(root, parent, sha)
