"""L0/L1 assembly.

L0 is a deterministic headline built from facts alone. When WS-3 lands, the SLM
*upgrades* this line with intent rather than enabling it -- which is the
graceful-degradation property of SCOPE.md section 2, made visible: unplug the
model and gita still answers, just more bluntly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diff.changes import ChangeSet, EntityChange
from .cluster import Cluster, cluster_changes
from .rollup import MAX_DEPTH, fit_lines, rollup_lines
from .tokens import count_tokens

#: L0 never eats more than this share of the budget.
L0_SHARE = 0.5

_MAX_HEADLINE_TITLES = 3


@dataclass(slots=True)
class ContextView:
    l0: str
    l1: str
    clusters: list[Cluster] = field(default_factory=list)
    depth: int = 1
    truncated: bool = False
    budget: int = 0

    @property
    def tokens(self) -> int:
        return count_tokens(self.l0) + count_tokens(self.l1)

    def render(self) -> str:
        return f"{self.l0}\n\n{self.l1}" if self.l1 else self.l0


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def fit_text(text: str, budget: int) -> str:
    """Trim ``text`` to fit ``budget``, word by word.

    L0 used to be emitted unconditionally, so any budget below its own cost was
    silently blown. A budget an agent cannot rely on is not a budget.
    """
    if budget <= 0 or not text:
        return ""
    if count_tokens(text) <= budget:
        return text

    kept: list[str] = []
    for word in text.split():
        if count_tokens(" ".join([*kept, word])) > budget:
            break
        kept.append(word)
    return " ".join(kept)


def _headline(changeset: ChangeSet, material: list[EntityChange],
              clusters: list[Cluster], budget: int, focus: str | None = None) -> str:
    prefix = f"{focus} · " if focus else ""

    if not material:
        return f"{prefix}no material changes"

    files = len({c.entity.path for c in material})
    interface = sum(1 for c in material if c.affects_interface)
    noise = len(changeset) - len(material)

    parts = [_plural(files, "file"), _plural(len(material), "change")]
    if interface:
        parts.append(f"{interface} interface")
    if noise:
        parts.append(f"{noise} noise filtered")
    headline = prefix + " · ".join(parts)

    titles = ", ".join(c.title for c in clusters[:_MAX_HEADLINE_TITLES])
    if not titles:
        return headline

    candidate = f"{headline}\ntop: {titles}"
    if count_tokens(candidate) <= max(1, int(budget * L0_SHARE)):
        return candidate
    return headline


def build_view(changeset: ChangeSet, budget: int = 1000,
               focus: str | None = None) -> ContextView:
    """Assemble a budgeted L0 + L1 view of a ChangeSet."""
    material = changeset.material()
    clusters = cluster_changes(list(changeset))

    l0_full = _headline(changeset, material, clusters, budget, focus)
    l0 = fit_text(l0_full, budget)
    remaining = max(0, budget - count_tokens(l0))

    lines, depth = fit_lines(material, remaining)
    full = rollup_lines(material, MAX_DEPTH)

    return ContextView(
        l0=l0,
        l1="\n".join(lines),
        clusters=clusters,
        depth=depth,
        truncated=lines != full or l0 != l0_full,
        budget=budget,
    )
