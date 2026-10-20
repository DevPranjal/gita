"""Source -> EntityTree, via tree-sitter.

Everything here is deterministic. No model is involved, and none may be:
per SCOPE.md section 2 the SLM never computes facts, it only writes prose over
facts this module produced.
"""

from __future__ import annotations

from .parsers import get_parser

from .languages import LanguageSpec, for_path
from .sql import SQL_EXTENSIONS, extract_sql
from .model import Entity, EntityKind, EntityTree, SyntaxRegion, digest

#: Wrappers to climb through when hunting for the binding site of a function value.
_TRANSPARENT = frozenset({
    "arguments", "parenthesized_expression", "sequence_expression",
    "return_statement", "array", "expression_statement", "await_expression",
    "non_null_expression", "as_expression", "satisfies_expression",
    "type_assertion", "spread_element", "ternary_expression", "pair_pattern",
    "expression_list", "var_declaration", "assignment_statement",
})

_NAME_FIELDS = ("name", "key", "type", "declarator", "pattern")

_IDENTIFIER_NODES = frozenset({
    "identifier", "type_identifier", "field_identifier",
    "property_identifier", "constant", "word",
})

MODULE_ENTITY = "<module>"
_NATIVE_LITERAL_NODES = frozenset({
    "string_literal", "char_literal", "raw_string_literal", "verbatim_string_literal", "string_content",
    "string", "line_string_literal", "multi_line_string_literal", "encapsed_string", "heredoc", "nowdoc",
})

_LAYOUT_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx", "go", "rust", "swift", "php"})
_LITERAL_NODES = frozenset({
    "string", "string_literal", "raw_string_literal", "interpreted_string_literal",
    "template_string", "jsx_element", "jsx_fragment", "token_tree",
    "line_string_literal", "multi_line_string_literal", "encapsed_string", "heredoc", "nowdoc",
})


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


def _file_namespace(node, spec: LanguageSpec):
    if spec.name == "csharp" and node.type == "compilation_unit":
        return next((child for child in node.named_children
                     if child.type == "file_scoped_namespace_declaration"), None)
    if spec.name == "php" and node.type == "program":
        namespaces = [child for child in node.named_children if child.type == "namespace_definition"]
        if len(namespaces) == 1 and namespaces[0].child_by_field_name("body") is None:
            return namespaces[0]
    return None


def _unsupported_layout(node, spec: LanguageSpec) -> str | None:
    if spec.name == "php":
        namespaces = [child for child in node.named_children if child.type == "namespace_definition"]
        if len(namespaces) > 1 and any(n.child_by_field_name("body") is None for n in namespaces):
            return "multiple unbraced PHP namespaces require separate scope regions"
    return None


def _declaration(node, spec: LanguageSpec):
    if spec.name == "cpp":
        while node.type == "template_declaration":
            declaration = next((child for child in reversed(node.named_children)
                                if child.type in spec.kinds or child.type in
                                ("template_declaration", "declaration", "field_declaration")), None)
            if declaration is None:
                break
            node = declaration
    return node


def _declarator_child(node):
    child = node.child_by_field_name("declarator")
    if child is None and node.type in ("reference_declarator", "parenthesized_declarator"):
        child = next(iter(node.named_children), None)
    return child


def _cpp_callable(node) -> bool:
    if len(node.children_by_field_name("declarator")) != 1:
        return False
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type == "operator_cast":
            return True
        if declarator.type == "function_declarator":
            name = declarator.child_by_field_name("declarator")
            return name is not None and name.type != "parenthesized_declarator"
        declarator = _declarator_child(declarator)
    return False


