import json

import pytest

from gita.eval.reviewbench import judge
from gita.eval.reviewbench.campaign import slot_id
from gita.eval.reviewbench.common import ProtocolError, append_json, write_json


def test_supporting_paths_union_both_arms_and_use_only_visible_preloads(tmp_path):
    rows = []
    for arm in ("B", "H"):
        identity = {"task": "task", "arm": arm, "repeat": 0, "attempt": 0}
        directory = tmp_path / "attempts" / slot_id(identity)
        directory.mkdir(parents=True)
        append_json(directory / "tools.jsonl", {
            "ok": True, "tool": "read_file", "arguments": {"path": arm + ".py"}, "result": {}})
        append_json(directory / "tools.jsonl", {
            "ok": False, "tool": "read_file", "arguments": {"path": "failed.py"}, "result": {}})
        append_json(directory / "tools.jsonl", {
            "ok": True, "tool": "gita_review", "arguments": {"operation": "source", "token": "Foo"},
            "result": {"entity": {"path": "named.py"}}})
        append_json(directory / "tools.jsonl", {
            "ok": True, "tool": "gita_review",
            "arguments": {"operation": "context", "token": "Bar", "path": "importer.py"},
            "result": {"entity": {"path": "provider.py"}}})
        append_json(directory / "tools.jsonl", {
            "ok": True, "tool": "gita_review",
            "arguments": {"operation": "context", "token": "VALUE", "path": "config.py"},
            "result": {"source_location": {"path": "config.py"}}})
        write_json(directory / "initial_context.txt", {
            "current_source": {"blocks": [{"path": "visible.py"}]}, "related": {"omission": "on_demand"}})
        write_json(directory / "initial_context.json", {
            "related": {"sources": [{"path": "private_unused.py"}]}})
        rows.append((arm, {"identity": identity, "initial_evidence": {"packet_digest": "packet"}}))
    assert judge.requested_source_paths(tmp_path, rows) == [
        "B.py", "H.py", "config.py", "named.py", "provider.py", "visible.py"]


@pytest.fixture
def source_files(monkeypatch):
    class Source:
        closed = False
        files = {"changed.py": b"def changed():\n    return 1\n",
                 "helper.py": b"class Dependency:\n    pass\n",
                 "binary.bin": b"\x00\x01", "large.py": b"x" * 200000}

        def paths(self, commit):
            return set(self.files)

        def blob(self, path, commit):
            return self.files[path]

        def members(self, commit):
            return {path: ("100644", "a" * 40) for path in self.files}

        def close(self):
            self.closed = True

    source = Source()
    monkeypatch.setattr(judge, "Evidence", lambda config: source)
    return source


def test_judge_gets_canonical_supporting_head_files_without_repeating_complete_snippets(source_files):
    scope = {"task": {"head": "h"}, "effective_base": "b"}
    candidates = [{"file": "changed.py", "start_line": 1, "end_line": 2}]
    context = judge.source_context(scope, candidates, [], targeted=True,
                                   supporting_paths=["helper.py", "changed.py", "binary.bin", "absent.py"])
    assert len(context["supporting_sources"]) == 1
    helper = context["supporting_sources"][0]
    assert helper["file"] == "helper.py" and helper["commit"] == "h"
    assert helper["source"].encode("utf8") == source_files.files["helper.py"]
    coverage = context["supporting_source_coverage"]
    assert coverage["already_complete_in_snippets"] == ["changed.py"]
    assert {row["reason"] for row in coverage["omitted"]} == {"binary", "not_present_at_HEAD"}
    assert coverage["absence_claim"] is False and source_files.closed


def test_supporting_source_overflow_is_explicit_and_never_partial(source_files):
    context = judge.source_context(
        {"task": {"head": "h"}, "effective_base": "b"}, [], [],
        targeted=True, supporting_paths=["large.py"])
    assert not context["supporting_sources"]
    assert context["supporting_source_coverage"]["omitted"] == [
        {"path": "large.py", "reason": "whole_source_budget"}]
    assert len(json.dumps(context)) <= 180000


def test_supporting_source_cannot_escape_repository(source_files):
    with pytest.raises(ProtocolError):
        judge.source_context({"task": {"head": "h"}, "effective_base": "b"}, [], [],
                             targeted=True, supporting_paths=["../outside.py"])
