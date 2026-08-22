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

from ..diff.changes import ChangeSet, EntityChange
from ..vcs.git import Repo
from .layers import fit_text
from .patch import entity_diff
from .rank import score_change
from .references import unreferenced
from .rollup import MAX_DEPTH, fit_lines, rollup_lines
from .tokens import count_tokens

#: A turn costs ~126k tokens of context. Spending a few thousand here to avoid
#: one follow-up call is overwhelmingly profitable.
DEFAULT_BUDGET = 6000

#: Share of the budget reserved for the summary before any hunks are added.
SUMMARY_SHARE = 0.35

#: Detailing hundreds of entities helps nobody and re-parses the same files.
MAX_DETAILED = 20

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


def _ranked(material: list[EntityChange]) -> list[EntityChange]:
    return sorted(material, key=lambda c: (-score_change(c), c.entity.id))


#: Detail below this is not worth reserving budget for -- a couple of context
#: lines with nowhere to sit.
MIN_USEFUL_DETAIL = 80


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
            cache: dict) -> str:
    patch = entity_diff(repo, base, head, change.entity.id, cache=cache)
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

    return f"--- {change.entity.id}  [{change.kind.value}]\n" + "\n".join(body) + "\n"


def compose(repo: Repo, base: str, head: str | None, changeset: ChangeSet,
            budget: int = DEFAULT_BUDGET, detail: bool = True,
            respect_raw_diff: bool = True,
            check_references: bool = True) -> Answer:
    """A complete answer in one call, within budget and never costlier than git."""
    material = changeset.material()
    worktree = head is None or head == ""

    if respect_raw_diff and material:
        raw = repo.raw_diff(base, head, changeset.paths())
        raw_tokens = count_tokens(raw)
        if raw_tokens:
            budget = min(budget, raw_tokens)

    headline = fit_text(_headline(changeset, material, worktree), budget)
    if not material or budget <= 0:
        return Answer(text=headline, budget=budget,
                      truncated=headline != _headline(changeset, material, worktree))

    summary_budget = _summary_budget(budget, count_tokens(headline))
    lines, _ = fit_lines(material, summary_budget)
    # The blank line between headline and entities costs a token of its own, so
    # the assembled text is what gets measured, never the sum of its parts.
    while lines and count_tokens("\n".join([headline, "", *lines])) > budget:
        lines.pop()

    # "Is this wired in?" is the first question asked of an addition. Without an
    # answer the agent reaches for git grep or a wider diff. It is budgeted like
    # everything else: appending it unmeasured made `--budget 120` emit 211.
    orphans = unreferenced(repo, changeset) if check_references else []
    spare = budget - count_tokens("\n".join([headline, "", *lines]))
    shown = _orphan_line(orphans, spare)
    if shown:
        lines.append(shown)

    summary = "\n".join([headline, "", *lines]) if lines else headline
    lost_orphans = bool(orphans) and not shown

    if not detail:
        full_lines = rollup_lines(material, MAX_DEPTH)
        return Answer(text=summary, unreferenced=orphans, budget=budget,
                      truncated=lines != full_lines or lost_orphans)

    sections: list[str] = []
    detailed: list[str] = []
    cache: dict = {}
    skipped = False

    def assembled(extra: str) -> str:
        return summary + "\n\n" + "\n".join([*sections, extra])

    for change in _ranked(material)[:MAX_DETAILED]:
        section = _detail(repo, base, head, change, cache)
        if not section:
            continue
        # Measure the text as it will actually be emitted. Summing the parts
        # missed the separator between them, which put one answer one token
        # over the raw diff it promises never to exceed.
        if count_tokens(assembled(section)) > budget:
            skipped = True
            continue  # a later, smaller entity may still fit
        sections.append(section)
        detailed.append(change.entity.id)

    text = summary if not sections else summary + "\n\n" + "\n".join(sections)
    full_lines = rollup_lines(material, MAX_DEPTH)
    return Answer(text=text, detailed=detailed, unreferenced=orphans,
                  budget=budget,
                  truncated=skipped or lines != full_lines or lost_orphans)


def material_patch(repo: Repo, base: str, head: str | None,
                   changeset: ChangeSet, budget: int = DEFAULT_BUDGET,
                   respect_raw_diff: bool = True) -> str:
    """A unified diff containing only material changes.

    The lowest-friction way to save an agent tokens is to hand it the format it
    already reads, minus the noise. No new syntax, no drilling, no extra turn.
    """
    if respect_raw_diff:
        raw_tokens = count_tokens(repo.raw_diff(base, head, changeset.paths()))
        if raw_tokens:
            budget = min(budget, raw_tokens)

    cache: dict = {}
    parts: list[str] = []
    spent = 0

    for change in _ranked(changeset.material()):
        patch = _detail(repo, base, head, change, cache)
        if not patch:
            continue
        cost = count_tokens(patch)
        if spent + cost > budget:
            break
        parts.append(patch)
        spent += cost

    return "\n".join(parts)