def _entity_kind(node, spec: LanguageSpec):
    if spec.name == "swift":
        if node.type == "class_declaration":
            declaration = node.child_by_field_name("declaration_kind")
            return {"struct": EntityKind.STRUCT, "enum": EntityKind.ENUM,
                    "extension": EntityKind.IMPL}.get(_text(declaration), EntityKind.CLASS)
        if node.type == "function_declaration" and node.parent is not None:
            if node.parent.type in ("class_body", "enum_class_body", "protocol_body"):
                return EntityKind.METHOD
        if node.type == "property_declaration" and len(node.children_by_field_name("name")) != 1:
            return None
    if spec.name == "php":
        if node.type == "namespace_definition" and node.child_by_field_name("body") is None:
            return None
        if _file_namespace(node, spec) is not None:
            return EntityKind.NAMESPACE
    if spec.name == "csharp":
        if node.type == "record_declaration" and any(child.type == "struct" for child in node.children):
            return EntityKind.STRUCT
        if node.type == "file_scoped_namespace_declaration":
            return None
        if _file_namespace(node, spec) is not None:
            return EntityKind.NAMESPACE
    if spec.name == "cpp":
        parent = node.parent
        if parent is not None and parent.type == "template_declaration" and _declaration(parent, spec) != parent:
            return None
        declaration = _declaration(node, spec)
        kind = spec.kinds.get(declaration.type)
        if declaration.type in ("declaration", "field_declaration") and _cpp_callable(declaration):
            kind = EntityKind.FUNCTION
        if kind is EntityKind.FUNCTION:
            while parent is not None:
                if parent.type == "field_declaration_list":
                    return EntityKind.METHOD
                if parent.type in ("function_definition", "namespace_definition", "translation_unit"):
                    break
                parent = parent.parent
        return kind
    return spec.kinds.get(node.type)


def _native_name(node, spec: LanguageSpec, read):
    namespace = _file_namespace(node, spec)
    if namespace is not None:
        return read(namespace.child_by_field_name("name"))
    if spec.name == "swift" and node.type in ("deinit_declaration", "subscript_declaration"):
        return "deinit" if node.type == "deinit_declaration" else "subscript"
    if spec.name == "php":
        if node.type == "property_element":
            return read(node.child_by_field_name("name")).lstrip("$")
        if node.type in spec.anonymous_nodes and node.parent is not None:
            if node.parent.type == "assignment_expression":
                return read(node.parent.child_by_field_name("left")).lstrip("$")
    if spec.name == "csharp":
        if node.type == "destructor_declaration":
            return "~" + read(node.child_by_field_name("name"))
        if node.type == "indexer_declaration":
            explicit = next((child for child in node.children if child.type == "explicit_interface_specifier"), None)
            return read(explicit).rstrip(".") + ".this" if explicit is not None else "this"
        if node.type == "operator_declaration":
            children = node.children
            operator = next(index for index, child in enumerate(children) if child.type == "operator")
            return "operator " + " ".join(read(child) for child in children[operator + 1:]
                                          if child.end_byte <= node.child_by_field_name("parameters").start_byte)
        if node.type == "conversion_operator_declaration":
            return "operator " + read(node.child_by_field_name("type"))
        explicit = next((child for child in node.children if child.type == "explicit_interface_specifier"), None)
        if explicit is not None:
            return read(explicit).rstrip(".") + "." + read(node.child_by_field_name("name"))
    if spec.name == "cpp":
        node = _declaration(node, spec)
        named = node.child_by_field_name("name")
        if named is not None:
            return read(named)
        declarator = node.child_by_field_name("declarator")
        while declarator is not None:
            if declarator.type == "operator_cast":
                function = declarator.child_by_field_name("declarator")
                while function is not None and function.type != "abstract_function_declarator":
                    function = _declarator_child(function)
                if function is not None:
                    return declarator.text[:function.start_byte - declarator.start_byte].decode("utf8", "replace").strip()
                return read(declarator)
            child = _declarator_child(declarator)
            if child is None:
                return read(declarator)
            declarator = child
    return None


def _entity_name(node, spec: LanguageSpec) -> str:
    native = _native_name(node, spec, _text)
    if native is not None:
        return native or "<anonymous>"
    if node.type in spec.anonymous_nodes:
        named = node.child_by_field_name("name")
        return _text(named) or _binding_name(node)

    # `impl Iterator for Walk` is a different thing from `struct Walk`, and
    # naming both "Walk" both loses the trait and forces a #2 collision suffix.
    if node.type == "impl_item":
        implemented = _text(node.child_by_field_name("type"))
        trait = _text(node.child_by_field_name("trait"))
        if trait and implemented:
            return f"{trait} for {implemented}"
        return implemented or "<anonymous>"

    for field_name in _NAME_FIELDS:
        child = node.child_by_field_name(field_name)
        if child is not None and _text(child):
            return _clean(_text(child))

    for child in node.children:
        if child.type in spec.name_children:
            return _clean(_text(child))

    for child in node.children:
        if child.type in _IDENTIFIER_NODES:
            return _text(child)
    return "<anonymous>"


