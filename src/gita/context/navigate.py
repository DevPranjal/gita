"""Drill-down and filtering — the navigable half of the protocol.

L1 rolls a subtree up to ``Parent (+4 nested)``. Without ``expand`` an agent that
wants those four has to re-request the whole changeset at a larger budget, or
jump straight to L2 using an entity id it was never given.

There is deliberately no question-answering here. An earlier `ask()` matched
query words against entity ids, so "what should I re-test?" returned every entity
with "test" in its path -- a confidently shaped answer that omitted the untouched
tests actually at risk. Filtering is what this code really does, so that is what
it is called. Question answering waits for WS-3, grounded in facts, and for WS-2
to supply the caller edges it needs.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..diff.changes import ChangeSet, EntityChange
from .layers import ContextView, build_view
from .rollup import MAX_DEPTH, fit_lines

_WORD = re.compile(r"[a-z0-9_]+")


def terms_of(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) > 1]


def matches(change: EntityChange, terms: Iterable[str]) -> bool:
    """Whether an entity's name or path contains every term."""
    entity = change.entity
    name = entity.qualname.lower()
    path = entity.path.lower()
    return all(term in name or term in path for term in terms)


def filter_changes(changes: Iterable[EntityChange], term: str = "",
                   interface_only: bool = False) -> list[EntityChange]:
    selected = [c for c in changes if not c.is_noise]
    if interface_only:
        selected = [c for c in selected if c.affects_interface]
    if term:
        terms = terms_of(term)
        selected = [c for c in selected if matches(c, terms)]
    return selected


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


def focus(changeset: ChangeSet, term: str = "",
          interface_only: bool = False) -> ChangeSet:
    """A ChangeSet restricted to matching entities.

    Callers render *and* serialise from this, so a JSON payload can never
    disagree with the text view about what was selected.
    """
    if not term and not interface_only:
        return changeset

    selected = filter_changes(changeset.material(), term, interface_only)
    focused = ChangeSet()
    focused.files_changed = len({c.entity.path for c in selected})
    focused.file_status = changeset.file_status
    focused.extend(selected)
    return focused


def focus_label(term: str = "", interface_only: bool = False) -> str:
    return " ".join(filter(None, [
        f'filter "{term}"' if term else "",
        "interface-only" if interface_only else "",
    ]))


def filtered_view(changeset: ChangeSet, term: str = "",
                  interface_only: bool = False,
                  budget: int = 1000) -> ContextView:
    """A view restricted to matching entities.

    An empty result stays empty: silently widening the filter would be the same
    dishonesty as answering a question we cannot answer.
    """
    if not term and not interface_only:
        return build_view(changeset, budget=budget)

    return build_view(focus(changeset, term, interface_only), budget=budget,
                      focus=focus_label(term, interface_only))
