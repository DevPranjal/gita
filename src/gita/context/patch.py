"""L2: the actual hunks, scoped to a single entity.

The expensive layer. An agent pays for this only after L0/L1 told it which
entity is worth the tokens.
"""

from __future__ import annotations

import difflib

from ..entities.extractor import extract_path
from ..entities.model import Entity, EntityTree
from ..vcs.git import Repo


def _load(repo: Repo, rev: str | None, path: str,
          cache: dict | None) -> tuple[EntityTree | None, list[str]]:
    """Parse one revision of one file, at most once per cache.

    A large diff asks for many entities from the same file. Re-reading and
    re-parsing it per entity made `got-new-option` a ten-second call, which is
    long enough that an agent starts reading files by hand instead.
    """
    key = (rev, path)
    if cache is not None and key in cache:
        return cache[key]

    result: tuple[EntityTree | None, list[str]] = (None, [])
    blob = repo.blob(rev, path)
    if blob is not None and b"\x00" not in blob[:8000]:
        try:
            tree = extract_path(blob, path)
        except (ValueError, RecursionError):
            tree = None
        if tree is not None:
            result = (tree, blob.decode("utf8", "replace").splitlines(keepends=True))

    if cache is not None:
        cache[key] = result
    return result


def _entity_at(repo: Repo, rev: str, path: str, entity_id: str,
               cache: dict | None = None) -> tuple[Entity | None, list[str]]:
    tree, source = _load(repo, rev, path, cache)
    if tree is None:
        return None, []
    return tree.get(entity_id), source


def _slice(entity: Entity | None, source: list[str]) -> list[str]:
    if entity is None:
        return []
    return source[entity.start_line - 1:entity.end_line]


def entity_diff(repo: Repo, base: str, head: str, entity_id: str,
                context_lines: int = 3, cache: dict | None = None) -> str:
    """Unified diff of one entity between two revisions.

    Returns an empty string when the entity exists on neither side. Pass a
    shared ``cache`` dict when diffing several entities from the same files.
    """
    path = entity_id.split("::", 1)[0]

    previous, old_source = _entity_at(repo, base, path, entity_id, cache)
    current, new_source = _entity_at(repo, head, path, entity_id, cache)
    if previous is None and current is None:
        return ""

    patch = difflib.unified_diff(
        _slice(previous, old_source),
        _slice(current, new_source),
        fromfile=f"a/{entity_id}",
        tofile=f"b/{entity_id}",
        n=context_lines,
    )
    return "".join(patch)
