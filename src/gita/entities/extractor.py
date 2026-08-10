"""Source -> EntityTree, via tree-sitter.

Everything here is deterministic. No model is involved, and none may be:
per SCOPE.md section 2 the SLM never computes facts, it only writes prose over
facts this module produced.
"""

from __future__ import annotations

from tree_sitter_language_pack import get_parser

from .languages import LanguageSpec, for_path
from .model import Entity, EntityKind, EntityTree, digest

#: Wrappers to climb through when hunting for the binding site of a function value.
_TRANSPARENT = frozenset({
    "arguments", "parenthesized_expression", "sequence_expression",
    "return_statement", "array", "expression_statement", "await_expression",
    "non_null_expression", "as_expression", "satisfies_expression",
    "type_assertion", "spread_element", "ternary_expression", "pair_pattern",
    "expression_list", "var_declaration", "assignment_statement",
})

_NAME_FIELDS = ("name", "type", "declarator", "pattern")

_IDENTIFIER_NODES = frozenset({
    "identifier", "type_identifier", "field_identifier",
    "property_identifier", "constant", "word",
})

MODULE_ENTITY = "<module>"


def _text(node) -> str:
    if node is None:
        return ""
    raw = node.text.decode("utf8", "replace").strip()
    return raw.splitlines()[0][:100] if raw else ""


def _binding_name(node) -> str:
    """Name an anonymous function after whatever binds it.

    ``const parse = () => {}``          -> ``parse``
    ``{ onClick: () => {} }``           -> ``onClick``
    ``module.exports = fn``             -> ``module.exports``
    ``describe('router', () => {})``    -> ``describe('router')``
    """
    parent = node.parent
    for _ in range(6):
        if parent is None:
            break
        kind = parent.type

        if kind in ("variable_declarator", "public_field_definition",
                    "field_definition", "property_signature", "var_spec",
                    "const_spec"):
            return _text(parent.child_by_field_name("name")) or MODULE_ENTITY
        if kind == "short_var_declaration":
            return _text(parent.child_by_field_name("left")) or MODULE_ENTITY
        if kind == "pair":
            return _text(parent.child_by_field_name("key")) or MODULE_ENTITY
        if kind == "assignment_expression":
            return _text(parent.child_by_field_name("left")) or MODULE_ENTITY
        if kind == "call_expression":
            callee = _text(parent.child_by_field_name("function")) or "call"
            arguments = parent.child_by_field_name("arguments")
            if arguments is not None:
                for argument in arguments.children:
                    if argument.type in ("string", "template_string"):
                        return f"{callee}({_text(argument)})"
            return f"{callee}(fn)"

        if kind not in _TRANSPARENT:
            break
        parent = parent.parent

    return "<anonymous>"


def _entity_name(node, spec: LanguageSpec) -> str:
    if node.type in spec.anonymous_nodes:
        named = node.child_by_field_name("name")
        return _text(named) or _binding_name(node)

    for field_name in _NAME_FIELDS:
        child = node.child_by_field_name(field_name)
        if child is not None and _text(child):
            return _text(child)
    for child in node.children:
        if child.type in _IDENTIFIER_NODES:
            return _text(child)
    return "<anonymous>"


def _tokens(node, spec: LanguageSpec, skip: frozenset[int] = frozenset()) -> list[str]:
    """Leaf tokens with comments removed -- the normalised form used for hashing.

    Collapsing to a token stream makes the hash insensitive to whitespace and
    formatting, so reflows and comment edits are detectable as cosmetic.
    """
    out: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.id in skip or current.type in spec.comment_nodes:
            continue
        if current.child_count == 0:
            text = current.text.decode("utf8", "replace").strip()
            if text:
                out.append(text)
            continue
        stack.extend(reversed(current.children))
    return out


def _body_nodes(node, spec: LanguageSpec) -> frozenset[int]:
    ids = set()
    for field_name in spec.body_fields:
        body = node.child_by_field_name(field_name)
        if body is not None:
            ids.add(body.id)
    return frozenset(ids)


def _make_entity(node, spec, path, name, parent_id, entity_id, kind) -> Entity:
    body = _body_nodes(node, spec)
    tokens = _tokens(node, spec)
    signature_tokens = _tokens(node, spec, skip=body) if body else tokens

    body_tokens: list[str] = []
    for field_name in spec.body_fields:
        child = node.child_by_field_name(field_name)
        if child is not None:
            body_tokens += _tokens(child, spec)
    if not body:
        body_tokens = tokens

    return Entity(
        id=entity_id,
        kind=kind,
        name=name,
        path=path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        raw_hash=digest(node.text.decode("utf8", "replace")),
        content_hash=digest(" ".join(tokens)),
        signature_hash=digest(" ".join(signature_tokens)),
        body_hash=digest(" ".join(body_tokens)),
        body_size=len(body_tokens),
        signature=" ".join(signature_tokens)[:200],
        parent_id=parent_id,
        tokens=tuple(tokens),
    )


def _module_tokens(root_node, spec: LanguageSpec) -> list[str]:
    """Top-level tokens not claimed by any entity: imports, constants, side effects.

    Without this the module hash would cover the whole file and change whenever
    any function did, making it useless as a signal.
    """
    out: list[str] = []
    stack = list(reversed(root_node.children))
    while stack:
        current = stack.pop()
        if current.type in spec.entity_nodes or current.type in spec.comment_nodes:
            continue
        if current.child_count == 0:
            text = current.text.decode("utf8", "replace").strip()
            if text:
                out.append(text)
            continue
        stack.extend(reversed(current.children))
    return out


def extract(source: bytes, path: str, spec: LanguageSpec | None = None) -> EntityTree:
    """Parse one revision of one file into an entity tree."""
    spec = spec or for_path(path)
    if spec is None:
        raise ValueError(f"unsupported file type: {path}")

    tree = get_parser(spec.name).parse(source)
    root_node = tree.root_node

    # Spike A finding 3: top-level statements need somewhere to live.
    root_tokens = _module_tokens(root_node, spec)
    root = Entity(
        id=path,
        kind=EntityKind.MODULE,
        name=MODULE_ENTITY,
        path=path,
        start_line=1,
        end_line=root_node.end_point[0] + 1,
        raw_hash=digest(source.decode("utf8", "replace")),
        content_hash=digest(" ".join(root_tokens)),
        signature_hash="",
        body_hash=digest(" ".join(root_tokens)),
        body_size=len(root_tokens),
        parent_id=None,
        synthetic=True,
    )

    entity_tree = EntityTree(
        path=path,
        language=spec.name,
        root_id=root.id,
        parse_error=root_node.has_error,
    )
    entity_tree.add(root)

    # Sibling names collide (overloads, repeated `it(...)` labels). Disambiguate
    # by document order so ids stay stable across revisions.
    seen: dict[str, int] = {}

    def visit(node, parent_id: str) -> None:
        child_parent = parent_id
        kind = spec.kinds.get(node.type)

        if kind is not None:
            name = _entity_name(node, spec)
            base = f"{parent_id}::{name}"
            count = seen.get(base, 0)
            seen[base] = count + 1
            entity_id = base if count == 0 else f"{base}#{count + 1}"

            entity_tree.add(_make_entity(node, spec, path, name, parent_id, entity_id, kind))
            child_parent = entity_id

        for child in node.children:
            visit(child, child_parent)

    for child in root_node.children:
        visit(child, root.id)

    return entity_tree


def extract_path(source: bytes, path: str) -> EntityTree | None:
    spec = for_path(path)
    return extract(source, path, spec) if spec else None
