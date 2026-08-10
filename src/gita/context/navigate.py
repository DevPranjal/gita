"""Drill-down and query-driven slicing — the navigable half of the protocol.

L1 rolls a subtree up to ``Parent (+4 nested)``. Without ``expand`` an agent that
wants those four has to re-request the whole changeset at a larger budget, or
jump straight to L2 using an entity id it was never given.

Routing here is deterministic keyword matching, and is labelled as such. WS-3 may
replace the router with a model; it may not let the model invent entities.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..diff.changes import ChangeSet, EntityChange
from .layers import ContextView, build_view
from .rank import score_change
from .rollup import MAX_DEPTH, fit_lines

_WORD = re.compile(r"[a-z0-9_]+")

_STOPWORDS = frozenset({
    "the", "and", "for", "did", "does", "what", "which", "was", "were", "are",
    "any", "all", "this", "that", "with", "from", "has", "have", "had", "how",
    "why", "who", "when", "where", "show", "give", "tell", "about", "into",
    "changed", "change", "changes", "diff", "commit", "please",
})

#: Intent keywords -> a predicate over changes. Crude on purpose, and honest.
_INTENTS: dict[str, frozenset[str]] = {
    "interface": frozenset({"api", "interface", "signature", "break", "breaking",
                            "compat", "compatible", "caller", "callers", "public",
                            "contract"}),
    "test": frozenset({"test", "tests", "retest", "testing", "spec", "coverage"}),
    "added": frozenset({"new", "added", "add", "introduced"}),
    "removed": frozenset({"removed", "deleted", "gone", "dropped"}),
}


def terms_of(question: str) -> list[str]:
    return [w for w in _WORD.findall(question.lower())
            if len(w) > 2 and w not in _STOPWORDS]


def intents_of(question: str) -> set[str]:
    words = set(_WORD.findall(question.lower()))
    return {name for name, keywords in _INTENTS.items() if words & keywords}


def relevance(change: EntityChange, terms: Iterable[str]) -> int:
    """How many query terms appear in an entity's identity or signature."""
    haystack = f"{change.entity.id} {change.entity.signature}".lower()
    return sum(1 for term in terms if term in haystack)


def expand(changes: Iterable[EntityChange], entity_id: str,
           budget: int = 400, max_depth: int = MAX_DEPTH) -> list[str]:
    """L1 lines for the descendants of ``entity_id``, within ``budget``."""
    prefix = f"{entity_id}::"
    selected = [c for c in changes
                if not c.is_noise and c.entity.id.startswith(prefix)]
    if not selected:
        return []
    lines, _ = fit_lines(selected, budget, max_depth=max_depth)
    return lines


def query_view(changeset: ChangeSet, question: str,
               budget: int = 1000) -> ContextView:
    """A view narrowed to the changes a question is about.

    Falls back to the full view when nothing matches -- an empty answer to a
    badly-worded question is worse than an unfocused one.
    """
    question = (question or "").strip()
    if not question:
        return build_view(changeset, budget=budget)

    material = changeset.material()
    terms = terms_of(question)
    intents = intents_of(question)

    selected = [c for c in material if relevance(c, terms)] if terms else []

    if "interface" in intents:
        by_intent = [c for c in material if c.affects_interface]
        selected = [c for c in selected if c.affects_interface] or selected or by_intent
    if "added" in intents:
        selected = selected or [c for c in material if c.kind.value == "added"]
    if "removed" in intents:
        selected = selected or [c for c in material if c.kind.value == "removed"]

    if not selected:
        selected = material

    focused = ChangeSet()
    focused.files_changed = len({c.entity.path for c in selected})
    focused.extend(sorted(selected, key=lambda c: -score_change(c)))

    return build_view(focused, budget=budget, focus=question)
