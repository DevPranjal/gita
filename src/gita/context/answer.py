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
from .rollup import MAX_DEPTH, fit_lines, rollup_lines
from .tokens import count_tokens

#: A turn costs ~126k tokens of context. Spending a few thousand here to avoid
#: one follow-up call is overwhelmingly profitable.
DEFAULT_BUDGET = 6000

#: Share of the budget reserved for the summary before any hunks are added.
SUMMARY_SHARE = 0.35

#: Detailing hundreds of entities helps nobody and re-parses the same files.
MAX_DETAILED = 20


@dataclass(slots=True)
class Answer:
    text: str
    detailed: list[str] = field(default_factory=list)
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
    return " · ".join(parts) + "\nfiles: " + ", ".join(files)


def _ranked(material: list[EntityChange]) -> list[EntityChange]:
    return sorted(material, key=lambda c: (-score_change(c), c.entity.id))


def _detail(repo: Repo, base: str, head: str | None, change: EntityChange,
            cache: dict) -> str:
    key = (base, head, change.entity.id)
    if key not in cache:
        cache[key] = entity_diff(repo, base, head, change.entity.id)
    patch = cache[key]
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
            respect_raw_diff: bool = True) -> Answer:
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

    summary_budget = max(0, int(budget * SUMMARY_SHARE) - count_tokens(headline))
    lines, _ = fit_lines(material, summary_budget)
    summary = "\n".join([headline, "", *lines]) if lines else headline

    if not detail:
        full_lines = rollup_lines(material, MAX_DEPTH)
        return Answer(text=summary, budget=budget, truncated=lines != full_lines)

    sections: list[str] = []
    detailed: list[str] = []
    cache: dict = {}
    spent = count_tokens(summary)
    skipped = False

    for change in _ranked(material)[:MAX_DETAILED]:
        section = _detail(repo, base, head, change, cache)
        if not section:
            continue
        cost = count_tokens(section)
        if spent + cost > budget:
            skipped = True
            continue  # a later, smaller entity may still fit
        sections.append(section)
        detailed.append(change.entity.id)
        spent += cost

    text = summary if not sections else summary + "\n\n" + "\n".join(sections)
    full_lines = rollup_lines(material, MAX_DEPTH)
    return Answer(text=text, detailed=detailed, budget=budget,
                  truncated=skipped or lines != full_lines)


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
