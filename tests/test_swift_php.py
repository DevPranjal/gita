from hashlib import blake2b
import subprocess

import pytest

from gita.entities.extractor import extract
from gita.entities.languages import for_path
from gita.index.evidence import parse_evidence
from gita.index.model import ParseStatus


SWIFT = b'''struct Panel {
    var value: Int = 1
    init(value: Int) { self.value = value }
    func swap(_ other: Int) -> Int { return other }
    subscript(index: Int) -> Int { return index }
}
class Owner { deinit {} }
protocol Drawing { func paint() }
enum Choice { case yes; case no }
extension Panel { func reset() {} }
typealias Count = Int
func render() -> String { return " a " }
'''

PHP = b'''<?php
namespace Demo\\Chat;
trait Visible { public function visible() {} }
interface Sink { public function push($value); }
enum State { case Ready; }
class Store {
    public string $title = " a ";
    public int $left = 1, $right = 2;
    const SIZE = 3;
    public function mount($value) {
        $parse = fn($x) => $x;
        $callback = function($x) { return $x; };
        return $value;
    }
}
function render($value) { return $value; }
'''


@pytest.mark.parametrize(("path", "language"), [
    ("Panel.swift", "swift"), ("PANEL.SWIFT", "swift"),
    ("Chat.php", "php"), ("CHAT.PHP", "php"), ("view.phtml", "php"),
])
def test_swift_php_extensions_are_registered(path, language):
    from gita.index.evidence import _spec_for_path

    spec = for_path(path)
    assert spec is not None and spec.name == language
    assert _spec_for_path(path.encode()) is spec


@pytest.mark.parametrize(("source", "path", "expected"), [
    (SWIFT, "Panel.swift", {
        (("Panel",), "struct"), (("Panel", "value"), "property"),
        (("Panel", "init"), "method"), (("Panel", "swap"), "method"),
        (("Panel", "subscript"), "method"), (("Owner",), "class"),
        (("Owner", "deinit"), "method"), (("Drawing",), "interface"),
        (("Drawing", "paint"), "method"), (("Choice",), "enum"),
        (("Panel",), "impl"), (("Panel", "reset"), "method"),
        (("Count",), "type"), (("render",), "function"),
    }),
    (PHP, "Chat.php", {
        (("Demo\\Chat",), "namespace"), (("Demo\\Chat", "Visible"), "trait"),
        (("Demo\\Chat", "Visible", "visible"), "method"),
        (("Demo\\Chat", "Sink"), "interface"), (("Demo\\Chat", "Sink", "push"), "method"),
        (("Demo\\Chat", "State"), "enum"), (("Demo\\Chat", "Store"), "class"),
        (("Demo\\Chat", "Store", "title"), "property"),
        (("Demo\\Chat", "Store", "left"), "property"),
        (("Demo\\Chat", "Store", "right"), "property"),
        (("Demo\\Chat", "Store", "SIZE"), "property"),
        (("Demo\\Chat", "Store", "mount"), "method"),
        (("Demo\\Chat", "Store", "mount", "parse"), "function"),
        (("Demo\\Chat", "Store", "mount", "callback"), "function"),
        (("Demo\\Chat", "render"), "function"),
    }),
])
def test_swift_php_declarations_have_exact_names_parents_and_source_bytes(source, path, expected):
    bundle = parse_evidence(source, path.encode())
    assert bundle.result.complete, bundle.result.diagnostics
    definitions = [d for d in bundle.result.definitions if not d.synthetic_root]
    assert {(d.names, d.kind) for d in definitions} == expected
    assert len(definitions) == len(expected)
    by_id = {d.id: d for d in bundle.result.definitions}
    versions = {v.id: v for v in bundle.versions}
    for definition in definitions:
        raw = source[definition.span.start_byte:definition.span.end_byte]
        assert versions[definition.version_id].source_digest == blake2b(raw, digest_size=32).digest()
        parent = by_id[definition.parent_id]
        if not parent.synthetic_root:
            assert definition.names[:-1] == parent.names
        assert parent.span.start_byte <= definition.span.start_byte
        assert parent.span.end_byte >= definition.span.end_byte
    assert bundle == parse_evidence(source, path.encode())
    legacy = extract(source, path)
    assert not legacy.parse_error
    legacy_names = set()
    for entity in legacy.walk():
        if entity.synthetic:
            continue
        names = [entity.name]
        parent = legacy.get(entity.parent_id)
        while not parent.synthetic:
            names.append(parent.name)
            parent = legacy.get(parent.parent_id)
        legacy_names.add((tuple(reversed(names)), entity.kind.value))
    assert legacy_names == expected


