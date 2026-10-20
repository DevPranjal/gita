"""Declare small workflow experiments without weakening fixed25 confirmation."""
from types import SimpleNamespace
import json

import pytest

from gita.eval.reviewbench import campaign
from gita.eval.reviewbench.common import ProtocolError, digest, append_json
from gita.eval.reviewbench.common import read_json, write_json


@pytest.fixture
def frozen_inputs(monkeypatch):
    monkeypatch.setattr(campaign, "verify", lambda root: {
        "digest": "data", "pin": "a" * 40, "starter_ids": [str(i) for i in range(25)]})
    monkeypatch.setattr(campaign, "policy_digest", lambda: "policy")
    monkeypatch.setattr(campaign, "git", lambda *args: b"b" * 40)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout="1.0.97"))
    monkeypatch.setattr("shutil.which", lambda name: "copilot.exe")
    return [{"task": {"id": "0"}}, {"task": {"id": "6"}}], {"policy_digest": "policy"}


def test_pilot_has_explicit_subset_workflow_and_smaller_caps(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    p = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True,
                          indexed_workflow=True, reviewer_credits=80, judge_credits=60)
    assert len(p["schedule"]) == 4
    assert p["development_pilot"] and p["targeted_judging"]
    assert p["budgets"]["reviewer_credits"] == 80
    assert p["budgets"]["judge_credits"] == 60
    assert p["workflow_guidance"]["H"] != p["workflow_guidance"]["B"]
    assert "irrelevant calls" in p["workflow_guidance"]["H"]


def test_small_subset_cannot_masquerade_as_fixed25(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    with pytest.raises(ProtocolError, match="25"):
        campaign.protocol(tmp_path, scopes, parsers)
    for ids in (["0", "0"], ["outside"], [str(i) for i in range(4)]):
        with pytest.raises(ProtocolError, match="Pilot"):
            campaign.protocol(tmp_path, [{"task": {"id": i}} for i in ids],
                              parsers, development_pilot=True)


def test_default_prompts_remain_identical_and_workflow_is_explicit():
    task = {"repo": "https://github.com/example/repo", "pr_number": 1,
            "base": "a" * 40, "head": "b" * 40}
    assert campaign.review_prompt(task, arm="B") == campaign.review_prompt(task, arm="H")
    b = campaign.review_prompt(task, arm="B", indexed_workflow=True)
    h = campaign.review_prompt(task, arm="H", indexed_workflow=True)
    assert "entity_context(path,line)" in h and "entity_context(path,line)" not in b
    assert "entity_diff(token,path)" in h and "start with change_context" in h
    assert "All evidence tools are optional" not in h


def test_review_profile_matches_capabilities_without_hiding_raw_fallback():
    from gita.eval.reviewbench.evidence import COMMON_TOOLS, inventory
    ready = {t["name"] for t in inventory("H", workflow=True, history_ready=True)}
    gaps = {t["name"] for t in inventory("H", workflow=True, history_ready=False)}
    assert set(COMMON_TOOLS) <= ready and set(COMMON_TOOLS) <= gaps
    assert {"entity_diff", "history"} <= ready
    assert not {"entity_diff", "history"} & gaps
    assert not {"entities", "source", "state", "events"} & ready
    assert {"entity_context", "change_context", "id", "index_status"} <= gaps


def test_workflow_metadata_is_bound_separately_from_immutable_source_scope():
    scope = {"repo": "source", "history_ready": True}
    slot = {"task": "task", "arm": "H"}
    frozen = {"indexed_workflow": True, "task_tool_schemas": {},
              "scope_digests": {"task": digest(scope)}}
    config = {**scope, "arm": "H", "cursor_secret": "secret", "tool_log": "calls",
              "indexed_workflow": True}
    campaign.validate_broker_config(config, slot, frozen)
    for changed in ({**config, "indexed_workflow": False},
                    {**config, "arm": "B"}, {**config, "history_ready": False}):
        with pytest.raises(ProtocolError):
            campaign.validate_broker_config(changed, slot, frozen)


def test_integrated_source_setting_is_explicit_and_scope_bound(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    frozen = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True,
                               indexed_workflow=True, integrated_source=True)
    assert frozen["integrated_source"] is True
    assert frozen["digest"] != campaign.protocol(
        tmp_path, scopes, parsers, development_pilot=True, indexed_workflow=True)["digest"]
    with pytest.raises(ProtocolError, match="requires"):
        campaign.protocol(tmp_path, scopes, parsers, development_pilot=True, integrated_source=True)
    scope = {"repo": "source", "history_ready": True}
    config = {**scope, "arm": "H", "cursor_secret": "key", "tool_log": "log",
              "indexed_workflow": True, "integrated_source": True, "engine_frontend": False}
    frozen["scope_digests"] = {"0": digest(scope)}
    campaign.validate_broker_config(config, {"task": "0", "arm": "H"}, frozen)
    with pytest.raises(ProtocolError, match="source-delivery"):
        campaign.validate_broker_config({**config, "integrated_source": False},
                                        {"task": "0", "arm": "H"}, frozen)


def test_frontend_is_explicit_and_cannot_bypass_integrated_profile(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    with pytest.raises(ProtocolError, match="frontend requires"):
        campaign.protocol(tmp_path, scopes, parsers, development_pilot=True, engine_frontend=True)
    f = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True,
                          indexed_workflow=True, integrated_source=True, engine_frontend=True)
    assert f["engine_frontend"] is True
    h = {t["name"] for t in f["task_tool_schemas"]["0"]["H"]}
    # Synthetic fixtures have no usable index; no unavailable engine tool is exposed.
    assert "gita_review" not in h and "raw_diff" in h


def test_raw_patch_through_frontend_is_not_counted_as_index_usage(tmp_path):
    path = tmp_path / "calls.jsonl"
    arguments = {"operation": "raw_patch"}
    result = {"text": "ordinary Git patch"}
    append_json(path, {"sequence": 1, "tool": "gita_review", "ok": True,
                       "arguments": arguments, "argument_digest": digest(arguments),
                       "result": result, "result_digest": digest(result)})
    events = [
        {"type": "tool.execution_start", "data": {
            "toolName": "review-gita_review", "arguments": arguments, "toolCallId": "call"}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "call"}}]
    assert campaign.audit_calls(events, path)["indexed_calls"] == 0


