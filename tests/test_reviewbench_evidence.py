from copy import deepcopy
import json
from pathlib import Path
import secrets

import pytest

from gita.eval.reviewbench.common import (ProtocolError, digest, git, pr_identity,
                                         task_identity, validate_findings)
from gita.eval.reviewbench.evidence import Evidence, MAX_TOOL_WIRE_CHARS, inventory, matches_path
from gita.eval.reviewbench.preparation import build_indexes, preflight, source_scope


@pytest.fixture
def fixture_repo(tmp_path, request):
    repository = tmp_path / "source"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.name", "Synthetic Test")
    git(repository, "config", "user.email", "synthetic@example.invalid")
    relative = "src/code.py" if getattr(request, "param", None) == "nested" else "code.py"
    source = repository.joinpath(*relative.split("/"))
    source.parent.mkdir(parents=True, exist_ok=True)
    duplicate = getattr(request, "param", None) == "duplicate"
    padding = ("    # " + "x" * 80 + "\n") * 100 if getattr(request, "param", None) == "large" else ""
    if getattr(request, "param", None) == "escaped":
        padding = ("    # " + '\\"' * 45 + "\n") * 99
    source.write_text("def divide(value):\n" + padding + "    return 10 / value\n"
                      + ("\ndef divide(other):\n    return 30 / other\n" if duplicate else ""),
                      encoding="utf8")
    extras = []
    if getattr(request, "param", None) == "empty":
        (repository / "empty.py").write_bytes(b"")
        extras.append("empty.py")
    git(repository, "add", "--", relative, *extras)
    git(repository, "commit", "--no-gpg-sign", "-m", "base")
    base = git(repository, "rev-parse", "HEAD").decode().strip()
    source.write_text("def divide(value):\n" + padding.replace("x", "y") + "    return 20 / value\n"
                      + ("\ndef divide(other):\n    return 40 / other\n" if duplicate else ""),
                      encoding="utf8")
    git(repository, "add", "--", relative)
    git(repository, "commit", "--no-gpg-sign", "-m", "review")
    head = git(repository, "rev-parse", "HEAD").decode().strip()
    source.write_text("SECRET_FUTURE_LABEL = 'do not leak'\n", encoding="utf8")
    git(repository, "add", "--", relative)
    git(repository, "commit", "--no-gpg-sign", "-m", "future label fix")
    future = git(repository, "rev-parse", "HEAD").decode().strip()
    task = {"repo": "https://github.com/synthetic/repo", "pr_number": 1, "base": base, "head": head}
    task["id"] = task_identity(task)
    scope = source_scope(task, repository)
    return repository, scope, future


def config(scope, arm="H"):
    return {**scope, "arm": arm, "cursor_secret": secrets.token_hex(32)}


@pytest.mark.parametrize(("path", "pattern", "expected"), [
    ("root.py", "**/*.py", True),
    ("src/deep/code.py", "src/**/*.py", True),
    ("src/a/b/code.py", "**/a/**/code.py", True),
    ("src/code.py", "src/**/nested/**/*.py", False),
    ("src/code.ts", "src/**/*.py", False),
    ("src2/code.py", "src", False),
    ("src/code.py", "src/no.py", False),
    ("src/deep/code.py", "*.py", True),
    ("src/code.py", "src/[a-z]*.py", True),
])
def test_path_filter_preserves_existing_globs_and_recursive_boundaries(path, pattern, expected):
    assert matches_path(path, pattern) is expected


@pytest.mark.parametrize("fixture_repo", ["nested"], indirect=True)
@pytest.mark.parametrize("pattern", ["src/**/*.py", "**/code.py", "src", "src/"])
@pytest.mark.parametrize("arm", ["B", "H"])
def test_recursive_path_filters_include_zero_depth_and_directory_members(fixture_repo, pattern, arm):
    _, scope, _ = fixture_repo
    e = Evidence(config(scope, arm))
    try:
        found = e.execute("search", {"text": "return", "path": pattern})
        assert "src/code.py:2:    return 20 / value" in found["text"]
        assert found["coverage"]["matched_files"] == 1
        listed = e.execute("list_files", {"pattern": pattern})
        assert listed["text"] == "src/code.py"
        assert listed["coverage"]["matched_files"] == 1
        assert "SECRET_FUTURE_LABEL" not in found["text"]
        empty = e.execute("search", {"text": "return", "path": "absent/**/*.py"})
        assert empty["text"] == "" and empty["coverage"]["matched_files"] == 0
    finally:
        e.close()