def test_php_bracketed_namespaces_keep_separate_scopes():
    source = b"<?php namespace A { function work() {} } namespace B { function work() {} }"
    result = parse_evidence(source, b"two.php").result
    assert result.complete
    assert {d.names for d in result.definitions if not d.synthetic_root} == {
        ("A",), ("A", "work"), ("B",), ("B", "work")}


def test_multiple_unbraced_php_namespaces_fail_explicitly_instead_of_misnaming():
    source = b"<?php namespace A; function work() {} namespace B; function work() {}"
    result = parse_evidence(source, b"two.php").result
    assert result.status is ParseStatus.UNSUPPORTED_LANGUAGE
    assert result.definitions == ()
    assert "multiple unbraced PHP namespaces" in result.diagnostics[0]
    assert extract(source, "two.php").parse_error


@pytest.mark.parametrize(("path", "first", "second"), [
    ("text.swift", b'func text() -> String { return " a " }', b'func text() -> String { return "a" }'),
    ("text.php", b'<?php function text() { return " a "; }', b'<?php function text() { return "a"; }'),
])
def test_literal_whitespace_is_not_normalized_away(path, first, second):
    before, after = (parse_evidence(s, path.encode()) for s in (first, second))
    assert before.result.complete and after.result.complete
    before_def = next(d for d in before.result.definitions if d.names == ("text",))
    after_def = next(d for d in after.result.definitions if d.names == ("text",))
    versions = {v.id: v for v in (*before.versions, *after.versions)}
    assert versions[before_def.version_id].body != versions[after_def.version_id].body


def test_swift_multi_binding_properties_remain_in_enclosing_source():
    source = b"struct Pair { var left = 1, right = 2 }"
    result = parse_evidence(source, b"Pair.swift").result
    assert result.complete
    assert {d.names for d in result.definitions} == {("<module>",), ("Pair",)}


@pytest.mark.parametrize(("suffix", "source"), [
    ("swift", b"struct Store { func compute(_ value: Int) -> Int { return value + 1 } }\n"),
    ("php", b"<?php namespace App; class Store { public function compute($value) { return $value + 1; } }\n"),
])
def test_swift_php_publish_stable_identity_history_and_exact_source(tmp_path, suffix, source):
    from gita.index.builder import BuildLimits, build
    from gita.index.model import GitOid
    from gita.index.query import open_index
    from gita.index.store import IndexStore
    from gita.vcs.git import CommittedRepo, Repo

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                              capture_output=True).stdout

    git("init")
    git("config", "user.name", "Synthetic Language Test")
    git("config", "user.email", "synthetic@example.invalid")
    path = tmp_path / ("store." + suffix)
    path.write_bytes(source)
    git("add", "--", path.name)
    git("commit", "--no-gpg-sign", "-m", "base")
    commits = [git("rev-parse", "HEAD").decode().strip()]
    changed = source.replace(b"+ 1", b"+ 2")
    path.write_bytes(changed)
    git("add", "--", path.name)
    git("commit", "--no-gpg-sign", "-m", "body change")
    commits.append(git("rev-parse", "HEAD").decode().strip())
    moved = tmp_path / ("moved." + suffix)
    path.rename(moved)
    git("add", "-A")
    git("commit", "--no-gpg-sign", "-m", "move")
    commits.append(git("rev-parse", "HEAD").decode().strip())
    location = IndexStore.locate(Repo(tmp_path))
    with CommittedRepo(tmp_path) as repository, IndexStore.create(location) as store:
        build(repository, store, scope="language-test", ref=commits[-1], since=commits[0],
              limits=BuildLimits(workers=1))
    with CommittedRepo(tmp_path) as repository, open_index(repository, scope="language-test") as session:
        references = []
        for commit, raw in zip(commits, (source, changed, changed), strict=True):
            result = session.resolve("compute", at=GitOid.from_hex("sha1", commit))
            assert result["status"] == "unique"
            reference = result["results"][0]
            references.append(reference)
            occurrence = session.state_at(reference["eid"], GitOid.from_hex("sha1", commit))["occurrence"]
            span = occurrence["span"]
            assert session.source(occurrence) == raw[span["start_byte"]:span["end_byte"]]
        assert len({r["eid"] for r in references}) == 1
        history = session.history(references[-1]["eid"], order="oldest")
        assert [e["flags"] for e in history["results"]] == [
            ["observed"], ["body_changed"], ["moved"]]
