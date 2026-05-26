"""Stable parsing surface for gita.

This module owns the *one* representation of "a Python module as a bag of
named top-level symbols plus imports" used by:

* :mod:`gita._manifest` — strict body-hash rename pairing (v0.1 manifest core)
* :mod:`gita.symdiff`   — similarity-based rename pairing (v0.1 diff frontend)
* :mod:`gita.lookup`    — symbol resolution for ``gita get`` (v0.2)

The public surface is intentionally small:

* :class:`Symbol`, :class:`Imports`, :class:`ModuleView` — data
* :func:`parse_module` — source → :class:`ModuleView` (or ``None`` on syntax error)
* :func:`find` — :class:`ModuleView` × name → :class:`Symbol`
                 (handles ``Class.method`` and bare-name resolution)
* :exc:`Ambiguous`, :exc:`NotFound`
* :func:`symbol_id` — stable handle for a (kind, name) pair

Everything else (signature rendering, body hashing, name stripping) is
exposed as module-level helpers because the manifest diff needs them, but
they're not part of the supported API.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterator

import libcst as cst


# ---------------------------------------------------------------------------
# stable-ish symbol IDs
# ---------------------------------------------------------------------------


def symbol_id(kind: str, name: str) -> str:
    """Deterministic 16-hex handle for a (kind, name) pair.

    Keyed on (kind, name) only — good enough to be a useful pointer in the
    manifest. Rename is handled explicitly at the diff layer via a
    ``rename_symbol`` op that carries both names.
    """
    return hashlib.sha256(f"{kind}:{name}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    kind: str                   # "function" | "class" | "method"
    name: str                   # bare name (e.g. "get")
    signature: str              # rendered "def name(args) -> ret" line, sans body
    body_hash: str              # hash of body text with own name stripped
    body_lines: int
    node: cst.CSTNode           # original node for downstream inspection
    parent: str | None = None   # enclosing class for methods, else None


@dataclass(frozen=True)
class Imports:
    # (module, sorted-tuple-of-names) so reorder is a no-op for set diff
    # and add/remove fall out of set difference.
    items: tuple[tuple[str, tuple[str, ...]], ...]
    # Source order, to detect pure reorders.
    order: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class ModuleView:
    symbols: dict[str, Symbol]  # keyed by name — TOP-LEVEL only (by design)
    imports: Imports
    raw_code: str


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class NotFound(LookupError):
    """Raised by :func:`find` when ``name`` doesn't resolve to any symbol."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class Ambiguous(LookupError):
    """Raised by :func:`find` when a bare name matches >1 symbol."""

    def __init__(self, name: str, candidates: list[str]) -> None:
        super().__init__(f"{name}: {candidates}")
        self.name = name
        self.candidates = candidates


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def parse_module(source: str) -> ModuleView | None:
    """Parse ``source``. Returns ``None`` on any parse error.

    Replaces the v0.1 pattern of catching ``cst.ParserSyntaxError`` (and
    occasionally any ``Exception``) at every call site.
    """
    try:
        module = cst.parse_module(source)
    except Exception:
        return None

    symbols: dict[str, Symbol] = {}
    import_items: list[tuple[str, tuple[str, ...]]] = []

    for stmt in module.body:
        if isinstance(stmt, cst.FunctionDef):
            name = stmt.name.value
            body_text = _strip_name(_render(stmt.body), name)
            symbols[name] = Symbol(
                kind="function",
                name=name,
                signature=_render_signature(stmt),
                body_hash=_hash(body_text),
                body_lines=body_text.count("\n"),
                node=stmt,
            )
        elif isinstance(stmt, cst.ClassDef):
            name = stmt.name.value
            body_text = _strip_name(_render(stmt.body), name)
            symbols[name] = Symbol(
                kind="class",
                name=name,
                signature=f"class {name}",
                body_hash=_hash(body_text),
                body_lines=body_text.count("\n"),
                node=stmt,
            )
        elif isinstance(stmt, cst.SimpleStatementLine):
            for small in stmt.body:
                if isinstance(small, cst.Import):
                    for alias in small.names:
                        import_items.append((_dotted(alias.name), ()))
                elif isinstance(small, cst.ImportFrom):
                    mod = _dotted(small.module) if small.module else ""
                    if isinstance(small.names, cst.ImportStar):
                        names: tuple[str, ...] = ("*",)
                    else:
                        names = tuple(sorted(a.name.value for a in small.names))
                    import_items.append((mod, names))

    imports = Imports(
        items=tuple(sorted(import_items)),
        order=tuple(import_items),
    )
    return ModuleView(symbols=symbols, imports=imports, raw_code=source)


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------


def find(view: ModuleView, name: str) -> Symbol:
    """Resolve ``name`` to a single :class:`Symbol`.

    Resolution rules:

    * Qualified (``"Class.method"``) — exact, never ambiguous, never bare-fallback.
    * Bare (``"foo"``) — matches any top-level symbol *or* any method with
      that name. If multiple match, raises :exc:`Ambiguous` with the list of
      qualified candidate names.
    * No match — raises :exc:`NotFound`.
    """
    if "." in name:
        cls_name, method_name = name.rsplit(".", 1)
        cls = view.symbols.get(cls_name)
        if cls is None or cls.kind != "class":
            raise NotFound(name)
        for method in _iter_methods(cls):
            if method.name == method_name:
                return method
        raise NotFound(name)

    candidates: list[Symbol] = []
    for sym in view.symbols.values():
        if sym.name == name:
            candidates.append(sym)
        if sym.kind == "class":
            for method in _iter_methods(sym):
                if method.name == name:
                    candidates.append(method)

    if not candidates:
        raise NotFound(name)
    if len(candidates) > 1:
        raise Ambiguous(name, sorted(_qualified(s) for s in candidates))
    return candidates[0]


def _qualified(sym: Symbol) -> str:
    if sym.parent is None:
        return sym.name
    return f"{sym.parent}.{sym.name}"


def _iter_methods(cls: Symbol) -> Iterator[Symbol]:
    """Yield each ``def`` immediately inside ``cls``'s body.

    We don't descend into nested classes — Python allows them but they're
    rare enough that v0.2 punts. Add a recursive walk in v0.3 if a user
    actually files an issue.
    """
    node = cls.node
    if not isinstance(node, cst.ClassDef):
        return
    body = node.body
    if not isinstance(body, cst.IndentedBlock):
        return
    for stmt in body.body:
        if isinstance(stmt, cst.FunctionDef):
            mname = stmt.name.value
            body_text = _strip_name(_render(stmt.body), mname)
            yield Symbol(
                kind="method",
                name=mname,
                signature=_render_signature(stmt),
                body_hash=_hash(body_text),
                body_lines=body_text.count("\n"),
                node=stmt,
                parent=cls.name,
            )


# ---------------------------------------------------------------------------
# helpers — exposed for gita._manifest and gita.symdiff, not for end users
# ---------------------------------------------------------------------------


def _render(node: cst.CSTNode) -> str:
    return cst.Module(body=[]).code_for_node(node)


def _strip_name(text: str, name: str) -> str:
    return text.replace(f" {name}(", " __SYM__(").replace(f" {name}:", " __SYM__:")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _dotted(name_node: cst.CSTNode | None) -> str:
    if name_node is None:
        return ""
    return _render(name_node).strip()


def _render_signature(fn: cst.FunctionDef) -> str:
    params = _render(fn.params).strip()
    ret = ""
    if fn.returns is not None:
        ret = " -> " + _render(fn.returns.annotation).strip()
    prefix = "async def " if fn.asynchronous is not None else "def "
    return f"{prefix}{fn.name.value}({params}){ret}"