def test_entity_context_returns_exact_named_source_without_copying_references(fixture_repo, monkeypatch):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    assert indexed["indexes"], indexed["index_preparation"]
    evidence = Evidence(config(indexed))
    try:
        result = evidence.execute("entity_context", {"path": "code.py", "line": 2})
        assert "error" not in result, result
        span = result["entity"]["span"]
        expected = evidence.blob("code.py", scope["task"]["head"])[
            span["start_byte"]:span["end_byte"]].decode("utf8")
        assert result["text"] == expected and "return 20 / value" in expected
        assert result["entity"]["names"] != ["<module>"]
        assert result["coverage"]["complete_entity"] is True
        assert result["provenance"]["source"] == "indexed_verified_against_git"
        assert len(json.dumps(result, ensure_ascii=True)) <= 8000
        assert evidence.execute("entity_context", {"path": "code.py", "line": 0})["error"]
        changed = evidence.execute("change_context", {})
        assert "divide" in changed["text"] and "code.py:2" in changed["text"]
        assert "verified indexed source previews" in changed["text"]
        assert "return 10 / value" in changed["text"] and "return 20 / value" in changed["text"]
        assert changed["semantic_endpoint_diff"] is False
        delta = evidence.execute("entity_diff", {"token": "divide", "path": "code.py"})
        assert "error" not in delta, delta
        assert delta["eid"].startswith("oeid1_")
        assert "return 10 / value" in delta["text"] and "return 20 / value" in delta["text"]
        evidence._change_context.clear()
        from gita.eval.reviewbench.common import IntegrityError
        def corrupt(*args, **kwargs):
            raise IntegrityError("Wrong source")
        monkeypatch.setattr(evidence, "entity_diff", corrupt)
        assert evidence.execute("change_context", {})["error"]["code"] == "integrity"
    finally:
        evidence.close()


def test_entity_context_stays_out_of_baseline_and_does_not_build(fixture_repo):
    _, scope, _ = fixture_repo
    assert "entity_context" not in {t["name"] for t in inventory("B")}
    assert "entity_context" in {t["name"] for t in inventory("H")}
    evidence = Evidence(config(scope))
    try:
        result = evidence.execute("entity_context", {"path": "code.py", "line": 2})
        assert result["error"] and "unavailable" in result["error"]["message"]
        assert not scope["indexes"]
    finally:
        evidence.close()


