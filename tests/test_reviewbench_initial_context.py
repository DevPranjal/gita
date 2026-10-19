"""Offline contracts for the common raw and arm-specific initial source views."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gita.eval.reviewbench import initial_context as context
from gita.eval.reviewbench.common import IntegrityError, ProtocolError
from gita.eval.reviewbench.evidence import Evidence


HEAD, BASE = "a" * 40, "b" * 40


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


@pytest.fixture
def harness(monkeypatch):
    files = {"src/code.py": b"".join(f"value_{i} = {i}\n".encode() for i in range(1, 501)),
             ".hidden": b"hidden\n", "image.bin": b"\0binary"}
    removed = {"deleted.py": b"old\n"}
    anchors = {"src/code.py": [(220, 1), (240, 1)]}
    raw_patch = b"diff --git a/code b/code\n" + b"+\\\\\xc3\xa9\n" * 2500
    evidence = Evidence.__new__(Evidence)
    evidence.repo = Path(".")
    evidence.head, evidence.base = HEAD, BASE
    evidence.allowed = (HEAD, BASE)
    evidence.issued = {}
    evidence.config = {"indexes": [{"commits": [HEAD]}], "scope_digest": "scope"}
    evidence.members = Mock(side_effect=lambda commit: {
        p: ("100644", blob(raw)) for p, raw in
        (files if commit == HEAD else {**files, **removed}).items()})
    evidence.blob = Mock(side_effect=lambda p, commit: files[p] if commit == HEAD
                         else pytest.fail("current source read outside HEAD"))
    evidence.close = Mock()
    database = sqlite3.connect(":memory:")
    database.executescript("CREATE TABLE version(id TEXT, kind TEXT);"
                           "CREATE TABLE definition(id TEXT, version_id TEXT);")
    refs = []
    session = SimpleNamespace(generation="generation", _store=SimpleNamespace(connection=database))
    session.entities_in = Mock(side_effect=lambda path, **kw: {
        "results": [r for r in refs if r["path"] == path.decode()],
        "page": {"next_cursor": None}})
    session.source = Mock(side_effect=lambda ref: files[ref["path"]][
        ref["span"]["start_byte"]:ref["span"]["end_byte"]])
    evidence._index = Mock(return_value=session)
    factory = Mock(return_value=evidence)
    monkeypatch.setattr(context, "Evidence", factory)

    def git(repo, *args):
        assert args[0] == "diff"
        assert "--no-ext-diff" in args and "--no-textconv" in args
        if "--name-only" in args:
            return b"".join(p.encode() + b"\0" for p in [*files, *removed])
        if "--unified=3" in args:
            return raw_patch
        path = args[-1].removeprefix(":(literal)")
        return "".join(f"@@ -{line},1 +{line},{count} @@\n"
                       for line, count in anchors.get(path, [])).encode()

    monkeypatch.setattr(context, "git", git)
    related = Mock(return_value={"sources": [], "symbols": [], "coverage": {"absence_claim": False}})
    monkeypatch.setattr(context, "related_sources", related)

    def add(kind, start, end, name="callable", path="src/code.py"):
        data = files[path]
        lines = data.splitlines(keepends=True)
        lo, hi = len(b"".join(lines[:start - 1])), len(b"".join(lines[:end]))
        identifier = str(len(refs))
        ref = {"id": identifier, "definition_id": identifier, "generation": "generation",
               "path": path, "path_bytes_b64": base64.b64encode(path.encode()).decode(),
               "commit": {"hex": HEAD}, "blob": {"hex": blob(data)}, "names": [name],
               "span": {"start_byte": lo, "end_byte": hi, "start_line": start, "end_line": end}}
        refs.append(ref)
        database.execute("INSERT INTO version VALUES (?, ?)", (identifier, kind))
        database.execute("INSERT INTO definition VALUES (?, ?)", (identifier, identifier))
        return ref

    yield SimpleNamespace(files=files, anchors=anchors, evidence=evidence, factory=factory,
                          related=related, add=add, refs=refs, session=session, raw=raw_patch)
    database.close()


def build(indexed=False, budget=100000):
    return context.build_initial_context({"private_path": "not-for-model",
                                         "cursor_secret": "not-for-model",
                                         "tool_log": "not-for-model"},
                                        indexed=indexed, max_characters=budget)


def test_same_raw_inventory_and_deterministic_wire(harness):
    baseline, indexed = build(), build(True)
    assert baseline["raw_patch"] == indexed["raw_patch"]
    assert baseline["inventory"] == indexed["inventory"]
    assert {r["path"] for r in baseline["inventory"]} == {
        "src/code.py", ".hidden", "image.bin", "deleted.py"}
    patch = baseline["raw_patch"]
    assert harness.raw.startswith(patch["text"].encode("utf8", "surrogateescape"))
    assert patch["sha256"] == hashlib.sha256(harness.raw).hexdigest()
    assert "next_cursor" not in context.serialize_context(baseline)
    assert "not-for-model" not in context.serialize_context(baseline)
    assert json.loads(context.serialize_context(baseline)) == baseline
    assert context.serialize_context(build()) == context.serialize_context(baseline)
    assert harness.factory.call_args.args[0]["arm"] == "B"
    assert "tool_log" not in harness.factory.call_args.args[0]
    assert harness.factory.call_args.args[0]["cursor_secret"] != "not-for-model"


def test_competitive_windows_merged_exact_HEAD_numbered_source(harness):
    packet = build()
    blocks = [b for b in packet["current_source"]["blocks"] if b["path"] == "src/code.py"]
    assert [(b["start_line"], b["end_line"]) for b in blocks] == [(1, 30), (120, 340)]
    lines = harness.files["src/code.py"].decode().splitlines(keepends=True)
    assert blocks[1]["text"] == "".join(f"{i}: {lines[i-1]}" for i in range(120, 341))
    assert all(b["commit"] == HEAD and b["blob"] == blob(harness.files[b["path"]]) for b in blocks)
    assert all(b["entities"] == [] for b in blocks)
    assert harness.related.call_args.kwargs["excluded_paths"] == {".hidden"}


def test_outermost_callable_not_module_or_class_and_no_query_parse(harness, monkeypatch):
    harness.add("module", 1, 500, "<module>")
    harness.add("class", 50, 450, "Class")
    outer = harness.add("function", 200, 280, "outer")
    harness.add("method", 215, 245, "inner")
    # Any accidental extraction path is forbidden in the query-time implementation.
    from gita.entities import extractor
    monkeypatch.setattr(extractor, "extract_entities", Mock(side_effect=AssertionError("query parse")),
                        raising=False)
    packet = build(True)
    blocks = [b for b in packet["current_source"]["blocks"] if b["path"] == "src/code.py"]
    assert [(b["start_line"], b["end_line"]) for b in blocks] == [(1, 30), (200, 280)]
    assert blocks[1]["entities"] == [{"name": "outer", "span": outer["span"],
                                     "generation": "generation", "complete": True}]
    assert harness.session.entities_in.call_args.kwargs["at"].digest.hex() == HEAD


@pytest.mark.parametrize("kind", ["module", "class", "unsupported_language"])
def test_noncallable_fallback_is_same_baseline_window(harness, kind):
    harness.add(kind, 1, 500)
    baseline, indexed = build(), build(True)
    b = [x for x in baseline["current_source"]["blocks"] if x["path"] == "src/code.py"]
    h = [x for x in indexed["current_source"]["blocks"] if x["path"] == "src/code.py"]
    assert [(x["start_line"], x["end_line"], x["text"]) for x in b] == [
        (x["start_line"], x["end_line"], x["text"]) for x in h]
    assert indexed["current_source"]["coverage"]["gaps"]["no_published_callable"] == 2


def test_deleted_zero_count_and_missing_index(harness):
    harness.evidence.config["indexes"] = []
    harness.anchors["src/code.py"] = [(220, 0)]
    harness.add("function", 200, 280, "not_a_deleted_entity")
    packet = build(True)
    deleted = next(r for r in packet["inventory"] if r["path"] == "deleted.py")
    assert deleted["source_reason"] == "deleted_or_missing_at_HEAD_raw_patch_only"
    assert not any(b["path"] == "deleted.py" for b in packet["current_source"]["blocks"])
    assert not any(b["entities"] for b in packet["current_source"]["blocks"])
    assert "zero_count_hunk_adjacent_HEAD_only_no_deleted_entity_claim" in (
        packet["current_source"]["coverage"]["gaps"])
    harness.evidence._index.assert_not_called()


def test_oversize_outer_callable_falls_back_not_inner(harness):
    harness.files["src/code.py"] = b"".join((b"#" + b"x" * 100 + b"\n") for _ in range(500))
    harness.add("function", 1, 500, "huge")
    harness.add("function", 210, 250, "inner")
    packet = build(True)
    assert packet["current_source"]["coverage"]["gaps"]["outermost_callable_exceeds_20000_bytes"] == 2
    assert not any(b["entities"] for b in packet["current_source"]["blocks"])


def test_corrupt_index_or_source_never_masked(harness):
    ref = harness.add("function", 200, 280)
    ref["blob"]["hex"] = "0" * 40
    with pytest.raises(IntegrityError):
        build(True)
    harness.evidence.close.assert_called_once()


@pytest.mark.parametrize("budget", [4000, 6000, 10000, 20000])
def test_actual_json_budget_raw_prefix_and_whole_blocks(harness, budget):
    harness.related.return_value = {"sources": [{"text": "x" * budget}], "symbols": []}
    baseline, indexed = build(budget=budget), build(True, budget)
    assert len(context.serialize_context(baseline)) <= budget
    assert baseline["raw_patch"] == indexed["raw_patch"]
    assert baseline["raw_patch"]["truncated"]
    assert baseline["raw_patch"]["omitted_bytes"] > 0
    assert baseline["related"]["omission"] == "whole_section_exceeds_budget"
    if budget <= 10000:
        assert baseline["current_source"]["coverage"]["omitted_blocks"] > 0
    for block in baseline["current_source"]["blocks"]:
        assert block["text"].startswith(f"{block['start_line']}: ")
        assert f"{block['end_line']}: " in block["text"]


@pytest.mark.parametrize("budget", [0, -1, True, 1, 1000])
def test_invalid_or_inventory_only_budget_raises(harness, budget):
    with pytest.raises(ProtocolError):
        build(budget=budget)


def test_related_inputs_are_local_source_only_and_full_files_only(harness):
    harness.files["src/code.py"] = b"result = ExternalThing()\n"
    packet = build()
    call = harness.related.call_args
    assert call.args[1] == ["ExternalThing"]
    assert call.kwargs["excluded_paths"] == {"src/code.py", ".hidden"}
    assert call.kwargs["indexed"] is False
    assert packet["coverage"]["absence_claim"] is False


@pytest.mark.parametrize("indexed", [False, True])
def test_global_changed_source_selection_precedes_earlier_file_header(harness, indexed):
    harness.files.clear()
    harness.files.update({"src/a.py": b"# " + b"x" * 20 + b"\n"})
    harness.files["src/a.py"] *= 500
    harness.files["src/z.py"] = b"def important():\n    return 42\n"
    harness.anchors.clear()
    harness.anchors.update({"src/a.py": [(250, 1)], "src/z.py": [(1, 2)]})
    harness.add("function", 1, 2, "important", path="src/z.py")
    packet = build(indexed, 5000)
    assert any(b["path"] == "src/z.py" and b["full_head_file"]
               for b in packet["current_source"]["blocks"])
    assert packet["current_source"]["coverage"]["omitted_blocks"] > 0
    assert len(context.serialize_context(packet)) <= 5000


def test_global_selection_density_ties_and_non_test_source_tier():
    def candidate(path, gain, start=1):
        return {"path": path, "text": "x" * 600, "start_line": start,
                "end_line": start + 10, "changed_head_lines": gain}

    header = candidate("src/a.py", 0)
    sparse = candidate("src/b.py", 1)
    dense = candidate("src/z.py", 5)
    test = candidate("tests/a.py", 100)
    candidates = [header, sparse, dense, test]
    empty = {"blocks": [], "coverage": {"candidate_blocks": 4, "omitted_blocks": 4}}
    budget = context._size(empty) + context._size(dense) + 1
    context._select_blocks(empty, candidates, budget)
    assert empty["blocks"] == [dense]
    assert empty["coverage"]["omitted_blocks"] == 3
    tied = {"blocks": [], "coverage": {}}
    context._select_blocks(tied, [candidate("src/z.py", 5), candidate("src/a.py", 5)], budget)
    assert tied["blocks"][0]["path"] == "src/a.py"


def test_changed_line_gain_counts_union_not_hunk_overlap(harness):
    harness.anchors["src/code.py"] = [(220, 5), (222, 5), (300, 0)]
    packet = build()
    blocks = [b for b in packet["current_source"]["blocks"] if b["path"] == "src/code.py"]
    assert sum(b["changed_head_lines"] for b in blocks) == 7
    assert blocks[0]["changed_head_lines"] == 0


def test_related_adapts_source_budget_to_fit_status_metadata(harness):
    attempts = []

    def related(evidence, terms, *, max_characters, **kwargs):
        attempts.append(max_characters)
        source = {"text": "x" * (max_characters - 50)}
        return {"sources": [source], "symbols": [{"status": "x" * 1000}]}

    harness.related.side_effect = related
    packet = build(budget=10000)
    assert len(attempts) == 2
    assert attempts[1] == attempts[0] // 2
    assert packet["related"]["sources"]
    assert len(packet["related"]["sources"][0]["text"]) == attempts[-1] - 50
    assert packet["related"]["symbols"]
    assert len(context.serialize_context(packet)) <= 10000


def test_related_metadata_alone_too_large_is_bounded_explicit_omission(harness):
    harness.related.return_value = {"sources": [], "symbols": [{"status": "x" * 10000}]}
    packet = build(budget=6000)
    assert harness.related.call_count == 3
    budgets = [call.kwargs["max_characters"] for call in harness.related.call_args_list]
    assert budgets == [budgets[0], max(2, budgets[0] // 2), max(2, budgets[0] // 4)]
    assert packet["related"] == {"view": "related", "omission": "whole_section_exceeds_budget"}
