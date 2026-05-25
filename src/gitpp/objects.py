"""Content-addressed object store for gitpp.

Layout (under ``<repo>/.gitpp/``)::

    HEAD                    # text file: "ref: refs/heads/<name>" or a raw sha
    refs/heads/<name>       # text file: commit sha
    index                   # json: {path: file_sha}
    objects/<aa>/<bbbb...>  # canonical-json bytes of an object

Object kinds (all stored as canonical JSON with a ``kind`` discriminator):

* ``file``    — ``{"kind": "file", "source": "<utf-8 source text>"}``
* ``tree``    — ``{"kind": "tree", "entries": {"<path>": "<file_sha>"}}``
* ``commit``  — ``{"kind": "commit", "tree": "<sha>", "parents": [...],
                   "message": "...", "timestamp": <unix>}``

In v0.0 we deliberately do *not* split files into per-CST-node objects — the
merge engine in :mod:`gitpp.merge` parses the source on demand. Per-node
content addressing (for cross-file dedup + stable IDs) is a v0.1 concern.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


# Canonical JSON: sorted keys, no whitespace, ensure_ascii=False so unicode
# source is stored verbatim. This is what gets hashed and written to disk.
def canonical_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_path(repo_root: Path, sha: str) -> Path:
    return repo_root / ".gitpp" / "objects" / sha[:2] / sha[2:]


def write_object(repo_root: Path, obj: dict[str, Any]) -> str:
    """Write ``obj`` as a canonical-JSON object, return its sha. Idempotent."""
    data = canonical_bytes(obj)
    sha = sha256_hex(data)
    path = object_path(repo_root, sha)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return sha


def read_object(repo_root: Path, sha: str) -> dict[str, Any]:
    path = object_path(repo_root, sha)
    if not path.exists():
        raise KeyError(f"object not found: {sha}")
    return json.loads(path.read_bytes())


# --- typed constructors (just to keep call-sites readable) ---


def make_file(source: str) -> dict[str, Any]:
    return {"kind": "file", "source": source}


def make_tree(entries: dict[str, str]) -> dict[str, Any]:
    return {"kind": "tree", "entries": dict(entries)}


def make_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Wrap a manifest payload in the standard ``kind`` envelope."""
    # The manifest dict already has ``kind: "manifest"`` from build_manifest,
    # so we just hand it back. Kept as a constructor for symmetry / discovery.
    return dict(manifest)


def make_commit(
    tree: str,
    parents: list[str],
    message: str,
    timestamp: int,
    *,
    manifest: str | None = None,
) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "kind": "commit",
        "tree": tree,
        "parents": list(parents),
        "message": message,
        "timestamp": timestamp,
    }
    if manifest is not None:
        obj["manifest"] = manifest
    return obj