def test_named_source_frontend_resolves_without_line_search_and_reuses_verified_lines(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        result = e.execute("gita_review", {"operation": "source", "token": "divide"})
        assert "error" not in result, result
        assert e.calls == 1 and result["coverage"]["complete_entity"]
        assert result["coverage"]["lookup"] == "unique_name_locator_or_eid"
        assert result["entity"]["path"] == "code.py"
        assert "2:     return 20 / value" in result["text"]
        assert "SECRET_FUTURE_LABEL" not in result["text"]
        reused = e.execute("read_file", {"path": "code.py", "start_line": 1, "end_line": 2})
        assert reused["coverage"]["reused_source_lines"] == 2
        replay = e.execute("gita_review", {
            "operation": "source", "token": "code.py::divide", "force_full": True})
        assert "return 20 / value" in replay["text"]
        base = e.execute("gita_review", {
            "operation": "source", "token": "divide", "path": "code.py",
            "commit": scope["effective_base"]})
        assert "return 10 / value" in base["text"] and "previously_delivered" not in base
        assert e.execute("gita_review", {
            "operation": "source", "token": "divide", "line": 2})["error"]
        missing = e.execute("gita_review", {"operation": "source", "token": "missing_entity"})
        assert missing["error"]["code"] == "not_found"
        assert "gita_id" not in missing["error"]["message"]
        assert len(json.dumps(result, ensure_ascii=True)) <= 8000
    finally:
        e.close()


def test_named_source_frontend_verifies_public_adapter_bytes(fixture_repo, monkeypatch):
    from gita.mcp import tools
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    original = tools.source_tool

    def corrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result["error"] is None
        result["results"][0]["content"] = "not committed source"
        return result

    monkeypatch.setattr(tools, "source_tool", corrupted)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        result = e.execute("gita_review", {"operation": "source", "token": "divide"})
        assert result["error"]["code"] == "integrity"
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["duplicate"], indirect=True)
def test_named_source_ambiguity_returns_actionable_eids_without_selecting_source(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        result = e.execute("gita_review", {
            "operation": "source", "token": "divide", "path": "code.py"})
        assert result["status"] == "ambiguous" and len(result["candidates"]) == 2
        assert not result["coverage"]["complete_entity"] and "source_receipt" not in result
        assert "return" not in result["text"] and "gita_id" not in result["text"]
        eid = result["candidates"][0]["eid"]
        selected = e.execute("gita_review", {"operation": "source", "token": eid})
        assert "error" not in selected, selected
        assert selected["coverage"]["complete_entity"]
        assert "return 20 / value" in selected["text"] or "return 40 / other" in selected["text"]
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["large"], indirect=True)
def test_large_pages_deliver_complete_named_source_with_verified_receipt(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True,
                  "large_evidence_pages": True})
    try:
        result = e.execute("gita_review", {"operation": "source", "token": "divide"})
        assert "error" not in result, result
        assert result["coverage"]["complete_entity"] and not result["truncated"]
        assert result["next_cursor"] is None and result["source_receipt"]
        assert 8000 < len(json.dumps(result, ensure_ascii=True)) <= 16000
        assert len(json.dumps(json.dumps(result, ensure_ascii=True))) <= MAX_TOOL_WIRE_CHARS
        assert "return 20 / value" in result["text"]
    finally:
        e.close()


