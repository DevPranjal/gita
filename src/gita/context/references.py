"""Whether a name is used anywhere -- the smallest useful slice of blast radius.

A reviewer's first question about an addition is "is this wired in?". Without an
answer, an agent reaches for `git grep` or asks for extra diff context, and gita
becomes an extra call instead of a replacement.

This is **name matching, not a call graph**. It answers "does this identifier
appear elsewhere", which is a good proxy for dead code and a poor one for impact
analysis. Labelled accordingly wherever it is shown; real caller edges are WS-2.
"""

from __future__ import annotations

from ..diff.changes import ChangeKind, ChangeSet
from ..vcs.git import Repo

#: Looking up hundreds of names would cost more than the answer is worth.
MAX_LOOKUPS = 25

#: Very short identifiers match everything and tell you nothing.
MIN_NAME_LENGTH = 3


def reference_counts(repo: Repo, names: list[str]) -> dict[str, int]:
    """How often each name appears in tracked files, excluding its definition."""
    counts: dict[str, int] = {}
    for name in names[:MAX_LOOKUPS]:
        if not name or len(name) < MIN_NAME_LENGTH:
            counts[name] = 0
            continue

        # -F keeps names like "Iterator for Walk" literal rather than a pattern.
        raw = repo.text("grep", "-F", "-c", "--", name, check=False)
        total = 0
        for line in raw.splitlines():
            _, _, tail = line.rpartition(":")
            if tail.strip().isdigit():
                total += int(tail.strip())
        # One occurrence is the definition itself.
        counts[name] = max(0, total - 1)
    return counts


def unreferenced(repo: Repo, changeset: ChangeSet) -> list[str]:
    """Entity ids that were added but whose name appears nowhere else.

    Only additions are considered: a modified function already had callers, or
    the person modifying it knows why it does not.
    """
    added = [c for c in changeset.material()
             if c.kind is ChangeKind.ADDED and not c.entity.synthetic]
    if not added:
        return []

    by_name: dict[str, list[str]] = {}
    for change in added:
        by_name.setdefault(change.entity.name, []).append(change.entity.id)

    counts = reference_counts(repo, list(by_name))
    return [entity_id
            for name, ids in by_name.items() if counts.get(name, 1) == 0
            for entity_id in ids]
