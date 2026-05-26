"""Phase 0 — promote symbol parsing to a stable public API.

These tests pin the surface that ``gita.symdiff`` (rename-aware diff) and
``gita.lookup`` (v0.2 ``gita get``) will both consume. The existing manifest
internals stay byte-compatible — phase 0 is move-not-change.
"""

from __future__ import annotations

import pytest

from gita import parse


# ---------------------------------------------------------------------------
# parse_module — basic shape
# ---------------------------------------------------------------------------


def test_parse_module_returns_named_symbols():
    src = (
        "def alpha(x): return x\n"
        "\n"
        "class Beta:\n"
        "    pass\n"
    )
    view = parse.parse_module(src)
    assert view is not None
    assert set(view.symbols) == {"alpha", "Beta"}
    assert view.symbols["alpha"].kind == "function"
    assert view.symbols["Beta"].kind == "class"


def test_parse_module_captures_signature():
    src = "def add(a: int, b: int) -> int:\n    return a + b\n"
    view = parse.parse_module(src)
    assert view is not None
    sym = view.symbols["add"]
    assert sym.signature == "def add(a: int, b: int) -> int"


def test_parse_module_unparseable_returns_none():
    # Trailing colon with no body — guaranteed syntax error.
    assert parse.parse_module("def broken(:\n") is None


# ---------------------------------------------------------------------------
# find — bare names, qualified names, methods inside classes
# ---------------------------------------------------------------------------


def test_find_top_level_function_by_bare_name():
    view = parse.parse_module("def fetch_user(uid): return uid\n")
    assert view is not None
    sym = parse.find(view, "fetch_user")
    assert sym.name == "fetch_user"
    assert sym.kind == "function"


def test_find_method_by_qualified_name():
    src = (
        "class UserHandler:\n"
        "    def get(self, uid): return uid\n"
        "    def put(self, uid, body): return body\n"
    )
    view = parse.parse_module(src)
    assert view is not None
    sym = parse.find(view, "UserHandler.get")
    assert sym.name == "get"
    assert sym.kind == "method"
    assert sym.parent == "UserHandler"


def test_find_method_by_bare_name_when_unique():
    # Single class, single ``run`` method, no top-level ``run`` — bare resolves.
    src = (
        "class Job:\n"
        "    def run(self): return 1\n"
    )
    view = parse.parse_module(src)
    assert view is not None
    sym = parse.find(view, "run")
    assert sym.kind == "method"
    assert sym.parent == "Job"


def test_find_ambiguous_bare_name_raises_with_candidates():
    src = (
        "class A:\n"
        "    def __init__(self): pass\n"
        "class B:\n"
        "    def __init__(self): pass\n"
    )
    view = parse.parse_module(src)
    assert view is not None
    with pytest.raises(parse.Ambiguous) as ei:
        parse.find(view, "__init__")
    assert sorted(ei.value.candidates) == ["A.__init__", "B.__init__"]


def test_find_unknown_name_raises_not_found():
    view = parse.parse_module("def only_this(): pass\n")
    assert view is not None
    with pytest.raises(parse.NotFound):
        parse.find(view, "missing")


def test_find_qualified_name_on_missing_class_raises_not_found():
    view = parse.parse_module("def only_this(): pass\n")
    assert view is not None
    with pytest.raises(parse.NotFound):
        parse.find(view, "Nope.method")


# ---------------------------------------------------------------------------
# symbol_id — stable handle, re-exported from parse for one home
# ---------------------------------------------------------------------------


def test_symbol_id_is_stable_and_kind_sensitive():
    a = parse.symbol_id("function", "foo")
    b = parse.symbol_id("function", "foo")
    c = parse.symbol_id("class", "foo")
    assert a == b
    assert a != c