@pytest.mark.parametrize("large", [False, True])
def test_page_budget_bounds_embedded_wire_escaping_without_losing_bytes(fixture_repo, large):
    _, scope, _ = fixture_repo
    e = Evidence({**config(scope), "large_evidence_pages": large})
    try:
        text = ('\\\\"' + chr(0x1F600)) * 6000
        pages, cursor = [], ""
        while True:
            result = e.page(text, "wire_probe", {}, cursor)
            assert e.fits_response(result)
            assert len(json.dumps(json.dumps(result, ensure_ascii=True))) <= MAX_TOOL_WIRE_CHARS
            pages.append(result["text"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        assert "".join(pages) == text
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["escaped"], indirect=True)
def test_large_named_source_separates_local_retrieval_and_wire_page_budgets(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True,
                  "large_evidence_pages": True})
    try:
        first = e.execute("gita_review", {"operation": "source", "token": "divide"})
        assert "error" not in first, first
        assert first["truncated"] and first["next_cursor"]
        pieces = [first["text"]]
        cursor = first["next_cursor"]
        while cursor:
            result = e.execute("continue_evidence", {"cursor": cursor})
            assert "error" not in result and e.fits_response(result), result
            pieces.append(result["text"])
            cursor = result["next_cursor"]
        assert "return 20 / value" in "".join(pieces)
        lines = "".join(pieces).split("\n")
        assert "\n".join(line.partition(": ")[2] for line in lines) + "\n" == e.blob(
            "code.py", e.head).decode("utf8")
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["large"], indirect=True)
def test_named_source_pages_complete_body_with_bound_continuation(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        first = e.execute("gita_review", {"operation": "source", "token": "divide"})
        assert "error" not in first and first["truncated"], first
        pieces = [first["text"]]
        cursor = first["next_cursor"]
        while cursor:
            result = e.execute("continue_evidence", {"cursor": cursor})
            assert "error" not in result and "source_receipt" not in result, result
            assert len(json.dumps(result, ensure_ascii=True)) <= 8000
            pieces.append(result["text"])
            cursor = result["next_cursor"]
        displayed = "".join(pieces).split("\n")
        assert [line.partition(": ")[0] for line in displayed] == [
            str(i) for i in range(1, len(displayed) + 1)]
        reconstructed = "\n".join(line.partition(": ")[2] for line in displayed).encode("utf8") + b"\n"
        assert reconstructed == e.blob("code.py", scope["task"]["head"])
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["nested"], indirect=True)
def test_named_source_directory_scope_registers_the_actual_member_path(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        result = e.execute("gita_review", {"operation": "source", "token": "divide", "path": "src"})
        assert "error" not in result, result
        assert result["entity"]["path"] == "src/code.py" and result["source_receipt"]
        assert "return 20 / value" in result["text"]
        reused = e.execute("read_file", {"path": "src/code.py", "start_line": 1, "end_line": 2})
        assert reused["coverage"]["reused_source_lines"] == 2
    finally:
        e.close()


def test_context_resolves_importer_hint_globally_without_claiming_a_call_binding(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        result = e.execute("gita_review", {
            "operation": "context", "token": "divide", "path": "importer.py"})
        assert "error" not in result, result
        assert result["entity"]["path"] == "code.py"
        assert result["resolution"]["requested_path_hint"] == "importer.py"
        assert result["resolution"]["scope"] == "global_published_name_match"
        assert "not" in result["resolution"]["binding"] or "verify" in result["resolution"]["binding"]
        assert "return 20 / value" in result["text"] and result["source_receipt"]
    finally:
        e.close()


def test_context_raw_fallback_is_exact_explicit_and_reusable(fixture_repo, monkeypatch):
    from gita.index.query import QueryError
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})

    def unavailable(*args, **kwargs):
        raise QueryError("not_found", "No published entity")

    monkeypatch.setattr(e, "named_source", unavailable)
    try:
        result = e.execute("gita_review", {
            "operation": "context", "token": "value", "path": "code.py"})
        assert "error" not in result, result
        assert result["provenance"]["source"] == "raw_literal_context_after_index_miss"
        assert result["coverage"]["indexed_entity"] is False
        assert result["resolution"]["kind"] == "literal_references_not_definitions"
        assert "entity" not in result and "return 20 / value" in result["text"]
        reused = e.execute("read_file", {"path": "code.py", "start_line": 1, "end_line": 2})
        assert reused["coverage"]["reused_source_lines"] == 2
        assert e.execute("gita_review", {
            "operation": "context", "token": "value", "path": "../outside"})["error"]
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["empty"], indirect=True)
def test_empty_committed_file_is_valid_evidence_without_a_fake_line_receipt(fixture_repo):
    _, scope, _ = fixture_repo
    for arm in ("B", "H"):
        e = Evidence({**config(scope, arm), "integrated_source": arm == "H"})
        try:
            result = e.execute("read_file", {"path": "empty.py", "start_line": 1, "end_line": 80})
            assert "error" not in result, result
            assert result["text"] == "" and result["file_lines"] == 0
            assert result["coverage"]["empty_source"] is True
            assert "source_receipt" not in result
            assert result["blob"] == e.members(e.head)["empty.py"][1]
        finally:
            e.close()


def test_fabricated_empty_response_for_nonempty_git_source_still_stops_integrity(fixture_repo, monkeypatch):
    _, scope, _ = fixture_repo
    e = Evidence({**config(scope), "integrated_source": True})
    monkeypatch.setattr(e, "read_file", lambda **kwargs: {
        "text": "", "file_lines": 0, "coverage": {"empty_source": True},
        "truncated": False, "next_cursor": None, "omitted": {"characters": 0}})
    try:
        result = e.execute("read_file", {"path": "code.py"})
        assert result["error"]["code"] == "integrity"
    finally:
        e.close()


@pytest.mark.parametrize("fixture_repo", ["default", "large"], indirect=True)
def test_frontend_history_preserves_events_and_supplies_verified_code_patches(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "indexed_workflow": True,
                  "integrated_source": True, "engine_frontend": True})
    try:
        result = e.execute("gita_review", {"operation": "history", "token": "divide"})
        assert "error" not in result, result
        assert result["history_view"] == "verified_entity_code_changes"
        events = list(result["results"])
        while result["next_cursor"]:
            result = e.execute("continue_evidence", {"cursor": result["next_cursor"]})
            assert "error" not in result, result
            events.extend(result["results"])
        if len(e.blob("code.py", e.head)) <= 6000:
            assert any("return 20 / value" in row.get("patch", "") for row in events)
        assert all("patch_status" in row for row in events)
        assert all("path_bytes_b64" not in (row["destination"] or {}) for row in events)
        native = e._index(e.head).history(events[0]["eid"], at=e.head, limit=1000)
        assert [row["id"] for row in events] == [row["id"] for row in native["results"]]
        assert [row["decision"] for row in events] == [row["decision"] for row in native["results"]]
        assert "SECRET_FUTURE_LABEL" not in json.dumps(events)
        if len(e.blob("code.py", e.head)) > 6000:
            assert any(row["patch_status"] == "omitted:source_patch_budget" for row in events)
    finally:
        e.close()


