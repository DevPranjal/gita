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
    #: Child node types to read a name from, when no name field exists.
    name_children: tuple[str, ...] = ()
    #: Derived from ``kinds``; a field rather than a property because it was
    #: rebuilt 600,000 times per diff when the traversal asked for it per node.
    entity_nodes: frozenset[str] = field(default=frozenset(), init=False,
                                         compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_nodes", frozenset(self.kinds))


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
        name="java", extensions=(".java",),
        kinds={
            "class_declaration": EntityKind.CLASS, "interface_declaration": EntityKind.INTERFACE,
            "enum_declaration": EntityKind.ENUM, "record_declaration": EntityKind.CLASS,
            "annotation_type_declaration": EntityKind.INTERFACE,
            "method_declaration": EntityKind.METHOD, "constructor_declaration": EntityKind.METHOD,
            "compact_constructor_declaration": EntityKind.METHOD,
            "variable_declarator": EntityKind.PROPERTY,
        },
        comment_nodes=frozenset({"line_comment", "block_comment"}),
        body_fields=("body", "value"),
    ),
    LanguageSpec(
        name="kotlin", extensions=(".kt", ".kts"),
        kinds={
            "class_declaration": EntityKind.CLASS, "object_declaration": EntityKind.CLASS,
            "function_declaration": EntityKind.FUNCTION, "property_declaration": EntityKind.PROPERTY,
            "secondary_constructor": EntityKind.METHOD, "type_alias": EntityKind.TYPE,
        },
        comment_nodes=frozenset({"line_comment", "multiline_comment"}),
        name_children=("simple_identifier", "type_identifier", "variable_declaration"),
    ),
    LanguageSpec(
        name="c", extensions=(".c",),
        kinds={
            "struct_specifier": EntityKind.STRUCT, "union_specifier": EntityKind.STRUCT,
            "enum_specifier": EntityKind.ENUM, "function_definition": EntityKind.FUNCTION,
            "type_definition": EntityKind.TYPE, "preproc_function_def": EntityKind.FUNCTION,
        },
        comment_nodes=frozenset({"comment"}), body_fields=("body", "value"),
    ),
    LanguageSpec(
        name="ruby", extensions=(".rb", ".rake"),
        kinds={
            "class": EntityKind.CLASS, "module": EntityKind.NAMESPACE,
            "method": EntityKind.METHOD, "singleton_method": EntityKind.METHOD,
        },
        comment_nodes=frozenset({"comment"}),
    ),
    LanguageSpec(
        name="bash", extensions=(".sh", ".bash"),
        kinds={"function_definition": EntityKind.FUNCTION},
        comment_nodes=frozenset({"comment"}),
    ),
    LanguageSpec(
        name="powershell", extensions=(".ps1", ".psm1", ".psd1"),
        kinds={
            "function_statement": EntityKind.FUNCTION, "class_statement": EntityKind.CLASS,
            "enum_statement": EntityKind.ENUM, "class_method_definition": EntityKind.METHOD,
            "class_property_definition": EntityKind.PROPERTY,
        },
        comment_nodes=frozenset({"comment"}),
        name_children=("function_name", "simple_name", "variable"),
    ),
    LanguageSpec(
        name="hcl", extensions=(".hcl", ".tf", ".tfvars"),
        kinds={"block": EntityKind.SECTION, "attribute": EntityKind.SECTION},
        comment_nodes=frozenset({"comment"}), name_children=("identifier",),
    ),
    LanguageSpec(
        name="dart", extensions=(".dart",),
        kinds={"class_definition": EntityKind.CLASS, "gita_callable": EntityKind.FUNCTION,
               "enum_declaration": EntityKind.ENUM},
        comment_nodes=frozenset({"comment", "documentation_comment"}),
        name_children=("identifier",),
    ),
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
        name="swift",
        extensions=(".swift",),
        kinds={
            "class_declaration": EntityKind.CLASS,
            "protocol_declaration": EntityKind.INTERFACE,
            "function_declaration": EntityKind.FUNCTION,
            "protocol_function_declaration": EntityKind.METHOD,
            "init_declaration": EntityKind.METHOD,
            "deinit_declaration": EntityKind.METHOD,
            "subscript_declaration": EntityKind.METHOD,
            "property_declaration": EntityKind.PROPERTY,
            "typealias_declaration": EntityKind.TYPE,
        },
        comment_nodes=frozenset({"comment", "multiline_comment"}),
        body_fields=("body", "value", "computed_value"),
    ),
    LanguageSpec(
        name="php",
        extensions=(".php", ".phtml"),
        kinds={
            "namespace_definition": EntityKind.NAMESPACE,
            "class_declaration": EntityKind.CLASS,
            "interface_declaration": EntityKind.INTERFACE,
            "trait_declaration": EntityKind.TRAIT,
            "enum_declaration": EntityKind.ENUM,
            "function_definition": EntityKind.FUNCTION,
            "method_declaration": EntityKind.METHOD,
            "property_element": EntityKind.PROPERTY,
            "const_element": EntityKind.PROPERTY,
            "anonymous_function": EntityKind.FUNCTION,
            "arrow_function": EntityKind.FUNCTION,
        },
        comment_nodes=frozenset({"comment"}),
        anonymous_nodes=frozenset({"anonymous_function", "arrow_function"}),
        body_fields=("body", "value", "default_value"),
        name_children=("name", "variable_name"),
    ),
    LanguageSpec(
        name="csharp",
        extensions=(".cs", ".csx"),
        kinds={
            "namespace_declaration": EntityKind.NAMESPACE,
            "file_scoped_namespace_declaration": EntityKind.NAMESPACE,
            "class_declaration": EntityKind.CLASS,
            "record_declaration": EntityKind.CLASS,
            "struct_declaration": EntityKind.STRUCT,
            "interface_declaration": EntityKind.INTERFACE,
            "enum_declaration": EntityKind.ENUM,
            "delegate_declaration": EntityKind.TYPE,
            "method_declaration": EntityKind.METHOD,
            "constructor_declaration": EntityKind.METHOD,
            "destructor_declaration": EntityKind.METHOD,
            "operator_declaration": EntityKind.METHOD,
            "conversion_operator_declaration": EntityKind.METHOD,
            "local_function_statement": EntityKind.FUNCTION,
            "property_declaration": EntityKind.PROPERTY,
            "indexer_declaration": EntityKind.PROPERTY,
            "event_declaration": EntityKind.PROPERTY,
        },
        comment_nodes=frozenset({"comment"}),
        body_fields=("body", "value"),
    ),
    LanguageSpec(
        name="cpp",
        extensions=(".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx", ".h++",
                    ".ipp", ".tpp", ".inl"),
        kinds={
            "namespace_definition": EntityKind.NAMESPACE,
            "class_specifier": EntityKind.CLASS,
            "struct_specifier": EntityKind.STRUCT,
            "union_specifier": EntityKind.STRUCT,
            "enum_specifier": EntityKind.ENUM,
            "function_definition": EntityKind.FUNCTION,
            "alias_declaration": EntityKind.TYPE,
            "type_definition": EntityKind.TYPE,
        },
        comment_nodes=frozenset({"comment"}),
    ),
    LanguageSpec(
        name="go",
        extensions=(".go",),
        kinds={
            "function_declaration": EntityKind.FUNCTION,
            "method_declaration": EntityKind.METHOD,
            # the name lives on type_spec; type_declaration is just the `type (...)` wrapper
            "type_spec": EntityKind.TYPE,
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

# Docs and config are not code, but they are most of what changes in a repo and
# were previously invisible: gita saw one of three changed files.
DATA_SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="markdown",
        extensions=(".md", ".markdown", ".mdx"),
        kinds={"section": EntityKind.SECTION},
        comment_nodes=frozenset({"html_block"}),
        body_fields=(),
        name_children=("atx_heading", "setext_heading"),
    ),
    LanguageSpec(
        name="yaml",
        extensions=(".yaml", ".yml"),
        kinds={"block_mapping_pair": EntityKind.SECTION},
        comment_nodes=frozenset({"comment"}),
        body_fields=("value",),
    ),
    LanguageSpec(
        name="json",
        extensions=(".json",),
        kinds={"pair": EntityKind.SECTION},
        comment_nodes=frozenset({"comment"}),
        body_fields=("value",),
    ),
    LanguageSpec(
        name="toml",
        extensions=(".toml",),
        kinds={"table": EntityKind.SECTION, "pair": EntityKind.SECTION},
        comment_nodes=frozenset({"comment"}),
        body_fields=("value",),
        name_children=("bare_key", "dotted_key", "quoted_key"),
    ),
)

SPECS = SPECS + DATA_SPECS

BY_NAME: dict[str, LanguageSpec] = {spec.name: spec for spec in SPECS}
BY_EXTENSION: dict[str, LanguageSpec] = {
    ext: spec for spec in SPECS for ext in spec.extensions
}


def for_path(path: str) -> LanguageSpec | None:
    dot = path.rfind(".")
    return BY_EXTENSION.get(path[dot:].lower()) if dot != -1 else None


def is_supported(path: str) -> bool:
    """Whether gita can break this file into named entities.

    SQL has no LanguageSpec because it has no usable grammar -- it is scanned
    directly -- but it is still parsed into entities, so it belongs here.
    """
    from .sql import SQL_EXTENSIONS
    return for_path(path) is not None or path.lower().endswith(SQL_EXTENSIONS)
