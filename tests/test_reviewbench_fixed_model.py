"""Offline fixed-reviewer configuration and provenance regressions."""
from types import SimpleNamespace
import time

import pytest

from gita.eval.reviewbench import campaign, runtime
from gita.eval.reviewbench.common import ProtocolError, digest, read_json, write_json


MODEL = "gpt-5.6-sol"


def usage(models=(MODEL,)):
    return {
        "currentModel": models[-1],
        "modelMetrics": {
            model: {"usage": {"inputTokens": 100, "outputTokens": 10,
                              "cacheReadTokens": 20, "cacheWriteTokens": 30},
                    "requests": {"count": 1}, "totalNanoAiu": 100000000}
            for model in models
        },
        "agentMetrics": {"main": {"totalNanoAiu": len(models) * 100000000}},
        "totalNanoAiu": len(models) * 100000000,
        "totalApiDurationMs": 5,
        "tokenDetails": {
            name: {"tokenCount": count * len(models)}
            for name, count in {"input": 50, "output": 10,
                                "cache_read": 20, "cache_write": 30}.items()
        },
    }


@pytest.mark.parametrize("model,reasoning", [
    ("auto", None), (MODEL, None), (MODEL, "medium"),
])
def test_command_preserves_guards_and_transports_settings(tmp_path, monkeypatch, model, reasoning):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "copilot.exe")
    prompt = "secret prompt " + "\u03bb" * 10000
    mcp = tmp_path / "mcp.json"
    args = runtime.command(tmp_path, prompt, ["review-read_file"], mcp,
                           model=model, reasoning=reasoning)
    assert args[args.index("--model") + 1] == model
    assert ("--reasoning-effort" in args) == (reasoning is not None)
    if reasoning:
        assert args[args.index("--reasoning-effort") + 1] == reasoning
    assert prompt not in args and "-p" not in args
    assert args[args.index("--available-tools") + 1] == "review-read_file"
    assert args[args.index("--additional-mcp-config") + 1] == "@" + str(mcp)
    assert args[args.index("--context") + 1] == "default"
    assert args[args.index("--max-ai-credits") + 1] == str(runtime.SESSION_CREDITS)
    assert all(flag in args for flag in (
        "--no-custom-instructions", "--disable-builtin-mcps", "--no-ask-user",
        "--no-auto-update", "--allow-all-tools", "--usage-output-file"))


@pytest.mark.parametrize("model,reasoning", [
    ("", None), ("--auto", None), (None, None), ("gpt model", None),
    (MODEL, "bogus"), (MODEL, True), ("auto", "medium"),
])
def test_invalid_settings_fail_before_execution(model, reasoning):
    with pytest.raises(ProtocolError):
        runtime.validate_settings(model, reasoning)


@pytest.fixture
def protocol_inputs(tmp_path, monkeypatch):
    scopes = [{"task": {"id": str(i)}} for i in range(25)]
    monkeypatch.setattr(campaign, "verify", lambda _: {
        "digest": "dataset", "pin": "a" * 40,
        "starter_ids": [s["task"]["id"] for s in scopes]})
    monkeypatch.setattr(campaign, "policy_digest", lambda: "policy")
    monkeypatch.setattr(campaign, "git", lambda *_: b"b" * 40)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *_, **__: SimpleNamespace(stdout="1.0.97"))
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "copilot.exe")
    monkeypatch.setattr(campaign, "preflight", lambda _: None)
    return tmp_path / "corpus", scopes, {"policy_digest": "policy"}


def test_seal_freezes_fixed_settings_in_digest(tmp_path, protocol_inputs):
    root, scopes, parsers = protocol_inputs
    frozen = campaign.seal(root, tmp_path / "run", scopes, parsers, seed=7,
                           reviewer_model=MODEL, reviewer_reasoning="medium")
    assert frozen == read_json(tmp_path / "run" / "protocol.json")
    assert campaign.reviewer_settings(frozen) == (MODEL, "medium")
    assert frozen["reviewer_model"] == frozen["model"] == MODEL
    assert frozen["reviewer_reasoning"] == frozen["reasoning_effort"] == "medium"
    assert frozen["digest"] == digest({k: v for k, v in frozen.items() if k != "digest"})
    with pytest.raises(ProtocolError, match="protocol mismatch"):
        campaign.seal(root, tmp_path / "run", scopes, parsers, seed=7)


def test_judge_settings_are_frozen_independently(protocol_inputs):
    frozen = campaign.protocol(*protocol_inputs, reviewer_model=MODEL, reviewer_reasoning="medium",
                               judge_model=MODEL, judge_reasoning="medium")
    assert frozen["judge_model"] == MODEL
    assert frozen["judge_reasoning"] == "medium"
    assert frozen["digest"] != campaign.protocol(
        *protocol_inputs, reviewer_model=MODEL, reviewer_reasoning="medium")["digest"]


