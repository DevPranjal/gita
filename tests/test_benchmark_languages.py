from hashlib import blake2b
import subprocess

import pytest

from gita.entities.extractor import extract
from gita.entities.languages import for_path
from gita.index.evidence import parse_evidence


CASES = [
    ("Example.java", b"""package demo;
interface API { int read(int x); }
class Store {
    int count;
    Store() {}
    int read(int x) { int local = 1; return x + local; }
}
""", {
        (("demo",), "namespace"), (("demo", "API"), "interface"),
        (("demo", "API", "read"), "method"), (("demo", "Store"), "class"),
        (("demo", "Store", "count"), "property"), (("demo", "Store", "Store"), "method"),
        (("demo", "Store", "read"), "method"),
    }),
    ("Example.kt", b"""package demo
interface API {
    fun read(x: Int): Int
}
class Store {
    var count = 1
    fun read(x: Int): Int { return x }
}
object Settings {
    val enabled = true
}
fun render(x: Int) = x
""", {
        (("demo",), "namespace"), (("demo", "API"), "interface"),
        (("demo", "API", "read"), "method"), (("demo", "Store"), "class"),
        (("demo", "Store", "count"), "property"), (("demo", "Store", "read"), "method"),
        (("demo", "Settings"), "class"), (("demo", "Settings", "enabled"), "property"),
        (("demo", "render"), "function"),
    }),
    ("example.c", b"""typedef int Count;
struct Store { int value; };
int compute(int value) { return value + 1; }
int declared(int value);
int (*callback)(int);
#define PLUS(x) ((x)+1)
""", {
        (("Count",), "type"), (("Store",), "struct"), (("compute",), "function"),
        (("declared",), "function"), (("PLUS",), "function"),
    }),
    ("example.rb", b"""module Demo
  class Store
    def read(value)
      value
    end
    def self.create
      new
    end
  end
end
""", {
        (("Demo",), "namespace"), (("Demo", "Store"), "class"),
        (("Demo", "Store", "read"), "method"), (("Demo", "Store", "create"), "method"),
    }),
    ("example.sh", b"""#!/bin/bash
function fetch() { echo " a "; }
write() { printf "%s" "$1"; }
""", {(("fetch",), "function"), (("write",), "function")}),
    ("Example.ps1", b"""function Get-Value {
    param([int]$Count)
    return $Count
}
class Store {
    [int] $Count
    [int] Read([int] $x) { return $x }
}
""", {
        (("Get-Value",), "function"), (("Store",), "class"),
        (("Store", "Count"), "property"), (("Store", "Read"), "method"),
    }),
    ("example.tf", b"""resource "local_file" "report" {
    filename = "report.txt"
    content = " a "
}
variable "count" {
    type = number
    default = 1
}
locals { enabled = true }
""", {
        (("resource.local_file.report",), "section"),
        (("resource.local_file.report", "filename"), "section"),
        (("resource.local_file.report", "content"), "section"),
        (("variable.count",), "section"), (("variable.count", "type"), "section"),
        (("variable.count", "default"), "section"),
        (("locals",), "section"), (("locals", "enabled"), "section"),
    }),
    ("example.dart", b"""class Store {
    int read(int value) { return value + 1; }
}
int render(int value) {
    int local(int x) => x;
    return local(value);
}
""", {
        (("Store",), "class"), (("Store", "read"), "method"),
        (("render",), "function"), (("render", "local"), "function"),
    }),
]


