"""The gita entity model.

An entity is any named thing that can change over time. Code and prose share
this shape -- a repository and a specification are both trees of named things --
so the differ downstream is domain-agnostic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum


class EntityKind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    TYPE = "type"
    ENUM = "enum"
    STRUCT = "struct"
    TRAIT = "trait"
    IMPL = "impl"
    CONSTANT = "constant"

    # document domain (WS-7)
    DOCUMENT = "document"
    SECTION = "section"
    TABLE = "table"


#: Kinds whose body is mostly other entities. A hit on one of these is weaker
#: evidence than a hit on a leaf, so attribution prefers leaves.
CONTAINER_KINDS = frozenset({
    EntityKind.MODULE,
    EntityKind.CLASS,
    EntityKind.INTERFACE,
    EntityKind.TRAIT,
    EntityKind.IMPL,
    EntityKind.DOCUMENT,
    EntityKind.SECTION,
})


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf8", "replace"), digest_size=8).hexdigest()


@dataclass(slots=True)
class Entity:
    """A named thing with a stable identity and three levels of hash.

    The three hashes are what make noise filtering deterministic:

    ``raw_hash``        exact bytes -- any edit at all changes this
    ``content_hash``    comments stripped, whitespace normalised
    ``signature_hash``  as above, but body excluded
    ``body_hash``       as above, but *only* the body -- identity under rename

    raw differs + content identical              => cosmetic change only
    content differs + signature identical        => body change, interface intact
    signature differs                            => interface change, callers at risk
    body identical + id differs                  => moved or renamed
    """

    id: str
    kind: EntityKind
    name: str
    path: str
    start_line: int
    end_line: int
    raw_hash: str
    content_hash: str
    signature_hash: str
    body_hash: str = ""
    body_size: int = 0
    signature: str = ""
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    tokens: tuple[str, ...] = ()
    synthetic: bool = False

    @property
    def is_container(self) -> bool:
        return self.kind in CONTAINER_KINDS

    @property
    def qualname(self) -> str:
        """Entity path without the file prefix: ``Parent::child``."""
        return self.id.split("::", 1)[1] if "::" in self.id else self.name

    @property
    def depth(self) -> int:
        return self.id.count("::")

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def __str__(self) -> str:
        return f"{self.id} [{self.kind.value}]"


@dataclass(slots=True)
class EntityTree:
    """All entities extracted from one revision of one file."""

    path: str
    language: str
    root_id: str
    entities: dict[str, Entity] = field(default_factory=dict)
    parse_error: bool = False

    def add(self, entity: Entity) -> None:
        self.entities[entity.id] = entity
        if entity.parent_id is not None:
            parent = self.entities.get(entity.parent_id)
            if parent is not None:
                parent.children.append(entity.id)

    def get(self, entity_id: str) -> Entity | None:
        return self.entities.get(entity_id)

    @property
    def root(self) -> Entity | None:
        return self.entities.get(self.root_id)

    def walk(self):
        return iter(self.entities.values())

    def leaves(self):
        return (e for e in self.entities.values() if not e.children)

    def enclosing(self, line: int) -> Entity | None:
        """Innermost entity containing ``line``; leaves win over containers.

        This is the Spike A operation -- 94.0% coverage, 0.01% miss rate.
        """
        best: Entity | None = None
        for entity in self.entities.values():
            if entity.start_line <= line <= entity.end_line:
                if best is None:
                    best = entity
                    continue
                if entity.depth > best.depth or (best.is_container and not entity.is_container):
                    best = entity
        return best

    def __len__(self) -> int:
        return len(self.entities)
