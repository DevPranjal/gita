"""History queries that walk real git commits + read stored manifests.

If a commit has no stored manifest (e.g. created before ``gita init`` or via
plain ``git commit``), we recompute it on demand from the git trees. That
keeps ``gita symbol-log`` and ``gita explain`` useful on any repo, with the
stored manifests acting as a cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from . import git as gx
from . import store
from .diff import build_for_commit


def manifest_for(root: Path, sha: str) -> dict[str, Any]:
    """Stored manifest if present, otherwise compute (and cache) it."""
    m = store.read(root, sha)
    if m is not None:
        return m
    m = build_for_commit(root, sha)
    if store.is_initialized(root):
        store.write(root, sha, m)
    return m


def walk(root: Path, ref: str = "HEAD", *, max_count: int | None = None) -> Iterator[tuple[str, gx.CommitMeta, dict[str, Any]]]:
    for sha in gx.log_shas(root, ref, max_count=max_count):
        yield sha, gx.commit_meta(root, sha), manifest_for(root, sha)


def symbol_log(root: Path, name: str, *, ref: str = "HEAD", max_count: int | None = None) -> list[dict[str, Any]]:
    """Commits touching ``name`` (newest first).

    Matches ``op.name``, ``op.from``, ``op.to``. For each commit returns
    ``{sha, message, author, timestamp, ops}`` where ``ops`` are only the
    ones that mention the symbol (plus their file path).
    """
    out: list[dict[str, Any]] = []
    for sha, meta, manifest in walk(root, ref, max_count=max_count):
        touching: list[dict[str, Any]] = []
        for fe in manifest.get("files", []):
            for op in fe.get("ops", []):
                if op.get("name") == name or op.get("from") == name or op.get("to") == name:
                    touching.append({"path": fe["path"], **op})
        if touching:
            out.append({
                "sha": sha,
                "message": meta.message,
                "author": meta.author_name,
                "timestamp": meta.timestamp,
                "ops": touching,
            })
    return out


def reindex(root: Path, *, ref: str = "HEAD", force: bool = False) -> dict[str, int]:
    """Backfill stored manifests for commits reachable from ``ref``.

    Returns ``{computed, skipped}`` counts.
    """
    store.init(root)
    computed = 0
    skipped = 0
    for sha in gx.log_shas(root, ref):
        if not force and store.has(root, sha):
            skipped += 1
            continue
        manifest = build_for_commit(root, sha)
        store.write(root, sha, manifest)
        computed += 1
    return {"computed": computed, "skipped": skipped}
