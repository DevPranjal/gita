"""Compare two committed revisions using the indexed engine.

TWO ENGINES, ONE VOCABULARY
---------------------------
gita has two entity engines, and until now only one of them could answer
"what changed between these two revisions":

*legacy*   ``entities/extractor.py`` + ``diff/differ.py``. Re-derives entities
           from both revisions on every call and pairs them up heuristically:
           exact content hash first, then body hash, then a 0.6 Jaccard
           similarity threshold on three-token shingles. Fast, needs nothing
           built in advance, works on the worktree -- and *guesses*. A rename is
           accepted because two functions look 60% alike.

*indexed*  ``index/`` (F0-F3). Identity is decided once, at build time, under
           the strict policy in ``matching.py``: similarity never becomes
           identity, relocations need distinctive exact evidence, copies get
           distinct identities, and anything short of that stays ambiguous
           rather than being resolved. The answer is then read back from a
           pinned, immutable generation.

This module converts the indexed engine's *events* into the same ``ChangeSet``
of ``EntityChange`` that the whole ``context/`` layer already consumes, so the
headline, rollup, ranking and rendering work unchanged. The engine changes; the
vocabulary does not.

WHAT THIS CANNOT DO, AND WHY IT IS NOT A DEFECT
----------------------------------------------
The indexed engine reads published generations and never builds on demand, so
indexed compare:

* requires ``gita index`` to have been run for the scope,
* covers only commits on that generation's first-parent membership, and
* cannot see uncommitted or staged work at all.

That last point is why this is an opt-in path rather than a replacement. A bare
``gita diff`` means "what have I not committed yet", which the indexed engine is
designed not to answer. Silently degrading that to a committed-range answer
would be worse than not offering it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..diff.changes import ChangeKind, ChangeSet, EntityChange
from ..entities.model import Entity, EntityKind
from .query import QueryError, open_index

#: One page of events per round trip. The query layer caps limits at 1000; this
#: is smaller so a very large range streams rather than materialising at once.
PAGE = 200

#: Flags that mean the bytes moved but the meaning did not. Kept here rather
#: than inferred, so a new flag in matching.py surfaces as an explicit unknown
#: instead of being silently folded into "body changed".
_COSMETIC_ONLY = frozenset({"cosmetic", "mode_changed"})


def _digest(value: Any) -> str:
    """A stable short digest of an indexed representation.

    The legacy ``Entity`` carries four hashes that its own differ uses to
    classify changes. Here the classification has already been made by the
    indexed engine, so these exist only to keep the dataclass honest and
    comparable. They are derived from real stored evidence -- never invented --
    and an unknown representation stays empty rather than being given a
    plausible-looking value.
    """
    if value is None:
        return ""
    return hashlib.blake2b(repr(value).encode("utf8"), digest_size=8).hexdigest()


def _signature_text(version) -> str:
    """A readable signature from the stored token representation.

    Intent ranking scores against this, so it is worth reconstructing rather
    than leaving blank; but it is the indexed tokens joined, not a re-parse of
    the source, and it is not claimed to be the original text.
    """
    tokens = getattr(version, "signature", None)
    if not tokens:
        return ""
    return " ".join(str(token) for token in tokens)[:200]


def _entity(session, occurrence: dict) -> Entity | None:
    """Build a legacy-shaped ``Entity`` from one indexed occurrence."""
    if occurrence is None:
        return None
    path = occurrence.get("path")
    if path is None:
        # A path that is not valid UTF-8 has no legacy representation. Dropping
        # it silently would under-report the change, so the caller counts it.
        return None

    definition, version = session._definition(occurrence["definition_id"])
    names = list(occurrence.get("names") or definition.names)
    span = occurrence["span"]

    try:
        kind = EntityKind(definition.kind)
    except ValueError:
        # The indexed kind vocabulary is drawn from the same language specs, so
        # this should not happen -- but guessing a kind would corrupt ranking.
        kind = EntityKind.MODULE if definition.synthetic_root else EntityKind.TYPE

    return Entity(
        id=f"{path}::{'::'.join(names)}" if names else path,
        kind=kind,
        name=names[-1] if names else path,
        path=path,
        start_line=span["start_line"],
        end_line=span["end_line"],
        raw_hash=version.source_digest.hex() if version.source_digest else "",
        content_hash=version.id,
        signature_hash=_digest(getattr(version, "signature", None)),
        body_hash=_digest(getattr(version, "body", None)),
        body_size=len(getattr(version, "body", None) or ()),
        signature=_signature_text(version),
        parent_id=None if definition.parent_id is None else str(definition.parent_id),
        synthetic=bool(definition.synthetic_root),
    )


def _classify(event: dict) -> ChangeKind:
    """Map indexed event flags onto the context layer's change vocabulary.

    Order matters and mirrors severity: a rename that also edited the body is
    reported as a rename, because that is the fact a caller needs first.
    """
    flags = set(event.get("flags") or ())
    source, destination = event.get("source"), event.get("destination")

    if source is None and destination is not None:
        return ChangeKind.ADDED
    if destination is None and source is not None:
        return ChangeKind.REMOVED

    if "renamed" in flags:
        return ChangeKind.RENAMED
    if "moved" in flags:
        return ChangeKind.MOVED
    if "signature_changed" in flags:
        return ChangeKind.SIGNATURE_CHANGED
    if flags & {"body_changed", "own_content_changed", "descendant_changed"}:
        return ChangeKind.BODY_CHANGED
    if flags and not flags - _COSMETIC_ONLY:
        return ChangeKind.COSMETIC
    return ChangeKind.UNCHANGED


def _change(session, event: dict) -> EntityChange | None:
    flags = set(event.get("flags") or ())
    current = _entity(session, event.get("destination"))
    previous = _entity(session, event.get("source"))
    if current is None and previous is None:
        return None

    kind = _classify(event)
    # An event the indexed engine itself marked immaterial is noise here too.
    if not event.get("material", True) and kind not in (
            ChangeKind.ADDED, ChangeKind.REMOVED):
        kind = ChangeKind.COSMETIC

    return EntityChange(
        kind=kind,
        current=current,
        previous=previous,
        signature_changed="signature_changed" in flags,
        body_changed=bool(flags & {"body_changed", "own_content_changed"}),
        # Deliberately None. The indexed engine does not accept similarity as
        # identity, so there is no score behind these pairings to report, and
        # inventing one would misrepresent how the match was made.
        similarity=None,
    )


def compare(repository, base: str, head: str, *, scope: str = "default",
            generation: str | None = None, page: int = PAGE
            ) -> tuple[ChangeSet, dict[str, Any]]:
    """``(changeset, coverage)`` for ``base..head``, decided by the indexed engine.

    Coverage is returned, not discarded, because indexed compare inherits the
    index's completeness. An index built with parse gaps cannot prove that an
    entity continued across one, so it honestly reports a departure and a birth
    where a complete index would have reported one edit. Presenting that as a
    finished answer -- "this function was deleted and another added" -- would be
    the tool inventing a story out of its own incompleteness.

    Raises ``QueryError`` when no index covers the request -- deliberately, so
    the caller can fall back or tell the user to build one. Manufacturing an
    answer from a different engine under the same flag would make the two
    indistinguishable in the output.
    """
    changeset = ChangeSet()
    paths: set[str] = set()
    undecodable = 0
    coverage: dict[str, Any] = {}

    with open_index(repository, scope, generation) as session:
        cursor = None
        while True:
            result = session.events_in(base, head, limit=page, cursor=cursor)
            coverage = result.get("coverage") or coverage
            for event in result["results"]:
                change = _change(session, event)
                if change is None:
                    undecodable += 1
                    continue
                changeset.add(change)
                paths.add(change.entity.path)
            cursor = result["page"].get("next_cursor")
            if not cursor:
                break

    changeset.files_changed = len(paths)
    # Not "skipped because uninteresting": these are entities whose raw path is
    # not valid UTF-8 and so has no legacy representation. Counted rather than
    # dropped, because a silent omission is the one thing this project refuses.
    changeset.files_skipped = undecodable
    return changeset, coverage


def coverage_warning(coverage: dict[str, Any]) -> str:
    """A one-line caveat when the index behind an answer could be wrong.

    Keyed on ``gaps``, not ``status``. They answer different questions: an
    index that skipped a LICENSE is honestly ``partial``, but nothing about it
    can turn an edit into a delete plus an add, and warning about it taught a
    reader to ignore the line that matters. Only evidence whose absence can
    break continuity earns a caveat -- and the advice has to be reachable, so
    a rebuild is offered only when a rebuild could actually help.
    """
    if not coverage:
        return ""
    gaps = coverage.get("gaps") or 0
    if not gaps:
        return ""
    return (f"[index coverage {coverage.get('status', 'unknown')}, {gaps} gaps: "
            "entities across a parse gap may be reported as removed and added "
            "rather than changed; rebuild the index to narrow this]")


__all__ = ["compare", "coverage_warning", "QueryError", "PAGE"]