def test_context_raw_fallback_is_not_counted_as_index_usage(tmp_path):
    path = tmp_path / "calls.jsonl"
    arguments = {"operation": "context", "token": "VALUE", "path": "config.py"}
    result = {"text": "1: VALUE = 1", "provenance": {"source": "raw_literal_context_after_index_miss"}}
    append_json(path, {"sequence": 1, "tool": "gita_review", "ok": True,
                       "arguments": arguments, "argument_digest": digest(arguments),
                       "result": result, "result_digest": digest(result)})
    events = [{"type": "tool.execution_start", "data": {
        "toolName": "review-gita_review", "arguments": arguments, "toolCallId": "call"}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "call"}}]
    assert campaign.audit_calls(events, path)["indexed_calls"] == 0


def test_initial_packets_are_frozen_by_task_and_arm(tmp_path, frozen_inputs, monkeypatch):
    from gita.eval.reviewbench import initial_context
    scopes, parsers = frozen_inputs
    monkeypatch.setattr(campaign, "preflight", lambda scope: None)
    monkeypatch.setattr(initial_context, "build_initial_context",
                        lambda scope, **kw: {"task": scope["task"]["id"], **kw})
    root = tmp_path / "run"
    frozen = campaign.seal(tmp_path / "corpus", root, scopes, parsers, seed=1,
                           development_pilot=True, initial_context=True)
    assert frozen["initial_context"] and frozen["reviewer_input_diagnostics"]
    assert set(frozen["initial_context_digests"]) == {"0", "6"}
    assert campaign.verify_protocol(root) == frozen
    target = root / "initial-context" / "0" / "H.json"
    target.write_bytes((target.parent / "B.json").read_bytes())
    with pytest.raises(ProtocolError, match="content changed"):
        campaign.verify_protocol(root)
    target.unlink()
    with pytest.raises(ProtocolError, match="file missing"):
        campaign.verify_protocol(root)


