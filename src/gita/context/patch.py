"""L2: the actual hunks, scoped to a single entity.

The expensive layer. An agent pays for this only after L0/L1 told it which
entity is worth the tokens.
"""

from __future__ import annotations

import difflib

from ..entities.extractor import extract_path
from ..entities.model import Entity
from ..vcs.git import Repo


def _entity_at(repo: Repo, rev: str, path: str, entity_id: str) -> tuple[Entity | None, list[str]]:
    blob = repo.blob(rev, path)
    if blob is None or b"\x00" in blob[:8000]:
        return None, []
    try:
        tree = extract_path(blob, path)
    except (ValueError, RecursionError):
        return None, []
    if tree is None:
        return None, []
    source = blob.decode("utf8", "replace").splitlines(keepends=True)
    return tree.get(entity_id), source


def _slice(entity: Entity | None, source: list[str]) -> list[str]:
    if entity is None:
        return []
    return source[entity.start_line - 1:entity.end_line]


def entity_diff(repo: Repo, base: str, head: str, entity_id: str,
                context_lines: int = 3) -> str:
    """Unified diff of one entity between two revisions.

    Returns an empty string when the entity exists on neither side.
    """
    path = entity_id.split("::", 1)[0]

    previous, old_source = _entity_at(repo, base, path, entity_id)
    current, new_source = _entity_at(repo, head, path, entity_id)
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
