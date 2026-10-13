"""Depth-adaptive rollup under a token budget.

Spike A finding 2: depth policy dominates compression. Listing every entity at
full nesting depth gave 58.5% token reduction; rolling up to the first path
segment gave 96.1%. Rollup is architecture, not optimisation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from ..diff.changes import EntityChange
from .cluster import head_of
from .rank import Intent, intent_bonus, is_test_path, score_change
from .tokens import count_tokens

MAX_DEPTH = 6

#: Below this, listing test entities individually is cheap and sometimes useful.
#: Above it, they are bulk: in `got-new-option` 100+ test titles all restating
#: the new option pushed the API change out of the agent's view entirely.
TEST_ROLLUP_MIN = 5

#: The same edit in this many sibling files is one fact, not N. Two is not a
#: pattern, and grouping a pair hides as much as it saves.
MIN_PARALLEL_FILES = 3

#: Beyond this the headline's file list is the better place to look.
MAX_NAMED_PATHS = 8


@dataclass(frozen=True, slots=True)
class SummaryRow:
    text: str
    entity_ids: frozenset[str]


def _by_shape(
    groups: dict[tuple[str, str], list[EntityChange]],
) -> dict[tuple[str, tuple[str, ...]], list[tuple[str, list[EntityChange]]]]:
    """Index groups by what changed, so the same change in N files is visible."""
    shapes: dict[tuple[str, tuple[str, ...]], list[tuple[str, list[EntityChange]]]] = \
        defaultdict(list)
    for (path, head), group in groups.items():
        kinds = tuple(sorted({change.kind.value for change in group}))
        shapes[(head, kinds)].append((path, group))
    for members in shapes.values():
        members.sort(key=lambda item: item[0])
    return shapes


def _parallel_line(head: str, kinds: tuple[str, ...], paths: list[str]) -> str:
    shown = ", ".join(paths[:MAX_NAMED_PATHS])
    if len(paths) > MAX_NAMED_PATHS:
        shown += f", +{len(paths) - MAX_NAMED_PATHS} more"
    return f"{', '.join(kinds)} {head} in {len(paths)} files: {shown}"


def line_span(group: list[EntityChange]) -> str:
    """``  L18-19`` for a group of changes, or ``""`` when it cannot be stated.

    Naming an entity says *what* changed; the range says *where to look*, which
    is otherwise a search the agent has to pay for. The revision is always
    implied correctly: an entity that only exists before the change is marked,
    because a bare range would point at whatever occupies those lines now.

    A mixed group reports the new side, which is where anything still readable
    lives.
    """
    present = [c.current for c in group if c.current is not None]
    side = ""
    if not present:
        present = [c.previous for c in group if c.previous is not None]
        side = " (before)"
    if not present:
        return ""

    start = min(entity.start_line for entity in present)
    end = max(entity.end_line for entity in present)
    return f"  L{start}{side}" if start == end else f"  L{start}-{end}{side}"


def _render(path: str, head: str, group: list[EntityChange],
            spans: bool = True) -> str:
    span = line_span(group) if spans else ""
    if len(group) == 1:
        return f"{group[0].kind.value} {path}::{head}{span}"
    return f"{path}::{head}  (+{len(group) - 1} nested){span}"


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
    intent: Intent | None = None,
) -> tuple[list[EntityChange], list[EntityChange]]:
    """Separate test churn worth summarising from changes worth listing.

    Tests are only rolled up when they are bulk *and* something else changed.
    A test-only commit is about its tests, and hiding them would hide the answer.

    A test the request actually names is not churn either. This split runs before
    ranking, so without this exemption an intent of "the helper assertion is
    wrong" could raise that test's score and still have it summarised away as
    part of a bulk count.
    """
    def is_bulk(change: EntityChange) -> bool:
        return (is_test_path(change.entity.path)
                and not intent_bonus(change, intent))

    tests = [c for c in changes if is_bulk(c)]
    rest = [c for c in changes if not is_bulk(c)]
    if len(tests) < TEST_ROLLUP_MIN or not rest:
        return changes, []
    return rest, tests


def rollup_lines(changes: list[EntityChange], depth: int = 1,
                 intent: Intent | None = None,
                 group_paths: bool = False,
                 spans: bool = True) -> list[str]:
    """One line per entity group, rolled up to ``depth`` path segments.

    ``spans=False`` drops the trailing ``L18-19``. It is the first thing shed
    under budget pressure: the range says *where to look*, but the name says
    *what changed*, and an entity that is never named cannot be looked up at all.
    """
    return [row.text for row in _rollup_rows(changes, depth, intent, group_paths, spans)]


def _rollup_rows(changes: list[EntityChange], depth: int = 1,
                 intent: Intent | None = None, group_paths: bool = False,
                 spans: bool = True) -> list[SummaryRow]:
    material = [change for change in changes if not change.is_noise]
    listed, bulk_tests = _split_bulk_tests(material, intent)

    groups: dict[tuple[str, str], list[EntityChange]] = defaultdict(list)
    for change in listed:
        groups[(change.entity.path, head_of(change, depth))].append(change)

    rendered: list[tuple[float, float, str, SummaryRow]] = []
    for (head, kinds), members in _by_shape(groups).items():
        if len(members) >= MIN_PARALLEL_FILES:
            flat = [c for _, group in members for c in group]
            rendered.append((max(score_change(c, intent) for c in flat),
                             sum(score_change(c, intent) for c in flat),
                             members[0][0],
                             SummaryRow(_parallel_line(head, kinds, [p for p, _ in members]),
                                        frozenset(c.id for c in flat))))
        else:
            for path, group in members:
                rendered.append((max(score_change(c, intent) for c in group),
                                 sum(score_change(c, intent) for c in group),
                                 path,
                                 SummaryRow(_render(path, head, group, spans),
                                            frozenset(c.id for c in group))))

    by_file: dict[str, list[EntityChange]] = defaultdict(list)
    for change in bulk_tests:
        by_file[change.entity.path].append(change)

    rendered.sort(key=lambda item: (-item[0], -item[1], item[2], item[3].text))
    if not group_paths:
        return [line for *_, line in rendered] + [
            SummaryRow(_test_file_line(path, group), frozenset(c.id for c in group))
            for path, group in sorted(
                by_file.items(),
                key=lambda item: (-sum(score_change(c) for c in item[1]), item[0]),
            )
        ]

    rows_by_path: dict[str, list[SummaryRow]] = defaultdict(list)
    for _, _, path, line in rendered:
        rows_by_path[path].append(line)

    lines = []
    for _, _, path, _ in rendered:
        rows = rows_by_path.pop(path, None)
        if rows is None:
            continue
        if len(rows) == 1:
            lines.append(rows[0])
        else:
            lines.append(
                SummaryRow(
                    f"{path}\n"
                    + "\n".join(f"  {row.text.replace(f'{path}::', '', 1)}" for row in rows),
                    frozenset(key for row in rows for key in row.entity_ids),
                )
            )

    lines.extend(
        SummaryRow(_test_file_line(path, group), frozenset(c.id for c in group))
        for path, group in sorted(
            by_file.items(),
            key=lambda item: (-sum(score_change(c, intent) for c in item[1]), item[0]),
        )
    )
    return lines


def fit_rows(changes: list[EntityChange], budget: int,
             max_depth: int = MAX_DEPTH,
             intent: Intent | None = None,
             group_paths: bool = False) -> tuple[list[SummaryRow], int]:
    """Deepest rollup that fits ``budget``, dropping lines only as a last resort.

    Degradation runs in order of what a reader loses least by: first the deepest
    naming that fits with line ranges, then the same ladder without them, and
    only then whole entities. Line ranges used to be unsheddable, so a per-line
    cost that pushed the list over its share silently removed entities instead
    -- an answer that hid *what* changed in order to keep saying *where*.
    """
    for spans in (True, False):
        best: tuple[list[SummaryRow], int] = ([], 1)
        fitted = False
        for depth in range(1, max_depth + 1):
            lines = _rollup_rows(changes, depth, intent=intent,
                                group_paths=group_paths, spans=spans)
            if count_tokens("\n".join(row.text for row in lines)) > budget:
                break
            best, fitted = (lines, depth), True
        if fitted:
            return best

    # Even the shallowest unranged view overflows: keep whichever lines fit,
    # highest ranked first.
    kept: list[SummaryRow] = []
    for line in _rollup_rows(changes, 1, intent=intent,
                            group_paths=group_paths, spans=False):
        if count_tokens("\n".join(row.text for row in [*kept, line])) > budget:
            break
        kept.append(line)
    return kept, 1


def fit_lines(changes: list[EntityChange], budget: int,
              max_depth: int = MAX_DEPTH, intent: Intent | None = None,
              group_paths: bool = False) -> tuple[list[str], int]:
    """String-only compatibility view of the fitted summary rows."""
    rows, depth = fit_rows(changes, budget, max_depth, intent, group_paths)
    return [row.text for row in rows], depth
