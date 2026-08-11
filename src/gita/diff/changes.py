"""Change classification -- the vocabulary of a context diff."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from ..entities.model import Entity


class ChangeKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    RENAMED = "renamed"
    MOVED = "moved"
    SIGNATURE_CHANGED = "signature_changed"
    BODY_CHANGED = "body_changed"
    COSMETIC = "cosmetic"
    UNCHANGED = "unchanged"


#: Changes an agent can safely never see unless it asks.
NOISE_KINDS = frozenset({ChangeKind.UNCHANGED, ChangeKind.COSMETIC})

#: Changes that can break a caller.
INTERFACE_KINDS = frozenset({
    ChangeKind.ADDED,
    ChangeKind.REMOVED,
    ChangeKind.RENAMED,
    ChangeKind.SIGNATURE_CHANGED,
})


@dataclass(slots=True)
class EntityChange:
    kind: ChangeKind
    current: Entity | None = None
    previous: Entity | None = None
    signature_changed: bool = False
    body_changed: bool = False
    similarity: float | None = None

    @property
    def entity(self) -> Entity:
        entity = self.current or self.previous
        assert entity is not None, "a change must reference at least one entity"
        return entity

    @property
    def id(self) -> str:
        return self.entity.id

    @property
    def is_noise(self) -> bool:
        return self.kind in NOISE_KINDS

    @property
    def affects_interface(self) -> bool:
        return self.kind in INTERFACE_KINDS or self.signature_changed

    def __str__(self) -> str:
        if self.kind is ChangeKind.RENAMED and self.previous and self.current:
            return f"{self.previous.qualname} -> {self.current.qualname}  [renamed]"
        if self.kind is ChangeKind.MOVED and self.previous and self.current:
            return f"{self.previous.path} -> {self.current.path}::{self.current.qualname}  [moved]"
        return f"{self.entity.id}  [{self.kind.value}]"


@dataclass(slots=True)
class ChangeSet:
    """Every entity change between two revisions -- the fact container.

    WS-2 extends this with resolution edges (callers, blast radius).
    """

    changes: list[EntityChange] = field(default_factory=list)
    files_changed: int = 0
    files_skipped: int = 0
    parse_errors: int = 0
    #: path -> git status letter, so "what is the state of my tree" needs no
    #: second command. `?` is untracked, as in `git status --short`.
    file_status: dict[str, str] = field(default_factory=dict)

    def add(self, change: EntityChange) -> None:
        self.changes.append(change)

    def extend(self, changes: list[EntityChange]) -> None:
        self.changes.extend(changes)

    def material(self) -> list[EntityChange]:
        """Everything except unchanged and cosmetic-only edits."""
        return [c for c in self.changes if not c.is_noise]

    def interface_changes(self) -> list[EntityChange]:
        return [c for c in self.changes if c.affects_interface]

    def by_kind(self, kind: ChangeKind) -> list[EntityChange]:
        return [c for c in self.changes if c.kind is kind]

    def counts(self) -> dict[str, int]:
        return dict(Counter(c.kind.value for c in self.changes))

    def paths(self) -> list[str]:
        return sorted({c.entity.path for c in self.material()})

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self):
        return iter(self.changes)