@pytest.mark.parametrize(("path", "source", "expected"), CASES)
def test_major_language_named_bindings_and_exact_byte_regions(path, source, expected):
    result = parse_evidence(source, path.encode())
    assert result.result.complete, result.result.diagnostics
    definitions = [d for d in result.result.definitions if not d.synthetic_root]
    assert {(d.names, d.kind) for d in definitions} == expected
    assert len(definitions) == len(expected)
    versions = {v.id: v for v in result.versions}
    by_id = {d.id: d for d in result.result.definitions}
    for definition in definitions:
        span = definition.span
        assert versions[definition.version_id].source_digest == blake2b(
            source[span.start_byte:span.end_byte], digest_size=32).digest()
        parent = by_id[definition.parent_id]
        assert parent.span.start_byte <= span.start_byte <= span.end_byte <= parent.span.end_byte
        if not parent.synthetic_root:
            assert definition.names[:-1] == parent.names
    assert result == parse_evidence(source, path.encode())
    legacy = extract(source, path)
    assert not legacy.parse_error
    assert {(e.name, e.kind.value, e.start_line) for e in legacy.walk() if not e.synthetic} == {
        (d.names[-1], d.kind, d.span.start_line) for d in definitions}


@pytest.mark.parametrize(("path", "language"), [
    ("Example.java", "java"), ("example.kt", "kotlin"), ("build.kts", "kotlin"),
    ("example.c", "c"), ("example.rb", "ruby"), ("build.rake", "ruby"),
    ("example.sh", "bash"), ("example.bash", "bash"),
    ("example.ps1", "powershell"), ("module.psm1", "powershell"), ("module.psd1", "powershell"),
    ("example.hcl", "hcl"), ("main.tf", "hcl"), ("values.tfvars", "hcl"),
    ("example.dart", "dart"),
])
def test_major_language_extension_registration(path, language):
    assert for_path(path).name == language
    assert for_path(path.upper()).name == language


TEXT_CASES = [
    ("text.java", b'class Text { String text() { return " a "; } }\n'),
    ("text.kt", b'fun text(): String { return " a " }\n'),
    ("text.c", b'const char *text(void) { return " a "; }\n'),
    ("text.rb", b'def text\n  " a "\nend\n'),
    ("text.sh", b'text() { printf "%s" " a "; }\n'),
    ("text.ps1", b'function text {\n  return " a "\n}\n'),
    ("text.tf", b'locals { text = " a " }\n'),
    ("text.dart", b'String text() { return " a "; }\n'),
    ("heredoc.tf", b'locals { text = <<EOT\n a \nEOT\n}\n'),
]


@pytest.mark.parametrize(("path", "source"), TEXT_CASES)
def test_literal_whitespace_changes_body_evidence_in_both_extractors(path, source):
    changed = source.replace(b" a ", b"a")
    bodies = []
    legacy_bodies = []
    for raw in (source, changed):
        bundle = parse_evidence(raw, path.encode())
        assert bundle.result.complete, bundle.result.diagnostics
        target = next(d for d in bundle.result.definitions if d.names[-1] == "text")
        bodies.append(next(v.body for v in bundle.versions if v.id == target.version_id))
        tree = extract(raw, path)
        legacy_bodies.append(next(e.body_tokens for e in tree.walk() if e.name == "text"))
    assert bodies[0] != bodies[1]
    assert legacy_bodies[0] != legacy_bodies[1]