def _clean(text: str) -> str:
    """Strip markup that decorates a heading or key rather than naming it."""
    return text.lstrip("#").strip().strip('"\'').strip() or "<anonymous>"


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
        if spec.name in ("csharp", "cpp", "swift", "php") and current.type in _NATIVE_LITERAL_NODES:
            out.append(current.text.decode("utf8", "replace"))
            continue
        if current.child_count == 0:
            text = current.text.decode("utf8", "replace").strip()
            if text:
                out.append(text)
            continue
        stack.extend(reversed(current.children))
    return out


def _own_tokens(node, spec: LanguageSpec) -> list[str]:
    return _own_and_body(node, spec, frozenset())[0]


def _own_and_body(node, spec: LanguageSpec,
                  body_ids: frozenset[int]) -> tuple[list[str], list[str]]:
    """Leaf tokens belonging to ``node`` itself, and the subset inside its body.

    Without this a class reports a change whenever any of its methods does, and
    the same change is counted at every level of the tree. An entity owns only
    the code no descendant entity has claimed.

    Children do not always tile their parent: tree-sitter gives a TOML string two
    quote children with the value in the gap between them. Taking only leaves
    dropped that gap, so a dependency version bump hashed as unchanged.

    Both results come from one walk because the body is a subtree of the whole,
    and traversing it twice was the single most expensive operation in a diff.
    """
    own: list[str] = []
    body: list[str] = []
    stack: list = [(node, False)]

    while stack:
        current, in_body = stack.pop()
        if isinstance(current, str):
            own.append(current)
            if in_body:
                body.append(current)
            continue
        if spec.name in ("csharp", "cpp", "swift", "php") and current.type in _NATIVE_LITERAL_NODES:
            text = current.text.decode("utf8", "replace")
            own.append(text)
            if in_body:
                body.append(text)
            continue
        if current.child_count == 0:
            text = current.text.decode("utf8", "replace").strip()
            if text:
                own.append(text)
                if in_body:
                    body.append(text)
            continue

        raw = current.text
        base = current.start_byte
        items: list = []
        cursor = 0
        for child in current.children:
            start = child.start_byte - base
            if start > cursor:
                gap = raw[cursor:start].decode("utf8", "replace").strip()
                if gap:
                    items.append((gap, in_body))
            if (child.type not in spec.comment_nodes
                    and _entity_kind(child, spec) is None):
                items.append((child, in_body or child.id in body_ids))
            cursor = child.end_byte - base
        if cursor < len(raw):
            tail = raw[cursor:].decode("utf8", "replace").strip()
            if tail:
                items.append((tail, in_body))
        stack.extend(reversed(items))

    return own, body


def _body_nodes(node, spec: LanguageSpec) -> frozenset[int]:
    if _file_namespace(node, spec) is not None:
        return frozenset(child.id for child in node.children
                         if child.type != "file_scoped_namespace_declaration")
    node = _declaration(node, spec)
    ids = set()
    for field_name in spec.body_fields:
        body = node.child_by_field_name(field_name)
        if body is not None:
            ids.add(body.id)
    if spec.name == "csharp":
        accessors = node.child_by_field_name("accessors")
        if accessors is not None:
            for accessor in accessors.named_children:
                body = accessor.child_by_field_name("body")
                if body is not None:
                    ids.add(body.id)
    return frozenset(ids)


def _make_entity(node, spec, path, name, parent_id, entity_id, kind) -> Entity:
    body = _body_nodes(node, spec)
    own, body_tokens = _own_and_body(node, spec, body)
    signature_tokens = _tokens(node, spec, skip=body) if body else own
    if not body:
        body_tokens = own
    name_node = _declaration(node, spec).child_by_field_name("name")

    return Entity(
        id=entity_id,
        kind=kind,
        name=name,
        path=path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        raw_hash=digest(node.text.decode("utf8", "replace")),
        content_hash=digest(" ".join(own)),
        signature_hash=digest(" ".join(signature_tokens)),
        body_hash=digest(" ".join(body_tokens)),
        body_size=len(body_tokens),
        signature=" ".join(signature_tokens)[:200],
        parent_id=parent_id,
        tokens=tuple(own),
        start_column=node.start_point[1],
        end_column=node.end_point[1],
        signature_shape=(tuple(_tokens(node, spec, skip=body | {name_node.id}))
                         if name_node is not None else None),
        body_tokens=tuple(body_tokens),
    )


