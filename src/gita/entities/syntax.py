"""Byte-preserving declaration projection for Dart's separate signature/body nodes."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class SyntaxNode(Protocol):
    @property
    def type(self) -> str: ...
    @property
    def id(self) -> int: ...
    @property
    def text(self) -> bytes | None: ...
    @property
    def start_byte(self) -> int: ...
    @property
    def end_byte(self) -> int: ...
    @property
    def start_point(self) -> Sequence[int]: ...
    @property
    def end_point(self) -> Sequence[int]: ...
    @property
    def parent(self) -> "SyntaxNode | None": ...
    @property
    def children(self) -> Sequence["SyntaxNode"]: ...
    @property
    def named_children(self) -> Sequence["SyntaxNode"]: ...
    @property
    def child_count(self) -> int: ...
    @property
    def is_named(self) -> bool: ...
    def child_by_field_name(self, name: str) -> "SyntaxNode | None": ...
    def children_by_field_name(self, name: str) -> Sequence["SyntaxNode"]: ...


_DART_SIGNATURES = frozenset({
    "function_signature", "getter_signature", "setter_signature", "constructor_signature",
})


@dataclass(frozen=True, slots=True)
class DartCallable:
    header: SyntaxNode
    body: SyntaxNode
    between: tuple[SyntaxNode, ...] = ()

    @property
    def type(self) -> str:
        return "gita_callable"

    @property
    def id(self) -> int:
        return self.header.id

    @property
    def parent(self) -> SyntaxNode | None:
        return self.header.parent

    @property
    def start_byte(self) -> int:
        return self.header.start_byte

    @property
    def end_byte(self) -> int:
        return self.body.end_byte

    @property
    def start_point(self) -> Sequence[int]:
        return self.header.start_point

    @property
    def end_point(self) -> Sequence[int]:
        return self.body.end_point

    @property
    def text(self) -> bytes:
        parent = self.parent
        if parent is None:
            raise ValueError("Dart callable must have a containing source node")
        raw = parent.text
        if raw is None:
            raise ValueError("Dart callable source bytes are unavailable")
        return raw[self.start_byte - parent.start_byte:self.end_byte - parent.start_byte]

    @property
    def children(self) -> Sequence[SyntaxNode]:
        return (self.header, *self.between, self.body)

    @property
    def named_children(self) -> Sequence[SyntaxNode]:
        return tuple(child for child in self.children if child.is_named)

    @property
    def child_count(self) -> int:
        return len(self.children)

    @property
    def is_named(self) -> bool:
        return True

    @property
    def signature(self) -> SyntaxNode:
        if self.header.type == "method_signature":
            return next(child for child in self.header.named_children if child.type in _DART_SIGNATURES)
        return self.header

    def child_by_field_name(self, name: str) -> SyntaxNode | None:
        return self.body if name == "body" else self.signature.child_by_field_name(name)

    def children_by_field_name(self, name: str) -> Sequence[SyntaxNode]:
        return (self.body,) if name == "body" else self.signature.children_by_field_name(name)


def children(node: SyntaxNode, language: str) -> Sequence[SyntaxNode]:
    raw = node.children
    if language != "dart" or isinstance(node, DartCallable):
        return raw
    projected: list[SyntaxNode] = []
    i = 0
    while i < len(raw):
        header = raw[i]
        if (header.type in _DART_SIGNATURES
                or header.type == "method_signature"
                and any(c.type in _DART_SIGNATURES for c in header.named_children)):
            j = i + 1
            while j < len(raw) and raw[j].type in ("comment", "documentation_comment"):
                j += 1
            if j < len(raw) and raw[j].type == "function_body":
                projected.append(DartCallable(header, raw[j], tuple(raw[i + 1:j])))
                i = j + 1
                continue
        projected.append(header)
        i += 1
    return projected