def test_cursor_handles_are_short_session_owned_and_query_bound(fixture_repo):
    _, scope, _ = fixture_repo
    a, b = Evidence(config(scope)), Evidence(config(scope))
    try:
        page = a.page("x" * 25000, "query", {"path": "code.py"})
        cursor = page["next_cursor"]
        assert len(cursor) == 28
        assert a.page("x" * 25000, "query", {"path": "code.py"}, cursor)["text"]
        for broker, arguments in ((b, {"path": "code.py"}), (a, {"path": "other.py"})):
            with pytest.raises(ProtocolError):
                broker.page("x" * 25000, "query", arguments, cursor)
        with pytest.raises(ProtocolError):
            a.page("x" * 25000, "query", {"path": "code.py"}, cursor + "x")
    finally:
        a.close()
        b.close()


def test_search_path_alias_preserves_filter_and_cursor_binding(fixture_repo):
    _, scope, _ = fixture_repo
    for arm in ("B", "H"):
        e = Evidence(config(scope, arm))
        try:
            a = e.execute("search", {"text": "return", "path": "code.py"})
            b = e.execute("search", {"text": "return", "pattern": "code.py"})
            assert a == b and "20 / value" in a["text"]
            assert e.execute("search", {"text": "return", "path": "absent.py"})["text"] == ""
            assert e.execute("search", {
                "text": "return", "pattern": "code.py", "path": "other.py"})["error"]
            assert e.execute("search", {"text": "return", "path": ""})["error"]
        finally:
            e.close()


def test_unbuilt_workflow_exposes_readiness_not_broken_index_calls(fixture_repo):
    from gita.eval.reviewbench.evidence import COMMON_TOOLS, tool_inventory
    _, scope, _ = fixture_repo
    cfg = {**config(scope), "indexed_workflow": True}
    tools = {t["name"] for t in tool_inventory(cfg)}
    assert tools == set(COMMON_TOOLS) | {"index_status"}
    e = Evidence(cfg)
    try:
        assert e.execute("entity_context", {"path": "code.py", "line": 1})["error"]
        assert "20 / value" in e.execute("read_file", {"path": "code.py"})["text"]
    finally:
        e.close()


def test_integrated_source_reuses_only_completed_exact_lines_and_can_replay(fixture_repo):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    e = Evidence({**config(indexed), "integrated_source": True})
    try:
        first = e.execute("entity_context", {"path": "code.py", "line": 2})
        assert first["coverage"]["exact_git_lines"] is True
        assert "1: def divide(value):" in first["text"]
        assert "2:     return 20 / value" in first["text"]
        reused = e.execute("read_file", {"path": "code.py", "start_line": 1, "end_line": 2})
        assert reused["coverage"]["reused_source_lines"] == 2
        assert reused["previously_delivered"] == [{
            "start_line": 1, "end_line": 2, "receipt": first["source_receipt"]}]
        full = e.execute("read_file", {"path": "code.py", "start_line": 1,
                                      "end_line": 2, "force_full": True})
        assert "return 20 / value" in full["text"] and "previously_delivered" not in full
        base = e.execute("read_file", {"path": "code.py", "commit": scope["effective_base"],
                                      "start_line": 1, "end_line": 2})
        assert "return 10 / value" in base["text"] and "previously_delivered" not in base
    finally:
        e.close()


