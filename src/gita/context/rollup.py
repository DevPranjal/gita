"""Depth-adaptive rollup under a token budget.

Spike A finding 2: depth policy dominates compression. Listing every entity at
full nesting depth gave 58.5% token reduction; rolling up to the first path
segment gave 96.1%. Rollup is architecture, not optimisation.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..diff.changes import EntityChange
from .cluster import head_of
from .rank import is_test_path, score_change
from .tokens import count_tokens

MAX_DEPTH = 6

#: Below this, listing test entities individually is cheap and sometimes useful.
#: Above it, they are bulk: in `got-new-option` 100+ test titles all restating
#: the new option pushed the API change out of the agent's view entirely.
TEST_ROLLUP_MIN = 5


def _render(path: str, head: str, group: list[EntityChange]) -> str:
    if len(group) == 1:
        return f"{path}::{head}  [{group[0].kind.value}]"
    return f"{path}::{head}  (+{len(group) - 1} nested)"


def _test_file_line(path: str, group: list[EntityChange]) -> str:
    """One line for a whole test file: how many changed and in what way."""
    counts = Counter(change.kind.value for change in group)
    detail = ", ".join(f"{count} {kind}"
                       for kind, count in sorted(counts.items(),
                                                 key=lambda item: (-item[1], item[0])))
    noun = "test" if len(group) == 1 else "tests"
    return f"{path}  ({len(group)} {noun}: {detail})"


def _split_bulk_tests(
    changes: list[EntityChange],
) -> tuple[list[EntityChange], list[EntityChange]]:
    """Separate test churn worth summarising from changes worth listing.

    Tests are only rolled up when they are bulk *and* something else changed.
    A test-only commit is about its tests, and hiding them would hide the answer.
    """
    tests = [c for c in changes if is_test_path(c.entity.path)]
    rest = [c for c in changes if not is_test_path(c.entity.path)]
    if len(tests) < TEST_ROLLUP_MIN or not rest:
        return changes, []
    return rest, tests


def rollup_lines(changes: list[EntityChange], depth: int = 1) -> list[str]:
    """One line per entity group, rolled up to ``depth`` path segments."""
    material = [change for change in changes if not change.is_noise]
    listed, bulk_tests = _split_bulk_tests(material)

    groups: dict[tuple[str, str], list[EntityChange]] = defaultdict(list)
    for change in listed:
        groups[(change.entity.path, head_of(change, depth))].append(change)

    ranked = sorted(
        groups.items(),
        key=lambda item: (
            -max(score_change(c) for c in item[1]),
            -sum(score_change(c) for c in item[1]),
            item[0],
        ),
    )
    lines = [_render(path, head, group) for (path, head), group in ranked]

    by_file: dict[str, list[EntityChange]] = defaultdict(list)
    for change in bulk_tests:
        by_file[change.entity.path].append(change)
    lines.extend(
        _test_file_line(path, group)
        for path, group in sorted(
            by_file.items(),
            key=lambda item: (-sum(score_change(c) for c in item[1]), item[0]),
        )
    )
    return lines


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
