"""Depth-adaptive rollup under a token budget.

Spike A finding 2: depth policy dominates compression. Listing every entity at
full nesting depth gave 58.5% token reduction; rolling up to the first path
segment gave 96.1%. Rollup is architecture, not optimisation.
"""

from __future__ import annotations

from collections import defaultdict

from ..diff.changes import EntityChange
from .cluster import head_of
from .rank import score_change
from .tokens import count_tokens

MAX_DEPTH = 6


def _render(path: str, head: str, group: list[EntityChange]) -> str:
    if len(group) == 1:
        return f"{path}::{head}  [{group[0].kind.value}]"
    return f"{path}::{head}  (+{len(group) - 1} nested)"


def rollup_lines(changes: list[EntityChange], depth: int = 1) -> list[str]:
    """One line per entity group, rolled up to ``depth`` path segments."""
    groups: dict[tuple[str, str], list[EntityChange]] = defaultdict(list)
    for change in changes:
        if change.is_noise:
            continue
        groups[(change.entity.path, head_of(change, depth))].append(change)

    ranked = sorted(
        groups.items(),
        key=lambda item: (
            -max(score_change(c) for c in item[1]),
            -sum(score_change(c) for c in item[1]),
            item[0],
        ),
    )
    return [_render(path, head, group) for (path, head), group in ranked]


def fit_lines(changes: list[EntityChange], budget: int,
              max_depth: int = MAX_DEPTH) -> tuple[list[str], int]:
    """Deepest rollup that fits ``budget``, dropping lines only as a last resort."""
    best: tuple[list[str], int] = ([], 1)
    fitted = False

    for depth in range(1, max_depth + 1):
        lines = rollup_lines(changes, depth)
        if count_tokens("\n".join(lines)) > budget:
            break
        best, fitted = (lines, depth), True

    if fitted:
        return best

    # Even the shallowest view overflows: keep whichever lines fit, highest ranked first.
    kept: list[str] = []
    for line in rollup_lines(changes, 1):
        if count_tokens("\n".join([*kept, line])) > budget:
            break
        kept.append(line)
    return kept, 1