def test_truncated_source_is_not_registered_as_delivered(fixture_repo, monkeypatch):
    _, scope, _ = fixture_repo
    e = Evidence({**config(scope), "integrated_source": True})
    try:
        original = e.page
        monkeypatch.setattr(e, "page", lambda *args, **kwargs:
                            {**original(*args, **kwargs), "truncated": True})
        first = e.execute("read_file", {"path": "code.py", "start_line": 1, "end_line": 2})
        assert first["truncated"] is True and e.source_delivery.retained_lines == 0
        monkeypatch.setattr(e, "page", original)
        second = e.execute("read_file", {"path": "code.py", "start_line": 1, "end_line": 2})
        assert "return 20 / value" in second["text"] and "previously_delivered" not in second
    finally:
        e.close()


def test_continuation_uses_issued_original_query_without_argument_copy(fixture_repo, monkeypatch):
    _, scope, _ = fixture_repo
    e = Evidence(config(scope, "B"))
    other = Evidence(config(scope, "B"))
    try:
        text = "content " * 3000
        monkeypatch.setattr(e, "raw_diff", lambda path="", cursor="":
                            e.page(text, "raw_diff", {"path": path}, cursor))
        first = e.execute("raw_diff", {"path": "code.py"})
        assert 5000 < len(first["text"]) < 6000
        token = first["next_cursor"]
        assert other.execute("continue_evidence", {"cursor": token})["error"]
        pieces = [first["text"]]
        while token:
            page = e.execute("continue_evidence", {"cursor": token})
            assert "error" not in page, page
            pieces.append(page["text"])
            token = page["next_cursor"]
        assert "".join(pieces) == text
        assert e.execute("continue_evidence", {"cursor": "invented"})["error"]
    finally:
        e.close()
        other.close()


def test_engine_frontend_preserves_raw_patch_and_indexed_source_without_fake_usage(fixture_repo):
    from gita.eval.reviewbench.evidence import tool_inventory
    _, scope, _ = fixture_repo
    scope = build_indexes(scope)
    cfg = {**config(scope), "indexed_workflow": True, "integrated_source": True,
           "engine_frontend": True}
    names = {t["name"] for t in tool_inventory(cfg)}
    assert "gita_review" in names and "raw_diff" not in names
    assert not {"entity_context", "entity_diff", "id", "history", "index_status"} & names
    e, b = Evidence(cfg), Evidence(config(scope, "B"))
    try:
        patch = e.execute("gita_review", {"operation": "raw_patch", "path": "code.py"})
        assert patch["text"] == b.raw_diff("code.py")["text"]
        source = e.execute("gita_review", {"operation": "source", "path": "code.py", "line": 2})
        assert "2:     return 20 / value" in source["text"]
        reread = e.execute("read_file", {"path": "code.py", "start_line": 1, "end_line": 2})
        assert reread["coverage"]["reused_source_lines"] == 2
        delta = e.execute("gita_review", {"operation": "diff", "token": "divide", "path": "code.py"})
        assert "return 10 / value" in delta["text"] and "return 20 / value" in delta["text"]
        assert e.execute("gita_review", {"operation": "diff", "token": "divide", "line": 2})["error"]
        assert e.execute("gita_review", {"operation": "imaginary"})["error"]
    finally:
        e.close()
        b.close()


def test_common_inventory_and_evidence_are_identical(fixture_repo):
    _, scope, _ = fixture_repo
    baseline, indexed = Evidence(config(scope, "B")), Evidence(config(scope))
    b = {tool["name"]: tool for tool in inventory("B")}
    h = {tool["name"]: tool for tool in inventory("H")}
    assert all(h[name] == definition for name, definition in b.items())
    for name, arguments in (("raw_diff", {}), ("read_file", {"path": "code.py"}),
                            ("search", {"text": "return"}), ("list_files", {}),
                            ("commit_log", {}), ("file_history", {"path": "code.py"})):
        assert getattr(baseline, name)(**arguments) == getattr(indexed, name)(**arguments)
    assert not any(name in h for name in ("gita_diff", "gita_show", "gita_expand"))


@pytest.mark.parametrize("reference", ["HEAD", "HEAD~1", "refs/heads/main", "../labels.json", ""])
def test_only_exact_scoped_commits(fixture_repo, reference):
    _, scope, future = fixture_repo
    evidence = Evidence(config(scope))
    for commit in (reference, future):
        with pytest.raises(ProtocolError):
            evidence.read_file("code.py", commit)


