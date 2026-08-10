"""Per-language entity extraction rules.

Spike A finding 1: the entity model must be defined over **bindings, not syntax
categories**. In JS/TS most code lives in function *values* -- callbacks, arrow
functions, object methods -- none of which are declarations. Recognising only
declarations cost ~65 points of attribution coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import EntityKind


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    name: str
    extensions: tuple[str, ...]
    kinds: dict[str, EntityKind]
    comment_nodes: frozenset[str]
    #: Functions with no name of their own; named from their binding site.
    anonymous_nodes: frozenset[str] = frozenset()
    #: Node fields excluded from the signature hash.
    body_fields: tuple[str, ...] = ("body",)

    @property
    def entity_nodes(self) -> frozenset[str]:
        return frozenset(self.kinds)


_JS_ANON = frozenset({
    "arrow_function",
    "function_expression",
    "generator_function",
})

_JS_KINDS: dict[str, EntityKind] = {
    "function_declaration": EntityKind.FUNCTION,
    "generator_function_declaration": EntityKind.FUNCTION,
    "class_declaration": EntityKind.CLASS,
    "method_definition": EntityKind.METHOD,
    **{node: EntityKind.FUNCTION for node in _JS_ANON},
}

_TS_KINDS: dict[str, EntityKind] = {
    **_JS_KINDS,
    "abstract_class_declaration": EntityKind.CLASS,
    "interface_declaration": EntityKind.INTERFACE,
    "type_alias_declaration": EntityKind.TYPE,
    "enum_declaration": EntityKind.ENUM,
    "module": EntityKind.MODULE,
}

_JS_COMMENTS = frozenset({"comment", "html_comment"})

SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="python",
        extensions=(".py", ".pyi"),
        kinds={
            "function_definition": EntityKind.FUNCTION,
            "class_definition": EntityKind.CLASS,
        },
        comment_nodes=frozenset({"comment"}),
    ),
    LanguageSpec(
        name="javascript",
        extensions=(".js", ".mjs", ".cjs", ".jsx"),
        kinds=_JS_KINDS,
        comment_nodes=_JS_COMMENTS,
        anonymous_nodes=_JS_ANON,
    ),
    LanguageSpec(
        name="typescript",
        extensions=(".ts", ".mts", ".cts"),
        kinds=_TS_KINDS,
        comment_nodes=_JS_COMMENTS,
        anonymous_nodes=_JS_ANON,
    ),
    LanguageSpec(
        name="tsx",
        extensions=(".tsx",),
        kinds=_TS_KINDS,
        comment_nodes=_JS_COMMENTS,
        anonymous_nodes=_JS_ANON,
    ),
    LanguageSpec(
        name="go",
        extensions=(".go",),
        kinds={
            "function_declaration": EntityKind.FUNCTION,
            "method_declaration": EntityKind.METHOD,
            "type_declaration": EntityKind.TYPE,
            "func_literal": EntityKind.FUNCTION,
        },
        comment_nodes=frozenset({"comment"}),
        anonymous_nodes=frozenset({"func_literal"}),
    ),
    LanguageSpec(
        name="rust",
        extensions=(".rs",),
        kinds={
            "function_item": EntityKind.FUNCTION,
            "struct_item": EntityKind.STRUCT,
            "enum_item": EntityKind.ENUM,
            "trait_item": EntityKind.TRAIT,
            "impl_item": EntityKind.IMPL,
            "mod_item": EntityKind.MODULE,
            "union_item": EntityKind.STRUCT,
            "macro_definition": EntityKind.FUNCTION,
            "type_item": EntityKind.TYPE,
        },
        comment_nodes=frozenset({"line_comment", "block_comment"}),
        body_fields=("body", "declaration_list"),
    ),
)

BY_NAME: dict[str, LanguageSpec] = {spec.name: spec for spec in SPECS}
BY_EXTENSION: dict[str, LanguageSpec] = {
    ext: spec for spec in SPECS for ext in spec.extensions
}


def for_path(path: str) -> LanguageSpec | None:
    dot = path.rfind(".")
    return BY_EXTENSION.get(path[dot:].lower()) if dot != -1 else None


def is_supported(path: str) -> bool:
    return for_path(path) is not None