def test_fixed_judge_uses_native_login_and_measured_usage(tmp_path, monkeypatch):
    from gita.eval.reviewbench import judge
    observed = {}
    def invoke(directory, prompt, allowed, mcp, **kwargs):
        observed.update(kwargs)
        assert allowed == [] and mcp is None
        return {"status": "runtime_complete", "usage": {"credits": 0.1}, "events": []}
    monkeypatch.setattr(judge, "invoke", invoke)
    monkeypatch.setattr(judge, "final_answer", lambda _: {"qualified": True})
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "protocol", 40)
    answer = judge._judge_call(tmp_path / "judge", "JSON only", ledger, "qualification",
                               time.time() + 60, model=MODEL, reasoning="medium")
    assert answer == {"qualified": True}
    assert observed["model"] == MODEL and observed["reasoning"] == "medium"
    assert observed["streaming"] is False and observed["diagnostic_transport"] is True
    assert ledger.snapshot()["spent"] == 0.1


def test_measured_native_overrun_is_counted_and_permanently_stops_ledger(tmp_path):
    ledger = runtime.CreditLedger(tmp_path / "ledger.json", "protocol", 160)
    ledger.reserve("review")
    with pytest.raises(ProtocolError, match="exceeded"):
        ledger.settle("review", 32.53016)
    state = ledger.snapshot()
    assert state["spent"] == 32.53016
    assert state["slots"]["review"]["state"] == "overrun"
    assert state["slots"]["review"]["credits"] == 32.53016
    assert state["terminal_stop"]["kind"] == "integrity"
    with pytest.raises(ProtocolError, match="terminal"):
        ledger.reserve("next")


def test_defaults_and_legacy_identity_remain_auto(protocol_inputs):
    frozen = campaign.protocol(*protocol_inputs)
    assert campaign.reviewer_settings(frozen) == ("auto", None)
    slot = {"task": "0", "arm": "B", "repeat": 0, "attempt": 0}
    legacy = {"digest": "p", "dataset_digest": "d",
              "model": "auto", "reasoning_effort": "auto-provider-default"}
    assert campaign.attempt_identity(slot, legacy) == {
        **slot, "protocol_digest": "p", "dataset_digest": "d",
        "model": "auto", "task_tuple_digest": "0"}
    assert "routing may differ" in frozen["interpretation"]


@pytest.mark.parametrize("arm", ["B", "H"])
def test_attempt_propagates_frozen_settings_without_prompt_changes(tmp_path, monkeypatch, arm):
    task = {"id": "task", "repo": "https://github.com/example/repo",
            "pr_number": 1, "base": "a" * 40, "head": "b" * 40}
    slot = {"task": "task", "arm": arm, "repeat": 0, "attempt": 0}
    frozen = {"digest": "p", "dataset_digest": "d", "model": MODEL,
              "reviewer_model": MODEL, "reviewer_reasoning": "medium",
              "reasoning_effort": "medium"}
    captured = {}

    def invoke(directory, prompt, names, mcp, **kwargs):
        captured.update(prompt=prompt, names=names, mcp=mcp, **kwargs)
        return {"status": "runtime_failed", "usage": None}

    monkeypatch.setattr(campaign, "invoke", invoke)
    result = campaign.attempt({"task": task}, slot, frozen, tmp_path / arm)
    assert captured["model"] == MODEL and captured["reasoning"] == "medium"
    assert captured["prompt"] == campaign.review_prompt(task)
    assert MODEL not in captured["prompt"] and "medium" not in captured["prompt"]
    assert result["identity"] == campaign.attempt_identity(slot, frozen)
    assert read_json(tmp_path / arm / "started.json")["identity"] == result["identity"]


