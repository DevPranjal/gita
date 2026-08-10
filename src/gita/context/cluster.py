"""Clustering: group entity changes into one logical change.

Deterministic grouping by enclosing top-level entity. WS-3 may later relabel a
cluster with an intent line, but the membership is decided here, without a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diff.changes import EntityChange
from .rank import score_change


@dataclass(slots=True)
class Cluster:
    path: str
    title: str
    changes: list[EntityChange] = field(default_factory=list)

    @property
    def score(self) -> float:
        return max((score_change(c) for c in self.changes), default=0.0)

    @property
    def weight(self) -> float:
        return sum(score_change(c) for c in self.changes)

    @property
    def id(self) -> str:
        return f"{self.path}::{self.title}"

    def __len__(self) -> int:
        return len(self.changes)


def head_of(change: EntityChange, depth: int = 1) -> str:
    """First ``depth`` segments of an entity's path within its file."""
    return "::".join(change.entity.qualname.split("::")[:depth])


def cluster_changes(changes: list[EntityChange]) -> list[Cluster]:
    clusters: dict[tuple[str, str], Cluster] = {}

    for change in changes:
        if change.is_noise:
            continue
        key = (change.entity.path, head_of(change))
        cluster = clusters.get(key)
        if cluster is None:
            cluster = Cluster(path=key[0], title=key[1])
            clusters[key] = cluster
        cluster.changes.append(change)

    return sorted(clusters.values(), key=lambda c: (-c.score, -c.weight, c.path, c.title))