@pytest.mark.parametrize(("path", "source", "token", "stable_move"), [
    ("Example.java", b'class Store { int compute(int value) { return value + 1; } }\n', "compute", True),
    ("example.kt", b'fun compute(value: Int): Int { return value + 1 }\n', "compute", True),
    ("example.c", b'int compute(int value) { return value + 1; }\n', "compute", True),
    ("example.rb", b'def compute(value)\n  value + 1\nend\n', "compute", True),
    ("example.sh", b'compute() { echo "$((value + 1))"; }\n', "compute", True),
    ("example.ps1", b'function Compute {\n param([int]$value)\n return $value + 1\n}\n', "Compute", True),
    ("example.tf", b'locals { value = (1 + 3 + 4 + 5) }\n', "locals.value", True),
    ("example.dart", b'int compute(int value) { return value + 1; }\n', "compute", True),
    pytest.param("minimal.tf", b"locals { value = 1 }\n", "locals.value", False,
                 id="tiny-hcl-does-not-force-cross-file-identity"),
])
def test_major_languages_publish_stable_edit_move_history(tmp_path, path, source, token, stable_move):
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
    original = tmp_path / path
    original.write_bytes(source)
    git("add", "--", path)
    git("commit", "--no-gpg-sign", "-m", "base")
    commits = [git("rev-parse", "HEAD").decode().strip()]
    changed = source.replace(b"1", b"2")
    original.write_bytes(changed)
    git("add", "--", path)
    git("commit", "--no-gpg-sign", "-m", "body change")
    commits.append(git("rev-parse", "HEAD").decode().strip())
    original.rename(tmp_path / ("moved" + original.suffix))
    git("add", "-A")
    git("commit", "--no-gpg-sign", "-m", "move")
    commits.append(git("rev-parse", "HEAD").decode().strip())
    with CommittedRepo(tmp_path) as repository, IndexStore.create(IndexStore.locate(Repo(tmp_path))) as store:
        build(repository, store, scope="language-test", ref=commits[-1], since=commits[0],
              limits=BuildLimits(workers=1))
    with CommittedRepo(tmp_path) as repository, open_index(repository, scope="language-test") as session:
        ids = []
        for commit, raw in zip(commits, (source, changed, changed), strict=True):
            at = GitOid.from_hex("sha1", commit)
            resolution = session.resolve(token, at=at)
            assert resolution["status"] == "unique", resolution
            eid = resolution["results"][0]["eid"]
            ids.append(eid)
            occurrence = session.state_at(eid, at)["occurrence"]
            span = occurrence["span"]
            assert session.source(occurrence) == raw[span["start_byte"]:span["end_byte"]]
        assert ids[0] == ids[1]
        flags = [e["flags"] for e in session.history(ids[-1], order="oldest")["results"]]
        if stable_move:
            assert ids[2] == ids[1]
            assert flags == [["observed"], ["body_changed"], ["moved"]]
        else:
            assert ids[2] != ids[1]
            assert flags == [["observed"]]


def test_dart_callable_projection_contains_bodies_and_correct_local_parent():
    source = b"""int render(int x) {
  // This belongs to the outer function.
  int local(int y) => y + 1;
  return local(x);
}
"""
    result = parse_evidence(source, b"example.dart")
    assert result.result.complete
    definitions = {d.names: d for d in result.result.definitions}
    outer, inner = definitions[("render",)], definitions[("render", "local")]
    assert source[outer.span.start_byte:outer.span.end_byte] == source.rstrip(b"\n")
    assert source[inner.span.start_byte:inner.span.end_byte] == b"int local(int y) => y + 1;"
    assert inner.parent_id == outer.id


def test_dart_projection_keeps_intervening_comments_and_separate_callable_boundaries():
    source = b"""int first()
// A comment between signature and implementation.
{ return 1; }
int get next => 2;
"""
    bundle = parse_evidence(source, b"example.dart")
    assert bundle.result.complete
    definitions = {d.names: d for d in bundle.result.definitions}
    first, next_value = definitions[("first",)], definitions[("next",)]
    assert source[first.span.start_byte:first.span.end_byte] == source[:source.index(b"\nint get next")]
    assert source[next_value.span.start_byte:next_value.span.end_byte] == b"int get next => 2;"
    assert first.span.end_byte <= next_value.span.start_byte


def test_dart_inner_body_change_is_not_duplicated_in_outer_owned_tokens():
    source = b"int outer(int x) { int inner(int y) => y + 1; return inner(x); }\n"
    before, after = [parse_evidence(raw, b"example.dart")
                     for raw in (source, source.replace(b"+ 1", b"+ 2"))]
    versions = [{v.id: v for v in bundle.versions} for bundle in (before, after)]
    outer = [next(d for d in bundle.result.definitions if d.names == ("outer",))
             for bundle in (before, after)]
    inner = [next(d for d in bundle.result.definitions if d.names == ("outer", "inner"))
             for bundle in (before, after)]
    assert versions[0][outer[0].version_id].own == versions[1][outer[1].version_id].own
    assert versions[0][inner[0].version_id].body != versions[1][inner[1].version_id].body