@pytest.mark.parametrize("models,expected", [
    ((MODEL,), "runtime_complete"),
    (("fallback",), "measurement_invalid"),
    ((MODEL, "fallback"), "measurement_invalid"),
])
def test_invoke_records_settings_and_rejects_fallback(tmp_path, monkeypatch, models, expected):
    directory = tmp_path / "attempt"
    directory.mkdir()
    captured = {}

    class Process:
        pid, returncode = 123, 0

        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["prompt"] = kwargs["stdin"].read()
            write_json(directory / "usage.json", usage(models))

        def poll(self):
            return 0

    monkeypatch.setattr(runtime.shutil, "which", lambda _: "copilot.exe")
    monkeypatch.setattr(runtime.subprocess, "Popen", Process)
    monkeypatch.setattr(runtime, "ProcessTree", lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(runtime, "inventories", lambda *_, **__: [])
    prompt = "exact \u03bb\r\nprompt"
    outcome = runtime.invoke(directory, prompt, [], None, model=MODEL, reasoning="medium")
    assert outcome["status"] == expected
    assert outcome["usage"] == runtime.parse_usage(usage(models))
    assert outcome["requested_model"] == MODEL
    assert outcome["requested_reasoning"] == "medium"
    assert captured["prompt"] == prompt.encode("utf8")
    invocation = read_json(directory / "invocation.json")
    assert invocation["requested_model"] == MODEL
    assert invocation["requested_reasoning"] == "medium"
    assert invocation["prompt_digest"] == digest(prompt)
    assert invocation["arguments_without_prompt"] == captured["args"]
    if expected == "measurement_invalid":
        assert "fallback is forbidden" in outcome["usage_error"]


@pytest.mark.parametrize("mutation", [None, "identity", "model", "reasoning", "arguments"])
def test_fixed_receipt_validates_frozen_requested_identity(tmp_path, mutation):
    slot = {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}
    frozen = {"digest": "p", "dataset_digest": "d", "model": MODEL,
              "reviewer_model": MODEL, "reviewer_reasoning": "medium",
              "reasoning_effort": "medium"}
    invocation = {"requested_model": MODEL, "requested_reasoning": "medium",
                  "arguments_without_prompt": ["copilot", "--model", MODEL,
                                               "--reasoning-effort", "medium"]}
    row = {"identity": campaign.attempt_identity(slot, frozen), "ended": "now",
           "status": "runtime_failed", "findings": None, "usage": None, "artifacts": {}}
    if mutation == "identity":
        row["identity"]["model"] = "auto"
    elif mutation == "model":
        invocation["requested_model"] = "auto"
    elif mutation == "reasoning":
        invocation["requested_reasoning"] = "high"
    elif mutation == "arguments":
        invocation["arguments_without_prompt"][2] = "auto"
    write_json(tmp_path / "result.json", row)
    write_json(tmp_path / "invocation.json", invocation)
    if mutation:
        with pytest.raises(ProtocolError, match="mismatch"):
            campaign.validate_receipt(tmp_path, slot, frozen)
    else:
        assert campaign.validate_receipt(tmp_path, slot, frozen) == row


def test_legacy_auto_receipt_needs_no_new_invocation_metadata(tmp_path):
    slot = {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}
    frozen = {"digest": "p", "dataset_digest": "d"}
    row = {"identity": campaign.attempt_identity(slot, frozen), "ended": "now",
           "status": "runtime_failed", "findings": None, "usage": None, "artifacts": {}}
    write_json(tmp_path / "result.json", row)
    assert campaign.validate_receipt(tmp_path, slot, frozen) == row


def test_conflicting_frozen_settings_rejected():
    with pytest.raises(ProtocolError, match="disagree"):
        campaign.reviewer_settings({"model": MODEL, "reviewer_model": "auto"})


def test_new_auto_receipt_cannot_claim_a_fixed_model_invocation(tmp_path):
    slot = {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}
    frozen = {"digest": "p", "dataset_digest": "d", "model": "auto",
              "reviewer_model": "auto", "reviewer_reasoning": None}
    row = {"identity": campaign.attempt_identity(slot, frozen), "ended": "now",
           "status": "runtime_failed", "findings": None, "usage": None, "artifacts": {}}
    write_json(tmp_path / "result.json", row)
    write_json(tmp_path / "invocation.json", {
        "requested_model": MODEL, "requested_reasoning": None,
        "arguments_without_prompt": ["copilot", "--model", MODEL]})
    with pytest.raises(ProtocolError, match="model/protocol mismatch"):
        campaign.validate_receipt(tmp_path, slot, frozen)


def test_valid_fixed_receipt_cannot_launder_mixed_realized_routes(tmp_path):
    slot = {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}
    frozen = {"digest": "p", "dataset_digest": "d", "model": MODEL,
              "reviewer_model": MODEL, "reviewer_reasoning": "medium",
              "reasoning_effort": "medium"}
    raw = usage((MODEL, "fallback"))
    row = {"identity": campaign.attempt_identity(slot, frozen), "ended": "now",
           "status": "valid", "findings": [], "usage": runtime.parse_usage(raw), "artifacts": {}}
    write_json(tmp_path / "result.json", row)
    write_json(tmp_path / "usage.json", raw)
    write_json(tmp_path / "invocation.json", {
        "requested_model": MODEL, "requested_reasoning": "medium",
        "arguments_without_prompt": ["copilot", "--model", MODEL, "--reasoning-effort", "medium"]})
    with pytest.raises(ProtocolError, match="fallback is forbidden"):
        campaign.validate_receipt(tmp_path, slot, frozen)