@pytest.fixture
def initial_attempt(tmp_path, monkeypatch, request):
    from gita.eval.reviewbench.initial_context import serialize_context
    task = {"id": "a" * 64, "repo": "https://github.com/example/repo", "pr_number": 1,
            "base": "b" * 40, "head": "c" * 40}
    packet = {"inventory": [{"path": "code.py"}], "current_source": {"blocks": []},
              "raw_patch": {"text": "untrusted source"},
              "related": {"omission": "on_demand"}, "budget": {"max_characters": 100000}}
    slot = {"task": task["id"], "arm": "H", "repeat": 0, "attempt": 0}
    frozen = {"digest": "protocol", "dataset_digest": "dataset",
              "model": "gpt-5.6-sol", "reasoning_effort": "medium",
              "reviewer_model": "gpt-5.6-sol", "reviewer_reasoning": "medium",
              "initial_context": True, "reviewer_input_diagnostics": True,
              "budgets": {"session_native_credits": 60},
              "initial_context_digests": {task["id"]: {"H": digest(packet)}},
              "indexed_workflow": True, "integrated_source": True, "engine_frontend": True}
    mode, checks = getattr(request, "param", ("json", False))
    frozen.update(initial_context_format=mode, proof_before_reporting=checks)
    write_json(tmp_path / "initial-context" / task["id"] / "H.json", packet)
    directory = tmp_path / "attempts" / "one"

    def invoke(path, prompt, names, mcp, **options):
        assert "gita_review(operation='source')" in prompt
        assert "entity_context" not in prompt
        assert options["input_diagnostics"] is True
        assert options["native_credits"] == 60
        assert options["model"] == "gpt-5.6-sol" and options["reasoning"] == "medium"
        presented = campaign.present_initial_evidence(serialize_context(packet), frozen)
        assert prompt.endswith(presented + "\nEND INITIAL EVIDENCE")
        assert (campaign.FINDING_CHECKS in prompt) is checks
        (path / "prompt.txt").write_text(prompt, encoding="utf8", newline="")
        write_json(path / "invocation.json", {
            "prompt_digest": digest(prompt), "input_diagnostics": True,
            "requested_model": options["model"], "requested_reasoning": options["reasoning"],
            "native_credit_cap": options["native_credits"],
            "arguments_without_prompt": ["--model", options["model"], "--reasoning-effort",
                                         options["reasoning"], "--max-ai-credits",
                                         str(options["native_credits"])]})
        (path / "events.jsonl").write_text("", encoding="utf8")
        return {"status": "measurement_invalid", "events": [], "usage": None}

    monkeypatch.setattr(campaign, "invoke", invoke)
    result = campaign.attempt({"task": task, "indexes": [{"commits": [task["head"]]}]},
                              slot, frozen, directory)
    return directory, slot, frozen, result


def test_initial_packet_is_delivered_in_exact_native_prompt(initial_attempt):
    directory, slot, frozen, result = initial_attempt
    assert campaign.validate_initial_delivery(directory, slot, frozen) == result["initial_evidence"]
    assert (directory / "tools.jsonl").read_bytes() == b""


@pytest.mark.parametrize("initial_attempt", [("readable-v1", True)], indirect=True)
def test_readable_native_prompt_and_first_pass_checks_are_attested(initial_attempt):
    directory, slot, frozen, result = initial_attempt
    assert campaign.validate_initial_delivery(directory, slot, frozen) == result["initial_evidence"]
    assert (directory / "initial_rendered.txt").is_file()
    assert (directory / "initial_context.txt").read_text(encoding="utf8").startswith("{")


@pytest.mark.parametrize("artifact", [
    "initial_context.json", "initial_context.txt", "prompt.txt", "invocation.json"])
def test_initial_prompt_attestation_rejects_tampering(initial_attempt, artifact):
    directory, slot, frozen, _ = initial_attempt
    path = directory / artifact
    if artifact == "initial_context.json":
        write_json(path, {"inventory": []})
    elif artifact == "invocation.json":
        write_json(path, {**read_json(path), "input_diagnostics": False})
    else:
        path.write_text("not the sealed evidence", encoding="utf8")
    with pytest.raises(ProtocolError):
        campaign.validate_initial_delivery(directory, slot, frozen)


def test_no_call_review_requires_explicit_preload_and_existing_empty_log(tmp_path):
    log = tmp_path / "tools.jsonl"
    with pytest.raises(ProtocolError):
        campaign.audit_calls([], log, allow_empty=True)
    log.touch()
    with pytest.raises(ProtocolError):
        campaign.audit_calls([], log)
    audit = campaign.audit_calls([], log, allow_empty=True)
    assert audit["evidence_calls"] == audit["indexed_calls"] == 0
    with pytest.raises(ProtocolError):
        campaign.audit_calls([{"type": "tool.execution_start", "data": {"toolName": "review-search"}}],
                             log, allow_empty=True)