def test_future_cached_objects_are_inaccessible(fixture_repo):
    repository, scope, future = fixture_repo
    poisoned = deepcopy(scope)
    poisoned["allowed_commits"].append(future)
    poisoned["graph"][future] = [scope["task"]["head"]]
    # Re-hashing poisoned metadata is not a substitute for the Git reachability gate.
    poisoned["scope_digest"] = digest(poisoned)
    with pytest.raises(ProtocolError, match="future"):
        Evidence(config(poisoned))
    result = Evidence(config(scope)).commit_log()
    assert future not in result["text"] and "future label" not in result["text"]


def test_wrong_mergebase_or_consistently_wrong_graph_rejected(fixture_repo):
    _, scope, _ = fixture_repo
    for mutate in ("base", "graph"):
        poisoned = deepcopy(scope)
        if mutate == "base":
            poisoned["effective_base"] = scope["task"]["head"]
        else:
            poisoned["graph"][scope["task"]["head"]] = []
        poisoned["scope_digest"] = digest(poisoned)
        with pytest.raises(ProtocolError):
            Evidence(config(poisoned))


@pytest.mark.parametrize("path", [".git/config", "../labels.json", "./code.py",
                                 "code.py/../code.py", "C:\\labels.json", "/labels.json"])
def test_paths_cannot_escape_git_tree(fixture_repo, path):
    _, scope, _ = fixture_repo
    with pytest.raises(ProtocolError):
        Evidence(config(scope)).read_file(path)


def test_cursor_binds_arguments_revision_and_tamper(fixture_repo):
    _, scope, _ = fixture_repo
    evidence = Evidence(config(scope))
    page = evidence.page("x" * 25000, "test", {"commit": scope["task"]["head"]})
    cursor = page["next_cursor"]
    assert cursor
    for bad_cursor, arguments in ((cursor + "1", {"commit": scope["task"]["head"]}),
                                  (cursor, {"commit": scope["effective_base"]})):
        with pytest.raises(ProtocolError):
            evidence.page("x" * 25000, "test", arguments, bad_cursor)


def test_history_revisions_have_raw_source_parity(fixture_repo):
    _, scope, _ = fixture_repo
    evidence = Evidence(config(scope))
    assert "10 / value" in evidence.read_file("code.py", scope["effective_base"])["text"]
    assert "20 / value" in evidence.read_file("code.py", scope["task"]["head"])["text"]
    assert "20 / value" in evidence.file_history("code.py")["text"]


def test_call_budget_is_an_error_not_empty_success(fixture_repo):
    _, scope, _ = fixture_repo
    evidence = Evidence(config(scope))
    for _ in range(40):
        assert "error" not in evidence.execute("list_files", {})
    assert "error" in evidence.execute("list_files", {})
    assert "findings" not in evidence.execute("raw_diff", {})


def test_index_failure_kept_in_source_eligible_cohort(fixture_repo):
    _, scope, _ = fixture_repo
    evidence = Evidence(config(scope))
    assert "error" in evidence.execute("entities", {"path": "code.py"})
    assert evidence.index_status()["coverage"]["status"] == "unavailable"
    assert "error" not in evidence.execute("raw_diff", {})


def test_head_contract_bounds_and_identity(fixture_repo):
    _, scope, _ = fixture_repo
    evidence = Evidence(config(scope, "B"))
    answer = {"pr": pr_identity(scope["task"]), "agent": "reviewbench-reviewer",
              "findings": [{"file": "code.py", "start_line": 2, "end_line": 2,
                            "message": "Division by zero for value=0", "producer": "reviewbench-reviewer"}]}
    assert len(validate_findings(answer, scope["task"], evidence)) == 1
    for mutation in ("line", "path", "identity", "bool"):
        wrong = deepcopy(answer)
        if mutation == "line":
            wrong["findings"][0]["end_line"] = 3
        elif mutation == "path":
            wrong["findings"][0]["file"] = "labels.json"
        elif mutation == "bool":
            wrong["findings"][0]["start_line"] = True
        else:
            wrong["pr"]["head"] = scope["effective_base"]
        with pytest.raises(ProtocolError):
            validate_findings(wrong, scope["task"], evidence)