def extract(source: bytes, path: str, spec: LanguageSpec | None = None) -> EntityTree:
    """Parse one revision of one file into an entity tree."""
    spec = spec or for_path(path)
    if spec is None:
        raise ValueError(f"unsupported file type: {path}")

    tree = get_parser(spec.name).parse(source)
    root_node = tree.root_node

    # Spike A finding 3: top-level statements need somewhere to live.
    namespace = _file_namespace(root_node, spec)
    root_tokens = [] if namespace is not None else _own_tokens(root_node, spec)
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
    if _unsupported_layout(root_node, spec) is not None:
        entity_tree.parse_error = True
        return entity_tree

    # Sibling names collide (overloads, repeated `it(...)` labels). Disambiguate
    # by document order so ids stay stable across revisions.
    seen: dict[str, int] = {}
    layout = ({i for i, line in enumerate(source.splitlines(), 1) if not line.strip()}
              if spec.name in _LAYOUT_LANGUAGES and not root_node.has_error else set())
    comments: list[tuple[int, int]] = []

    def visit(node, parent_id: str, location: tuple[tuple[str, int], ...]) -> None:
        if not root_node.has_error and (
                node.type in spec.comment_nodes or node.type == "expression_statement"):
            entity_tree.regions.append(SyntaxRegion(
                "comment" if node.type in spec.comment_nodes else node.type,
                parent_id, node.start_point[0] + 1, node.end_point[0] + 1,
                node.start_point[1], node.end_point[1], location[:-1],
            ))
        if layout and (node.child_count == 0 or node.type in _LITERAL_NODES
                       or node.type in spec.comment_nodes):
            start = node.start_point[0] + 1
            end = node.end_point[0] + (node.end_point[1] > 0)
            layout.difference_update(range(start, end + 1))
            if node.type in spec.comment_nodes:
                comments.append((start, end))
        child_parent = parent_id
        kind = _entity_kind(node, spec)

        if kind is not None:
            name = _entity_name(node, spec)
            base = f"{parent_id}::{name}"
            count = seen.get(base, 0)
            seen[base] = count + 1
            entity_id = base if count == 0 else f"{base}#{count + 1}"

            entity_tree.add(_make_entity(node, spec, path, name, parent_id, entity_id, kind))
            child_parent = entity_id

        prefix = () if kind is not None else location
        for index, child in enumerate(node.children):
            visit(child, child_parent, prefix + ((child.type, index),))

    if namespace is not None:
        visit(root_node, root.id, ())
    else:
        for index, child in enumerate(root_node.children):
            visit(child, root.id, ((child.type, index),))

    # Blank separation can affect directives and documentation attachment.
    for start, end in comments:
        for line, step in ((start - 1, -1), (end + 1, 1)):
            while line in layout:
                layout.remove(line)
                line += step
    entity_tree.layout_lines = frozenset(layout)
    return entity_tree


def extract_plain(source: bytes, path: str) -> EntityTree:
    """A whole file as a single entity, for types gita cannot parse.

    Dockerfiles, lockfiles and shell scripts still change, and reporting nothing
    is worse than reporting coarsely: a silent omission reads as "unchanged".
    """
    text = source.decode("utf8", "replace")
    normalised = " ".join(text.split())
    tree = EntityTree(path=path, language="plain", root_id=path)
    tree.add(Entity(
        id=path,
        kind=EntityKind.MODULE,
        name=MODULE_ENTITY,
        path=path,
        start_line=1,
        end_line=max(1, text.count("\n") + 1),
        raw_hash=digest(text),
        content_hash=digest(normalised),
        signature_hash="",
        body_hash=digest(normalised),
        body_size=len(normalised.split()),
        parent_id=None,
        synthetic=True,
    ))
    return tree


def extract_path(source: bytes, path: str) -> EntityTree | None:
    spec = for_path(path)
    if spec is not None:
        return extract(source, path, spec)
    if path.lower().endswith(SQL_EXTENSIONS):
        return extract_sql(source, path)
    return extract_plain(source, path)
