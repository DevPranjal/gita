"""Offline related-source contracts: no models, network, or corpus execution."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import stat
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from gita.eval.reviewbench import related_source
from gita.eval.reviewbench.common import IntegrityError, ProtocolError, git
from gita.eval.reviewbench.evidence import Evidence
from gita.eval.reviewbench.related_source import related_sources


HEAD = "a" * 40
BASE = "b" * 40


def blob_id(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def broker(files: dict[str, bytes]) -> Evidence:
    evidence = Evidence.__new__(Evidence)
    evidence.head = HEAD
    evidence.base = BASE
    evidence.allowed = {HEAD, BASE}
    evidence.config = {"indexes": [], "scope_digest": "scope"}
    evidence.issued = {}
    evidence.members = Mock(side_effect=lambda commit: (
        {path: ("100644", blob_id(raw)) for path, raw in files.items()}
        if commit == HEAD else pytest.fail("non-HEAD membership read")))
    evidence.blob = Mock(side_effect=lambda path, commit: (
        files[path] if commit == HEAD else pytest.fail("non-HEAD source read")))
    evidence.repo = Path(".")
    evidence._index = Mock(side_effect=AssertionError("unexpected index access"))
    return evidence


def occurrence(path: str, raw: bytes, start: int = 0) -> dict:
    end = len(raw)
    return {"id": "occurrence-" + path, "generation": "generation",
            "path": path, "path_bytes_b64": base64.b64encode(path.encode()).decode(),
            "commit": {"hex": HEAD, "object_format": "sha1"},
            "blob": {"hex": blob_id(raw)}, "names": ["Foo"],
            "span": {"start_byte": start, "end_byte": end,
                     "start_line": 1 + raw[:start].count(b"\n"),
                     "end_line": max(1 + raw[:start].count(b"\n"),
                                     1 + raw[:end].count(b"\n") - int(raw.endswith(b"\n")))}}


def index(evidence: Evidence, refs: list[dict], *, page_size: int = 20) -> Mock:
    evidence.config["indexes"] = [{"commits": [HEAD]}]
    session = Mock()
    session.generation = "generation"

    def resolve(token, *, at, historical, limit, cursor):
        assert at.digest.hex() == HEAD
        assert historical is False
        assert limit == 20
        offset = int(cursor or "0")
        selected = refs[offset:offset + page_size]
        next_offset = offset + len(selected)
        return {"results": [{"occurrence": ref} for ref in selected],
                "page": {"total": len(refs),
                         "next_cursor": str(next_offset) if next_offset < len(refs) else None}}

    session.resolve.side_effect = resolve
    session.source.side_effect = lambda ref: evidence.blob(ref["path"], HEAD)[
        ref["span"]["start_byte"]:ref["span"]["end_byte"]]
    evidence._index = Mock(return_value=session)
    return session


def raw_grep(monkeypatch, paths: list[str], declarations: list[str] | None = None):
    def grep(repo, *args, **kwargs):
        assert args[:4] == ("grep", "-l", "-z", "-I")
        assert args[4] in ("-E", "-F")
        assert args[5] == "-e" if args[4] == "-E" else args[5:7] == ("-w", "-e")
        assert args[-2:] == (HEAD, "--")
        assert kwargs == {"accepted": (0, 1)}
        selected = paths if args[4] == "-F" or declarations is None else declarations
        return b"".join((HEAD + ":" + path).encode() + b"\0" for path in selected)
    monkeypatch.setattr(related_source, "git", grep)


def test_index_exact_definition_and_independent_git_validation():
    raw = b"# unrelated prefix\nclass Foo:\n    value = '\xc3\xa9'\n"
    evidence = broker({"src/foo.py": raw})
    ref = occurrence("src/foo.py", raw, len(b"# unrelated prefix\n"))
    session = index(evidence, [ref])
    result = related_sources(evidence, ["Foo"], indexed=True)
    source = result["sources"][0]
    assert source["text"].encode() == raw[ref["span"]["start_byte"]:]
    assert source["span"] == ref["span"]
    assert source["commit"] == HEAD
    assert source["blob"] == blob_id(raw)
    assert source["source_kind"] == "indexed_definition"
    assert result["symbols"][0]["status"] == "resolved_definition"
    cast(Mock, evidence._index).assert_called_once_with(HEAD)
    assert session.source.call_count >= 2


def test_index_all_ambiguous_matches_and_cursors():
    files = {f"src/{i}.py": b"class Foo: pass\n" for i in range(3)}
    evidence = broker(files)
    session = index(evidence, [occurrence(path, raw) for path, raw in files.items()], page_size=1)
    result = related_sources(evidence, ["Foo"], indexed=True)
    assert len(result["sources"]) == 3
    assert result["symbols"][0]["ambiguity"] is True
    assert result["symbols"][0]["match_count"] == 3
    assert session.resolve.call_count == 3


def test_index_match_limit_explicit():
    files = {f"src/{i}.py": b"class Foo: pass\n" for i in range(25)}
    evidence = broker(files)
    session = index(evidence, [occurrence(path, raw) for path, raw in files.items()])
    result = related_sources(evidence, ["Foo"], indexed=True)
    assert len(result["sources"]) == 20
    assert result["symbols"][0]["omitted_by_match_limit"] == 5
    assert result["symbols"][0]["has_more"] is True
    assert session.resolve.call_count == 1


def test_raw_fallback_identical_to_baseline_and_priority(monkeypatch):
    files = {"docs/foo.md": b"Foo docs\n", "tests/foo.py": b"Foo test\n",
             "src/foo.py": b"# Foo\nnot_a_definition = True\n"}
    evidence = broker(files)
    raw_grep(monkeypatch, list(files))
    baseline = related_sources(evidence, ["Foo"], indexed=False)
    fallback = related_sources(evidence, ["Foo"], indexed=True)
    assert baseline["sources"] == fallback["sources"]
    assert fallback["coverage"]["index_gap"]
    assert fallback["source_kind"] == "raw_git_file"
    assert baseline["sources"][0]["path"] == "src/foo.py"
    assert baseline["sources"][0]["text"] == files["src/foo.py"].decode()
    assert baseline["sources"][0]["name"] is None
    assert "heuristic" in baseline["coverage"]["source_priority"]


@pytest.mark.parametrize("symbols", [["--help"], ["Foo;bar"], ["Foo\nbar"], [""],
                                     ["a" * 81], ["foo.bar"], ["é"], [1],
                                     ["Foo"] * 17, "Foo"])
def test_invalid_identifiers_refused_before_reads(symbols):
    evidence = broker({})
    with pytest.raises(ProtocolError):
        related_sources(evidence, symbols, indexed=False)
    cast(Mock, evidence.members).assert_not_called()


def test_whole_source_budget_uses_serialized_characters(monkeypatch):
    evidence = broker({"src/foo.py": b'Foo = "' + b"\\" * 100 + b'"\n'})
    raw_grep(monkeypatch, ["src/foo.py"])
    complete = related_sources(evidence, ["Foo"], indexed=False)
    size = len(json.dumps(complete["sources"], ensure_ascii=True))
    assert complete["coverage"]["serialized_source_characters"] == size
    omitted = related_sources(evidence, ["Foo"], indexed=False, max_characters=size - 1)
    assert omitted["sources"] == []
    assert len(omitted["coverage"]["omitted_by_budget"]) == 1
    assert omitted["symbols"][0]["candidates"][0]["omission"] == "whole_source_exceeds_budget"
    assert related_sources(evidence, ["Foo"], indexed=False, max_characters=size)["sources"] == complete["sources"]


def test_dedup_files_symbols_and_excluded_paths(monkeypatch):
    evidence = broker({"src/foo.py": b"Foo Bar\n"})
    raw_grep(monkeypatch, ["src/foo.py", "src/foo.py"])
    result = related_sources(evidence, ["Foo", "Bar", "Foo"], indexed=False)
    assert len(result["sources"]) == 1
    assert len(result["symbols"]) == 2
    assert all(row["candidates"][0]["included"] for row in result["symbols"])
    excluded = related_sources(evidence, ["Foo", "Bar"], indexed=False,
                               excluded_paths={"src/foo.py"})
    assert excluded["sources"] == []
    assert len(excluded["coverage"]["excluded_sources"]) == 1
    assert excluded["symbols"][0]["candidates"][0]["omission"] == "already_included_full_head_file"


def test_index_deduplicates_occurrences_across_symbols():
    raw = b"class Foo: pass\n"
    evidence = broker({"foo.py": raw})
    ref = occurrence("foo.py", raw)
    index(evidence, [ref, ref])
    result = related_sources(evidence, ["Foo", "Bar"], indexed=True)
    assert len(result["sources"]) == 1
    assert all(len(row["candidates"]) == 1 for row in result["symbols"])


@pytest.mark.parametrize("error", [IntegrityError("corrupt"), ProtocolError("malformed")])
def test_index_open_corruption_is_not_swallowed(error):
    evidence = broker({})
    evidence.config["indexes"] = [{"commits": [HEAD]}]
    evidence._index = Mock(side_effect=error)
    with pytest.raises(type(error), match=str(error)):
        related_sources(evidence, ["Foo"], indexed=True)


def test_expected_typed_unavailability_falls_back(monkeypatch):
    evidence = broker({"foo.py": b"Foo\n"})
    evidence.config["indexes"] = [{"commits": [HEAD]}]
    evidence._index = Mock(side_effect=ProtocolError(related_source._UNAVAILABLE))
    raw_grep(monkeypatch, ["foo.py"])
    assert related_sources(evidence, ["Foo"], indexed=True)["source_kind"] == "raw_git_file"


@pytest.mark.parametrize("tamper", ["source", "commit", "blob"])
def test_index_integrity_mismatch_propagates(tamper):
    raw = b"class Foo: pass\n"
    evidence = broker({"foo.py": raw})
    ref = occurrence("foo.py", raw)
    session = index(evidence, [ref])
    if tamper == "source":
        session.source.side_effect = None
        session.source.return_value = b"wrong"
    elif tamper == "commit":
        ref["commit"]["hex"] = BASE
        # Real Evidence accepts allowed BASE; the helper must still require HEAD.
        evidence._occurrences = Mock()
    else:
        ref["blob"]["hex"] = "c" * 40
    with pytest.raises(IntegrityError):
        related_sources(evidence, ["Foo"], indexed=True)


def test_unresolved_not_absence_or_parser_coverage_claim():
    evidence = broker({})
    index(evidence, [])
    result = related_sources(evidence, ["Foo"], indexed=True)
    assert result["coverage"]["unresolved"] == ["Foo"]
    assert result["coverage"]["absence_claim"] is False
    assert "parser" in result["coverage"]["limitations"]


def test_nonmember_raw_results_fail_closed(monkeypatch):
    evidence = broker({})
    raw_grep(monkeypatch, ["not-a-member.py"])
    with pytest.raises(IntegrityError):
        related_sources(evidence, ["Foo"], indexed=False)


def test_real_git_reads_committed_head_not_worktree():
    root = Path(__file__).resolve().parents[1] / (".related-source-test-" + uuid4().hex)
    root.mkdir()
    try:
        git(root, "init", "-q")
        (root / "foo.py").write_bytes(b"Foo = 1\n")
        git(root, "add", "--", "foo.py")
        git(root, "-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
            "commit", "-q", "-m", "fixture")
        head = git(root, "rev-parse", "HEAD").decode().strip()
        (root / "foo.py").write_bytes(b"Foo = 999\n")
        evidence = Evidence.__new__(Evidence)
        evidence.repo = root
        evidence.head = head
        evidence.allowed = {head}
        evidence.config = {"indexes": []}
        evidence._paths = {}
        evidence._blobs = {}
        result = related_sources(evidence, ["Foo"], indexed=False)
        assert result["sources"][0]["text"] == "Foo = 1\n"
        assert result["sources"][0]["commit"] == head
    finally:
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(root)


def test_raw_declaration_preferred_over_references_and_tests(monkeypatch):
    files = {"tests/usage.py": b"assert Widget()\n", "src/usage.py": b"use(Widget)\n",
             "src/widget.py": b"class Widget:\n    pass\n"}
    evidence = broker(files)
    raw_grep(monkeypatch, list(files), declarations=["src/widget.py"])
    result = related_sources(evidence, ["Widget"], indexed=False)
    assert [source["path"] for source in result["sources"]] == ["src/widget.py"]
    status = result["symbols"][0]
    assert status["lookup_kind"] == "declaration_locator_heuristic"
    assert status["declaration_match_count"] == 1
    assert status["reference_match_count"] == 2
    assert status["candidates"][0]["match_kind"] == "declaration_locator_heuristic"
    cast(Mock, evidence.blob).assert_called_once_with("src/widget.py", HEAD)


def test_reference_only_is_truthful_and_does_not_load_files(monkeypatch):
    evidence = broker({"tests/usage.py": b"assert Widget()\n"})
    raw_grep(monkeypatch, ["tests/usage.py"], declarations=[])
    result = related_sources(evidence, ["Widget"], indexed=False)
    assert result["sources"] == []
    status = result["symbols"][0]
    assert status["status"] == "reference_only_not_absence"
    assert status["reference_match_count"] == 1
    assert status["candidates"][0]["path"] == "tests/usage.py"
    assert status["candidates"][0]["span"] is None
    assert status["candidates"][0]["omission"] == "reference_only_not_loaded"
    assert result["coverage"]["unresolved"] == ["Widget"]
    assert result["coverage"]["absence_claim"] is False
    cast(Mock, evidence.blob).assert_not_called()


def test_global_priority_allocates_budget_before_earlier_test_symbol(monkeypatch):
    files = {"tests/a.py": b"class TestThing: pass\n", "src/z.py": b"class RealThing: pass\n"}
    evidence = broker(files)

    def grep(repo, *args, **kwargs):
        assert args[-2:] == (HEAD, "--")
        query = args[-3]
        path = "tests/a.py" if "TestThing" in query else "src/z.py"
        return (HEAD + ":" + path).encode() + b"\0"

    monkeypatch.setattr(related_source, "git", grep)
    complete = related_sources(evidence, ["TestThing", "RealThing"], indexed=False)
    size = len(json.dumps([complete["sources"][0]], ensure_ascii=True))
    bounded = related_sources(evidence, ["TestThing", "RealThing"], indexed=False,
                              max_characters=size)
    assert [source["path"] for source in bounded["sources"]] == ["src/z.py"]
    assert bounded["symbols"][0]["candidates"][0]["omission"] == "whole_source_exceeds_budget"
    assert bounded["symbols"][1]["candidates"][0]["included"] is True
    reversed_terms = related_sources(evidence, ["RealThing", "TestThing"], indexed=False,
                                    max_characters=size)
    assert reversed_terms["sources"] == bounded["sources"]


def test_real_git_declaration_locator_forms_and_reference_only():
    root = Path(__file__).resolve().parents[1] / (".related-source-test-" + uuid4().hex)
    root.mkdir()
    examples = {
        "Kind": "class Kind:\n    pass\n",
        "operation": "async def operation(value):\n    return value\n",
        "execute": "export function execute(value) { return value; }\n",
        "Contract": "public interface Contract {}\n",
        "Storage": "struct Storage {}\n",
        "Choice": "enum Choice { A }\n",
        "Alias": "type Alias = string;\n",
        "Behavior": "trait Behavior {}\n",
        "Entry": "public record Entry(string Value);\n",
        "Package": "module Package\nend\n",
        "setting": "const setting = 1;\n",
        "member": "  public member(value) { return value; }\n",
        "calculate": "  public static int calculate(int x) { return x; }\n",
        "option": '  "option": true,\n',
        "configured": "self.configured = True\n",
        "yaml_value": "yaml_value: true\n",
        "operate": "func (r Receiver) operate() {}\n",
    }
    try:
        git(root, "init", "-q")
        for token, text in examples.items():
            (root / (token + ".txt")).write_bytes(text.encode("utf8"))
        (root / "references.py").write_bytes(b"use(ExternalThing)\n")
        git(root, "add", "--", ".")
        git(root, "-c", "user.name=Offline", "-c", "user.email=offline@example.invalid",
            "commit", "-q", "-m", "declaration locator fixture")
        head = git(root, "rev-parse", "HEAD").decode().strip()
        evidence = Evidence.__new__(Evidence)
        evidence.repo = root
        evidence.head = head
        evidence.allowed = {head}
        evidence.config = {"indexes": []}
        evidence._paths = {}
        evidence._blobs = {}
        for token in examples:
            result = related_sources(evidence, [token], indexed=False)
            assert result["symbols"][0]["declaration_match_count"] == 1, token
            assert result["sources"][0]["text"] == examples[token]
        result = related_sources(evidence, ["ExternalThing"], indexed=False)
        assert result["sources"] == []
        assert result["symbols"][0]["status"] == "reference_only_not_absence"
    finally:
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(root)
