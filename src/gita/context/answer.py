"""One-shot answers: everything the agent needs, in a single call.

The measurement that drove this: gita cost about +1.25 turns per task, and a turn
is roughly 126,000 tokens of re-sent context. gita's entire output is ~1,500.
Output is therefore ~100x cheaper than a turn, and progressive disclosure -- which
optimises bytes per call -- was buying pennies while spending pounds.

So this module inverts the default. Rather than making the agent drill, it spends
tokens up front: headline, files, entities, and the actual hunks for the entities
that matter, all in one response. Two guarantees keep that safe:

* the output never exceeds the caller's budget, and
* the output never exceeds what a raw `git diff` would have cost.

The second is what makes gita safe to adopt: at worst it is as expensive as the
thing it replaces, and it is usually far cheaper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..diff.changes import ChangeKind, ChangeSet, EntityChange
from ..vcs.git import Repo
from .layers import fit_text
from .patch import DisplayDiffs, change_diff, display_change_diffs, entity_diff
from .rank import Intent, score_change
from .references import unreferenced
from .rollup import MAX_DEPTH, SummaryRow, fit_rows, rollup_lines
from .tokens import count_tokens

#: A turn costs ~126k tokens of context. Spending a few thousand here to avoid
#: one follow-up call is overwhelmingly profitable.
DEFAULT_BUDGET = 6000

#: Share of the budget reserved for the summary before any hunks are added.
SUMMARY_SHARE = 0.35

#: Detailing hundreds of entities helps nobody and re-parses the same files.
MAX_DETAILED = 200

#: Beyond a handful the list stops being a finding and becomes a wall.
MAX_ORPHANS_SHOWN = 5


@dataclass(slots=True)
class Answer:
    text: str
    detailed: list[str] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)
    truncated: bool = False
    budget: int = 0

    @property
    def tokens(self) -> int:
        return count_tokens(self.text)


@dataclass(slots=True)
class PatchAnswer:
    patch: str
    budget: int
    requested_budget: int
    detailed: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)

    @property
    def status(self) -> Literal["complete", "partial", "nothing_fits"]:
        if not self.omitted:
            return "complete"
        return "partial" if self.patch else "nothing_fits"

    @property
    def truncated(self) -> bool:
        return bool(self.omitted)

    @property
    def tokens(self) -> int:
        return count_tokens(self.patch)


def _headline(changeset: ChangeSet, material: list[EntityChange],
              worktree: bool = False) -> str:
    if not material:
        if changeset.files_changed == 0:
            base = "nothing to compare: no files differ between these revisions"
            # Without a next step the agent has to guess, and a guess costs a turn.
            if worktree:
                return ("working tree is clean, nothing uncommitted to review\n"
                        "to review the last commit: gita diff HEAD^ HEAD")
            return base
        return f"no material changes ({changeset.files_changed} files, all noise)"

    files = sorted({c.entity.path for c in material})
    interface = sum(1 for c in material if c.affects_interface)
    noise = len(changeset) - len(material)

    parts = [f"{len(files)} files", f"{len(material)} changes"]
    if interface:
        parts.append(f"{interface} interface")
    if noise:
        parts.append(f"{noise} noise filtered")

    # File names matter: an entity list alone cannot answer "which files changed",
    # which cost real recall in iteration 2.
    # ASCII only: a middle dot in this line broke a piped Windows shell.
    return " | ".join(parts) + "\nfiles: " + ", ".join(_named(changeset, files, worktree))


#: Mirrors `git status --short`, which agents ran straight after `gita diff`
#: on every repetition because we did not say which files were new.
_STATUS = {"A": "new", "?": "untracked", "M": "modified",
           "D": "deleted", "R": "renamed"}


def _named(changeset: ChangeSet, files: list[str], worktree: bool) -> list[str]:
    if not worktree:
        return files
    return [f"{path} ({_STATUS[status]})"
            if (status := changeset.file_status.get(path, "")) in _STATUS
            else path
            for path in files]


_ORPHAN_PREFIX = "unreferenced (name appears nowhere else): "


def _orphan_line(orphans: list[str], spare: int) -> str:
    """The finding, trimmed to whatever budget is left, or nothing."""
    if not orphans or spare <= 0:
        return ""
    for count in range(min(len(orphans), MAX_ORPHANS_SHOWN), 0, -1):
        line = _ORPHAN_PREFIX + ", ".join(orphans[:count])
        if count_tokens(line) <= spare:
            return line
    return ""


def _ranked_details(plan: DisplayDiffs, lookup: dict[str, EntityChange],
                    intent: Intent | None = None) -> list[EntityChange]:
    scores = {owner: score_change(lookup[owner], intent) for owner in plan.patches}
    for entity_id, owners in plan.required.items():
        for owner in owners:
            scores[owner] = max(scores[owner], score_change(lookup[entity_id], intent))
    return sorted((lookup[owner] for owner in plan.patches),
                  key=lambda c: (-scores[c.id], c.id))


#: Detail below this is not worth reserving budget for -- a couple of context
#: lines with nowhere to sit.
MIN_USEFUL_DETAIL = 80


def _notice(names_hidden: int, hunks_hidden: int = 0,
            can_raise_budget: bool = True) -> str:
    """Name what is missing and the cheap way to reach it.

    An answer that stops early without saying so is worse than a short one: the
    agent cannot tell a small changeset from a cut one, so it recovers with the
    only tool it is certain of. Measured, that recovery was
    `git diff -- <biggest file>` -- 214,918 tokens against a 6,000-token budget.
    Saying what is missing, and which gita call reaches it, costs about twenty.

    When the raw diff is what caps the answer, raising --budget cannot help, so
    offering it would spend a turn to learn nothing.
    """
    parts = []
    if names_hidden > 0:
        parts.append(f"{names_hidden} more changed entities not listed")
    if hunks_hidden > 0:
        parts.append(f"code not shown for {hunks_hidden}")
    if not parts:
        parts.append("output cut to fit")
    routes = "`gita show ID` for one entity, `gita diff --filter PATH` to narrow"
    if can_raise_budget:
        routes += ", or raise --budget"
    return f"[{'; '.join(parts)}. {routes}]"


def _render_sections(sections: list[str]) -> str:
    """Group by file priority; subsequent headers inherit the explicit path."""
    by_path: dict[str, list[tuple[str, str, str]]] = {}
    for section in sections:
        header, _, body = section.partition("\n")
        kind, entity_id = header[4:].split(" ", 1)
        path, _, qualname = entity_id.partition("::")
        by_path.setdefault(path, []).append((kind, qualname, body))
    rendered = []
    for path, group in by_path.items():
        for index, (kind, qualname, body) in enumerate(group):
            entity_id = (path + (f"::{qualname}" if qualname else "")
                         if index == 0 else f"::{qualname or '<module>'}")
            rendered.append(f"--- {kind} {entity_id}\n{body}")
    return "\n".join(rendered)


def _assemble(summary: str, sections: list[str], notice: str = "") -> str:
    """The text exactly as it will be emitted -- what the budget is measured on."""
    text = summary if not sections else summary + "\n\n" + _render_sections(sections)
    return text + "\n\n" + notice if notice else text


def _summary_budget(budget: int, headline: int) -> int:
    """Naming what changed outranks showing it.

    The share is there to leave room for hunks. When the leftover could not buy
    a useful hunk anyway, spend it on the summary: an answer that says
    "3 changes" and then lists none of them is not an answer.
    """
    remaining = max(0, budget - headline)
    share = max(0, int(budget * SUMMARY_SHARE) - headline)
    return remaining if remaining - share < MIN_USEFUL_DETAIL else share


def _detail(repo: Repo, base: str, head: str | None, change: EntityChange,
            cache: dict, patch: str | None = None) -> str:
    if patch is None:
        patch = change_diff(repo, base, head, change, cache=cache)
    if not patch:
        return ""

    # difflib repeats `--- a/x` and `+++ b/x` per entity. Our own header already
    # names the entity, and on small diffs those two lines cost more than the
    # change itself -- enough to make the output larger than plain git.
    body = patch.splitlines()
    while body and (body[0].startswith("--- ") or body[0].startswith("+++ ")):
        body.pop(0)
    if not body:
        return ""

    hunks = [line for line in body if line.startswith("@@")]
    comment_only = hunks and all(
        line.endswith(("added comment", "removed comment", "changed comment")) for line in hunks)
    label = "comments" if comment_only else change.kind.value
    return f"--- {label} {change.entity.id}\n" + "\n".join(body) + "\n"


def _summary_lines(material: list[EntityChange], headline: str, budget: int,
                   summary_budget: int, orphans: list[str],
                   intent: Intent | None = None
                   ) -> tuple[list[SummaryRow], str, str]:
    """Entity lines and the assembled summary, inside both the share and the cap."""
    rows, _ = fit_rows(material, summary_budget, intent=intent, group_paths=True)
    # The blank line between headline and entities costs a token of its own, so
    # the assembled text is what gets measured, never the sum of its parts.
    while rows and count_tokens("\n".join([headline, "", *(row.text for row in rows)])) > budget:
        rows.pop()
    lines = [row.text for row in rows]

    # "Is this wired in?" is the first question asked of an addition. Without an
    # answer the agent reaches for git grep or a wider diff. It is budgeted like
    # everything else: appending it unmeasured made `--budget 120` emit 211.
    spare = budget - count_tokens("\n".join([headline, "", *lines]))
    shown = _orphan_line(orphans, spare)
    display_lines = [*lines, shown] if shown else lines
    summary = "\n".join([headline, "", *display_lines]) if display_lines else headline
    return rows, summary, shown


def compose(repo: Repo, base: str, head: str | None, changeset: ChangeSet,
            budget: int = DEFAULT_BUDGET, detail: bool = True,
            respect_raw_diff: bool = True,
            check_references: bool = True,
            intent: Intent | None = None) -> Answer:
    """A complete answer in one call, within budget and never costlier than git."""
    material = changeset.material()
    material_ids = {c.id for c in material}
    worktree = head is None or head == ""
    capped_by_raw = False

    if respect_raw_diff and material:
        raw = repo.raw_diff(base, head, changeset.paths())
        raw_tokens = count_tokens(raw)
        if raw_tokens:
            capped_by_raw = raw_tokens < budget
            budget = min(budget, raw_tokens)

    headline = fit_text(_headline(changeset, material, worktree), budget)
    if not material or budget <= 0:
        return Answer(text=headline, budget=budget,
                      truncated=headline != _headline(changeset, material, worktree))

    orphans = unreferenced(repo, changeset) if check_references else []
    # A brief answer has no hunks to protect, so reserving a share for them
    # would cap the summary below the budget the caller actually granted.
    summary_budget = (_summary_budget(budget, count_tokens(headline)) if detail
                      else max(0, budget - count_tokens(headline)))
    rows, summary, shown = _summary_lines(material, headline, budget,
                                          summary_budget, orphans, intent=intent)
    lost_orphans = bool(orphans) and not shown

    if not detail:
        summary_ids = {key for row in rows for key in row.entity_ids}
        names_hidden = len(material_ids - summary_ids)
        cut = bool(names_hidden) or lost_orphans
        text = summary
        if cut:
            candidate = _assemble(summary, [],
                                  _notice(names_hidden,
                                          can_raise_budget=not capped_by_raw))
            if count_tokens(candidate) <= budget:
                text = candidate
        return Answer(text=text, unreferenced=orphans, budget=budget,
                      truncated=cut)

    sections: list[str] = []
    detailed: list[str] = []
    cache: dict = {}
    plan = display_change_diffs(repo, base, head, list(changeset), cache=cache)
    lookup = {change.id: change for change in changeset}
    shown_details: set[str] = set()
    attempted = 0

    def assembled(extra: str) -> str:
        return _assemble(summary, [*sections, extra])

    for change in _ranked_details(plan, lookup, intent):
        if not plan.patches[change.id]:
            continue
        if attempted >= MAX_DETAILED:
            break
        attempted += 1
        section = _detail(repo, base, head, change, cache, plan.patches[change.id])
        # Measure the text as it will actually be emitted. Summing the parts
        # missed the separator between them, which put one answer one token
        # over the raw diff it promises never to exceed.
        if count_tokens(assembled(section)) > budget:
            continue  # a later, smaller entity may still fit
        sections.append(section)
        detailed.append(change.entity.id)
        shown_details.add(change.id)

    full_lines = rollup_lines(material, MAX_DEPTH, intent=intent, group_paths=True)
    # Detail almost never spends its whole reservation, and an unspent
    # reservation is an answer that stopped short of its own budget: adding line
    # ranges once cut a 176-token answer to 133 against the same 186-token cap,
    # withholding two of four entity names it had room for. SUMMARY_SHARE exists
    # to stop names crowding out hunks, so once the hunks are chosen, whatever
    # they declined belongs to the entity list.
    if [row.text for row in rows] != full_lines:
        # Reserve the largest notice this answer could need, so re-flowing buys
        # names with genuinely spare budget rather than out of a hunk.
        reserve = count_tokens(_notice(len(full_lines), len(plan.required),
                                       can_raise_budget=not capped_by_raw))
        spare = budget - count_tokens(_assemble(summary, sections)) - reserve
        if spare > 0:
            grown, grown_summary, grown_shown = _summary_lines(
                material, headline, budget, summary_budget + spare, orphans,
                intent=intent)
            if (len(grown) >= len(rows) and grown != rows
                    and count_tokens(_assemble(grown_summary, sections)) <= budget):
                rows, summary, shown = grown, grown_summary, grown_shown
                lost_orphans = bool(orphans) and not shown

    summary_ids = {key for row in rows for key in row.entity_ids}
    # An empty hunk requirement covers no source, but cannot announce a relocation.
    metadata_only_moves = {c.id for c in material
                           if c.kind is ChangeKind.MOVED and not plan.required.get(c.id)}

    def omissions() -> tuple[set[str], set[str]]:
        code_missing = plan.required.keys() - plan.covered(shown_details)
        names_missing = ((code_missing & material_ids) | metadata_only_moves) - summary_ids
        return names_missing, code_missing

    names_missing, code_missing = omissions()
    # A cap that is not the budget must still report itself. MAX_DETAILED used
    # to drop entities silently, so an answer could spend a third of its budget,
    # withhold fourteen of thirty-four changes, and still claim to be complete.
    truncated = bool(names_missing or code_missing) or lost_orphans
    text = _assemble(summary, sections)

    if truncated:
        # Buy the notice with detail, never by exceeding the cap: appending an
        # unmeasured line is what made `--budget 120` emit 211 tokens in v1.0.0.
        while True:
            notice = _notice(len(names_missing), len(code_missing),
                             can_raise_budget=not capped_by_raw)
            if (not sections
                    or count_tokens(_assemble(summary, sections, notice)) <= budget):
                break
            sections.pop()
            shown_details.remove(detailed.pop())
            names_missing, code_missing = omissions()
        text = _assemble(summary, sections)
        if count_tokens(_assemble(summary, sections, notice)) <= budget:
            text = _assemble(summary, sections, notice)

    return Answer(text=text, detailed=detailed, unreferenced=orphans,
                  budget=budget, truncated=truncated)


def material_patch(repo: Repo, base: str, head: str | None,
                   changeset: ChangeSet, budget: int = DEFAULT_BUDGET,
                   respect_raw_diff: bool = True) -> PatchAnswer:
    """Budgeted patch content with explicit emission coverage.

    Coverage follows shared ownership: a child represented by a parent hunk
    is omitted only if that hunk was withheld. Metadata is outside the patch budget.
    """
    requested_budget = budget
    if respect_raw_diff:
        raw_tokens = count_tokens(repo.raw_diff(base, head, changeset.paths()))
        if raw_tokens:
            budget = min(budget, raw_tokens)

    cache: dict = {}
    parts: list[str] = []
    detailed: list[str] = []
    plan = display_change_diffs(repo, base, head, list(changeset), cache=cache)
    lookup = {change.id: change for change in changeset}

    for change in _ranked_details(plan, lookup):
        patch = _detail(repo, base, head, change, cache, plan.patches[change.id])
        if not patch:
            continue
        if count_tokens(_render_sections([*parts, patch])) > budget:
            continue
        parts.append(patch)
        detailed.append(change.id)

    omitted = sorted(plan.required.keys() - plan.covered(set(detailed)))
    return PatchAnswer(_render_sections(parts), budget, requested_budget,
                       detailed=detailed, omitted=omitted)