@pytest.mark.parametrize("change", ["metadata", "command"])
def test_cached_native_limit_must_match_frozen_budget(initial_attempt, change):
    from gita.eval.reviewbench.common import file_digest
    directory, slot, frozen, _ = initial_attempt
    assert campaign.validate_receipt(directory, slot, frozen)["status"] == "measurement_invalid"
    invocation = read_json(directory / "invocation.json")
    if change == "metadata":
        invocation["native_credit_cap"] = 30
    else:
        invocation["arguments_without_prompt"][-1] = "30"
    write_json(directory / "invocation.json", invocation)
    result = read_json(directory / "result.json")
    result["artifacts"]["invocation.json"] = file_digest(directory / "invocation.json")
    write_json(directory / "result.json", result)
    with pytest.raises(ProtocolError, match="native credit setting"):
        campaign.validate_receipt(directory, slot, frozen)


def test_on_demand_related_projection_preserves_all_other_evidence(tmp_path):
    packet = {"inventory": ["file"], "raw_patch": {"text": "patch"},
              "current_source": {"blocks": [{"text": "complete HEAD function"}]},
              "related": {"sources": [{"text": "not actually resolved dependency"}]}}
    slot = {"task": "task", "arm": "H"}
    frozen = {"initial_related": False,
              "initial_context_digests": {"task": {"H": digest(packet)}}}
    write_json(tmp_path / "initial-context" / "task" / "H.json", packet)
    original, rendered = campaign.initial_packet(tmp_path / "attempts" / "one", slot, frozen)
    visible = json.loads(rendered)
    assert original == packet
    assert visible["related"]["omission"] == "on_demand_only"
    assert visible["related"]["absence_claim"] is False
    assert "not actually resolved dependency" not in rendered
    for key in ("inventory", "raw_patch", "current_source"):
        assert visible[key] == packet[key]
    frozen["initial_related"] = True
    assert json.loads(campaign.initial_packet(tmp_path / "attempts" / "one", slot, frozen)[1]) == packet


def test_qualified_runtime_contract_is_bound_before_judging(tmp_path, frozen_inputs):
    from gita.eval.reviewbench.runtime_contracts import NODE_CALLBACK_PROBE
    scopes, parsers = frozen_inputs
    contracts = {"node_trace_callback": {
        "probe": NODE_CALLBACK_PROBE, "probe_digest": digest(NODE_CALLBACK_PROBE),
        "observed": {"node": "v22.19.0", "cases": [
            {"name": name, "errors": errors, "callbacks": 1}
            for name, errors in (("sync_error", 1), ("async_error", 1), ("success", 0))]}}}
    frozen = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True,
                               judge_runtime_contracts=contracts)
    assert frozen["judge_runtime_contracts"] == contracts
    assert frozen["digest"] != campaign.protocol(
        tmp_path, scopes, parsers, development_pilot=True)["digest"]
    with pytest.raises(ProtocolError, match="Unsupported judge runtime contract"):
        campaign.protocol(tmp_path, scopes, parsers, development_pilot=True, judge_runtime_contracts={})


def test_union_reviewed_source_policy_is_explicit_and_bound(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    original = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True)
    changed = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True, judge_reviewed_source=True)
    assert not original["judge_reviewed_source"] and changed["judge_reviewed_source"]
    assert changed["digest"] != original["digest"]
    assert changed["named_source_adapter_sha256"] and changed["indexed_adapter_sha256"]
    with pytest.raises(ProtocolError, match="reviewed-source"):
        campaign.protocol(tmp_path, scopes, parsers, development_pilot=True, judge_reviewed_source="yes")


def test_readable_evidence_and_finding_checks_are_explicit_protocol_changes(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    old = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True)
    changed = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True,
                                initial_context_format="readable-v1", proof_before_reporting=True)
    assert changed["digest"] != old["digest"]
    assert old["initial_context_format"] == "json" and not old["proof_before_reporting"]
    assert changed["proof_before_reporting"] and changed["initial_context_format"] == "readable-v1"
    assert "recovery/retry" in campaign.FINDING_CHECKS and "fixed safe values" in campaign.FINDING_CHECKS
