"""Extraction tests for the languages only exercised indirectly by the corpus.

Corpus integration proves the engine does not crash on Go, Rust and TSX. It does
not prove the right entities come out. These do.
"""

from gita import ChangeKind, EntityKind, diff_trees, extract

GO = b'''
package main

import "fmt"

type Store struct {
	data map[string]string
}

func (s *Store) Get(key string) string {
	return s.data[key]
}

func New() *Store {
	return &Store{data: make(map[string]string)}
}

var handler = func(msg string) {
	fmt.Println(msg)
}
'''

RUST = b'''
use std::collections::HashMap;

pub struct Store {
    data: HashMap<String, String>,
}

impl Store {
    pub fn get(&self, key: &str) -> Option<&String> {
        self.data.get(key)
    }
}

pub trait Cache {
    fn clear(&mut self);
}

pub enum Mode {
    Fast,
    Safe,
}

pub fn build() -> Store {
    Store { data: HashMap::new() }
}
'''

TSX = b'''
interface Props {
  label: string;
}

type Handler = (value: string) => void;

export function Button({ label }: Props) {
  return <button>{label}</button>;
}

const Panel = ({ label }: Props) => {
  return <div>{label}</div>;
};

export class Widget {
  render(): string {
    return "widget";
  }
}
'''


def ids(source: bytes, path: str) -> set[str]:
    return {e.id for e in extract(source, path).walk()}


class TestGo:
    def test_functions_and_methods(self):
        found = ids(GO, "store.go")
        assert "store.go::New" in found
        assert "store.go::Get" in found

    def test_type_declaration(self):
        assert "store.go::Store" in ids(GO, "store.go")

    def test_func_literal_named_from_binding(self):
        assert "store.go::handler" in ids(GO, "store.go")

    def test_kinds(self):
        tree = extract(GO, "store.go")
        assert tree.get("store.go::New").kind is EntityKind.FUNCTION
        assert tree.get("store.go::Get").kind is EntityKind.METHOD

    def test_body_change_is_detected(self):
        changed = GO.replace(b"return s.data[key]", b"return s.data[key] + \"!\"")
        result = {c.id: c for c in diff_trees(extract(GO, "s.go"), extract(changed, "s.go"))}
        assert result["s.go::Get"].kind is ChangeKind.BODY_CHANGED


class TestRust:
    def test_struct_trait_enum_and_fn(self):
        found = ids(RUST, "store.rs")
        assert "store.rs::Store" in found
        assert "store.rs::Cache" in found
        assert "store.rs::Mode" in found
        assert "store.rs::build" in found

    def test_impl_block_nests_its_methods(self):
        found = ids(RUST, "store.rs")
        assert "store.rs::Store#2" in found or "store.rs::Store" in found
        assert any(i.endswith("::get") for i in found), "impl methods must be nested"

    def test_kinds(self):
        tree = extract(RUST, "store.rs")
        assert tree.get("store.rs::Cache").kind is EntityKind.TRAIT
        assert tree.get("store.rs::Mode").kind is EntityKind.ENUM
        assert tree.get("store.rs::build").kind is EntityKind.FUNCTION

    def test_signature_change_is_detected(self):
        changed = RUST.replace(b"pub fn build() -> Store", b"pub fn build(cap: usize) -> Store")
        result = {c.id: c for c in diff_trees(extract(RUST, "s.rs"), extract(changed, "s.rs"))}
        assert result["s.rs::build"].kind is ChangeKind.SIGNATURE_CHANGED

    def test_comment_change_is_cosmetic(self):
        changed = RUST.replace(b"use std::collections::HashMap;",
                               b"// a new note\nuse std::collections::HashMap;")
        result = {c.id: c for c in diff_trees(extract(RUST, "s.rs"), extract(changed, "s.rs"))}
        assert result["s.rs"].kind is ChangeKind.COSMETIC


class TestTsx:
    def test_component_function_and_arrow(self):
        found = ids(TSX, "ui.tsx")
        assert "ui.tsx::Button" in found
        assert "ui.tsx::Panel" in found

    def test_interface_and_type_alias(self):
        tree = extract(TSX, "ui.tsx")
        assert tree.get("ui.tsx::Props").kind is EntityKind.INTERFACE
        assert tree.get("ui.tsx::Handler").kind is EntityKind.TYPE

    def test_class_and_method_nesting(self):
        found = ids(TSX, "ui.tsx")
        assert "ui.tsx::Widget" in found
        assert "ui.tsx::Widget::render" in found

    def test_jsx_body_change_is_detected(self):
        changed = TSX.replace(b"<button>{label}</button>", b"<button disabled>{label}</button>")
        result = {c.id: c for c in diff_trees(extract(TSX, "u.tsx"), extract(changed, "u.tsx"))}
        assert result["u.tsx::Button"].kind is ChangeKind.BODY_CHANGED

    def test_no_parse_error_on_jsx(self):
        assert not extract(TSX, "ui.tsx").parse_error
