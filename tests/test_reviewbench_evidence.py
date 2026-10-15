from copy import deepcopy
import json
from pathlib import Path
import secrets

import pytest

from gita.eval.reviewbench.common import (ProtocolError, digest, git, pr_identity,
                                         task_identity, validate_findings)
from gita.eval.reviewbench.evidence import Evidence, inventory
from gita.eval.reviewbench.preparation import build_indexes, preflight, source_scope


@pytest.fixture
def fixture_repo(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    git(repository, "init")
    git(repository, "config", "user.name", "Synthetic Test")
    git(repository, "config", "user.email", "synthetic@example.invalid")
    source = repository / "code.py"
    source.write_text("def divide(value):\n    return 10 / value\n", encoding="utf8")
    git(repository, "add", "--", "code.py")
    git(repository, "commit", "--no-gpg-sign", "-m", "base")
    base = git(repository, "rev-parse", "HEAD").decode().strip()
    source.write_text("def divide(value):\n    return 20 / value\n", encoding="utf8")
    git(repository, "add", "--", "code.py")
    git(repository, "commit", "--no-gpg-sign", "-m", "review")
    head = git(repository, "rev-parse", "HEAD").decode().strip()
    source.write_text("SECRET_FUTURE_LABEL = 'do not leak'\n", encoding="utf8")
    git(repository, "add", "--", "code.py")
    git(repository, "commit", "--no-gpg-sign", "-m", "future label fix")
    future = git(repository, "rev-parse", "HEAD").decode().strip()
    task = {"repo": "https://github.com/synthetic/repo", "pr_number": 1, "base": base, "head": head}
    task["id"] = task_identity(task)
    scope = source_scope(task, repository)
    return repository, scope, future


def config(scope, arm="H"):
    return {**scope, "arm": arm, "cursor_secret": secrets.token_hex(32)}


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
