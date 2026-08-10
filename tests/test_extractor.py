import pytest

from gita import EntityKind, extract

PY = b'''
import os

CONST = 1


class Store:
    """docstring."""

    def get(self, key):
        return self._data[key]

    def put(self, key, value):
        self._data[key] = value


def helper(a, b):
    return a + b
'''

JS = b'''
const parse = (input) => {
  return JSON.parse(input);
};

const handlers = {
  onClick: function () { return 1; },
};

describe('Router', () => {
  it('should 404', () => {
    expect(1).toBe(1);
  });
  it('should 404', () => {
    expect(2).toBe(2);
  });
});

module.exports = function middleware(req, res) { next(); };

exports.boot = () => { start(); };
'''


def ids(tree):
    return {e.id for e in tree.walk()}


class TestExtraction:
    def test_python_entities(self):
        tree = extract(PY, "store.py")
        assert "store.py::Store" in ids(tree)
        assert "store.py::Store::get" in ids(tree)
        assert "store.py::Store::put" in ids(tree)
        assert "store.py::helper" in ids(tree)

    def test_synthetic_module_root_exists(self):
        tree = extract(PY, "store.py")
        root = tree.root
        assert root is not None
        assert root.synthetic
        assert root.kind is EntityKind.MODULE

    def test_kinds_are_assigned(self):
        tree = extract(PY, "store.py")
        assert tree.get("store.py::Store").kind is EntityKind.CLASS
        assert tree.get("store.py::helper").kind is EntityKind.FUNCTION

    def test_parent_child_links(self):
        tree = extract(PY, "store.py")
        store = tree.get("store.py::Store")
        assert "store.py::Store::get" in store.children
        assert tree.get("store.py::Store::get").parent_id == "store.py::Store"

    def test_no_parse_error_on_valid_source(self):
        assert not extract(PY, "store.py").parse_error


class TestBindingNames:
    """Spike A finding 1: functions are values in JS, named by their binding site."""

    def test_arrow_bound_to_const(self):
        assert "app.js::parse" in ids(extract(JS, "app.js"))

    def test_function_bound_to_object_key(self):
        assert "app.js::onClick" in ids(extract(JS, "app.js"))

    def test_callback_named_from_enclosing_call(self):
        found = ids(extract(JS, "app.js"))
        assert "app.js::describe('Router')" in found

    def test_nested_callbacks_build_a_path(self):
        found = ids(extract(JS, "app.js"))
        assert "app.js::describe('Router')::it('should 404')" in found

    def test_assignment_target(self):
        assert "app.js::exports.boot" in ids(extract(JS, "app.js"))

    def test_named_function_expression_keeps_its_own_name(self):
        found = ids(extract(JS, "app.js"))
        assert "app.js::middleware" in found

    def test_keyword_tokens_do_not_become_entities(self):
        assert not [i for i in ids(extract(JS, "app.js")) if i.endswith("<anonymous>")]

    def test_colliding_siblings_are_disambiguated(self):
        found = ids(extract(JS, "app.js"))
        assert "app.js::describe('Router')::it('should 404')" in found
        assert "app.js::describe('Router')::it('should 404')#2" in found


class TestHashes:
    def test_reformatting_preserves_content_hash(self):
        a = extract(b"def f(a,b):\n    return a+b\n", "m.py")
        b = extract(b"def f(a, b):\n        return a + b\n", "m.py")
        assert a.get("m.py::f").content_hash == b.get("m.py::f").content_hash
        assert a.get("m.py::f").raw_hash != b.get("m.py::f").raw_hash

    def test_comment_edit_preserves_content_hash(self):
        a = extract(b"def f():\n    # old note\n    return 1\n", "m.py")
        b = extract(b"def f():\n    # a totally new note\n    return 1\n", "m.py")
        assert a.get("m.py::f").content_hash == b.get("m.py::f").content_hash

    def test_body_edit_changes_content_but_not_signature(self):
        a = extract(b"def f(a, b):\n    return a + b\n", "m.py")
        b = extract(b"def f(a, b):\n    return a * b\n", "m.py")
        assert a.get("m.py::f").content_hash != b.get("m.py::f").content_hash
        assert a.get("m.py::f").signature_hash == b.get("m.py::f").signature_hash

    def test_signature_edit_changes_signature_hash(self):
        a = extract(b"def f(a, b):\n    return a\n", "m.py")
        b = extract(b"def f(a, b, c=1):\n    return a\n", "m.py")
        assert a.get("m.py::f").signature_hash != b.get("m.py::f").signature_hash


class TestEnclosing:
    @pytest.mark.parametrize("line,expected", [
        (11, "store.py::Store::get"),
        (14, "store.py::Store::put"),
        (18, "store.py::helper"),
    ])
    def test_innermost_entity_wins(self, line, expected):
        assert extract(PY, "store.py").enclosing(line).id == expected

    def test_blank_line_inside_class_resolves_to_the_class(self):
        assert extract(PY, "store.py").enclosing(12).id == "store.py::Store"

    def test_top_level_line_falls_back_to_module(self):
        tree = extract(PY, "store.py")
        assert tree.enclosing(2).id == "store.py"