def test_real_index_preflight_and_source_ref_tamper(fixture_repo):
    _, scope, future = fixture_repo
    indexed = build_indexes(scope)
    assert indexed["index_preparation"]["status"] == "built", indexed["index_preparation"]
    assert preflight(indexed)["independent_occurrences_verified"] > 0
    evidence = Evidence(config(indexed))
    page = evidence.entities("code.py")
    reference = page["results"][0]
    assert evidence.source(reference)["text"]
    for mutation in ("generation", "commit", "span", "names"):
        wrong = deepcopy(reference)
        if mutation == "generation":
            wrong["generation"] = "wrong"
        elif mutation == "commit":
            wrong["commit"]["hex"] = future
        elif mutation == "span":
            wrong["span"]["end_byte"] += 1
        else:
            wrong["names"] = ["fake"]
        with pytest.raises(ProtocolError):
            evidence.source(wrong)
    evidence.close()


def test_consistent_wrong_index_query_fails_independent_oracle(fixture_repo, monkeypatch):
    _, scope, _ = fixture_repo
    indexed = build_indexes(scope)
    assert indexed["index_preparation"]["status"] == "built", indexed["index_preparation"]
    monkeypatch.setattr(Evidence, "entities", lambda *_, **__: {"results": [], "next_cursor": None})
    with pytest.raises(ProtocolError, match="conformance"):
        preflight(indexed)


def test_corrupt_index_is_not_silent_raw_fallback(fixture_repo, monkeypatch):
    _, scope, _ = fixture_repo
    monkeypatch.setattr(Evidence, "entities", lambda *_, **__: {"results": [], "next_cursor": None})
    indexed = build_indexes(scope)
    assert indexed["index_preparation"]["status"] == "integrity_failed"
    with pytest.raises(ProtocolError, match="blocks"):
        preflight(indexed)


def test_actual_mcp_stdio_inventory_and_broker_call_no_model(fixture_repo, tmp_path):
    import anyio
    import os
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from gita.eval.reviewbench.common import write_json
    _, scope, _ = fixture_repo
    path = tmp_path / "broker.json"
    write_json(path, config(scope, "B"))
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    parameters = StdioServerParameters(command=sys.executable,
                                      args=["-m", "gita.eval.reviewbench.evidence", str(path)],
                                      env=environment)

    async def exercise():
        async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {t["name"] for t in inventory("B")}
            assert {tool.name: getattr(tool, "input_schema", getattr(tool, "inputSchema", None))
                    for tool in listed.tools} == {t["name"]: t["inputSchema"] for t in inventory("B")}
            reply = await session.call_tool("read_file", {"path": "code.py"})
            result = json.loads(reply.content[0].text)
            assert "20 / value" in result["text"]
            assert not getattr(reply, "is_error", getattr(reply, "isError", False))
    anyio.run(exercise)


def test_actual_prepare_uses_normal_unchecked_out_repository_and_queries_index(fixture_repo, tmp_path, monkeypatch):
    import shutil
    from gita.eval.reviewbench import preparation
    source, old_scope, future = fixture_repo
    task = {**old_scope["task"], "mirror": "https://github.com/review-bench/synthetic_repo.git"}
    actual_git = preparation.git
    def provision(repo, *args, **kwargs):
        if args[0] == "fetch":
            shutil.copytree(source / ".git" / "objects", repo / ".git" / "objects", dirs_exist_ok=True)
            return b""
        return actual_git(repo, *args, **kwargs)
    monkeypatch.setattr(preparation, "git", provision)
    scope = preparation.prepare(tmp_path / "preparation", task, index=True,
                                ready_policy=preparation.policy_digest())
    repo = Path(scope["repo"])
    assert actual_git(repo, "rev-parse", "--is-bare-repository").strip() == b"false"
    assert not (repo / "code.py").exists()
    assert actual_git(repo, "rev-parse", "HEAD").decode().strip() == task["head"]
    assert scope["index_preparation"]["status"] == "built", scope["index_preparation"]
    evidence = Evidence(config(scope))
    assert evidence.entities("code.py")["results"]
    with pytest.raises(ProtocolError):
        evidence.read_file("code.py", future)
    evidence.close()
