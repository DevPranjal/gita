"""Turn what an agent types into an entity id.

Storage needs exact identifiers; input does not. An agent asked to trace
`SaveUploadedFile` types exactly that, and requiring `context.go::SaveUploadedFile`
cost a wasted turn and a fallback to `git log -L`.

Ambiguity is reported rather than guessed: silently picking one of two matching
entities would be the same confident-wrong failure that got `ask()` withdrawn.
"""

from __future__ import annotations

from typing import Iterable


class Ambiguous(LookupError):
    def __init__(self, query: str, matches: list[str]):
        self.query = query
        self.matches = matches
        listed = "\n  ".join(matches[:10])
        super().__init__(
            f"'{query}' matches {len(matches)} entities:\n  {listed}\n"
            "Pass one of these ids exactly.")


def candidates(ids: Iterable[str], query: str) -> list[str]:
    """Matches for ``query``, best tier first, without choosing between them."""
    ids = list(ids)
    if query in ids:
        return [query]

    lowered = query.lower()

    def leaf(entity_id: str) -> str:
        return entity_id.rsplit("::", 1)[-1]

    tiers = (
        [i for i in ids if leaf(i) == query],
        [i for i in ids if i.endswith(f"::{query}")],
        [i for i in ids if leaf(i).lower() == lowered],
        [i for i in ids if i.lower().endswith(f"::{lowered}")],
        [i for i in ids if lowered in i.lower()],
    )
    for tier in tiers:
        if tier:
            return tier
    return []


def resolve_entity(ids: Iterable[str], query: str) -> str | None:
    """One entity id, or None. Raises ``Ambiguous`` when the query is unclear."""
    found = candidates(ids, query)
    if not found:
        return None
    if len(found) > 1:
        raise Ambiguous(query, sorted(found))
    return found[0]
