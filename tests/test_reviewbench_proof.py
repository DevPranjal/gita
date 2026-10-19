"""Offline proof contracts; no models, network, corpus or reference labels."""
import json
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from gita.eval.reviewbench import proof
from gita.eval.reviewbench.common import IntegrityError, ProtocolError, read_json, write_json


@pytest.fixture
def root():
    # Keep all test artifacts inside this checkout, not system temporary storage.
    path = Path(__file__).resolve().parents[1] / (".proof-test-" + uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def decision(index=0, action="keep", evidence=None):
    return {"finding_index": index, "decision": action, "reason": "Source guard and impact checked",
            "evidence_calls": [1] if evidence is None else evidence}


@pytest.mark.parametrize("action", ["keep", "drop", "uncertain"])
def test_legal_decisions(action):
    expected = decision(action=action, evidence=[] if action == "uncertain" else [1])
    assert proof.validate_decisions({"decisions": [expected]}, [{}], [1]) == [expected]


@pytest.mark.parametrize("rows", [
    [],
    [decision(-1)],
    [decision(1)],
    [decision(True)],
    [decision(), decision()],
    [decision(evidence=[99])],
    [decision(evidence=[True])],
    [decision(evidence=[1, 1])],
    [decision(evidence=[])],
    [decision(action="drop", evidence=[])],
    [decision(action="maybe")],
    [{**decision(), "reason": " "}],
    [{**decision(), "message": "replacement"}],
])
def test_bad_decisions_fail_closed(rows):
    with pytest.raises(ProtocolError):
        proof.validate_decisions({"decisions": rows}, [{}], [1])


def test_missing_indices_and_unknown_answer_fields():
    with pytest.raises(ProtocolError):
        proof.validate_decisions({"decisions": [decision(), decision()]}, [{}, {}], [1])
    with pytest.raises(ProtocolError):
        proof.validate_decisions({"decisions": [], "findings": []}, [], [])
    assert proof.validate_decisions({"decisions": []}, [], []) == []


@pytest.fixture
def primary(root, monkeypatch):
    run = root / "original"
    run.mkdir()
    schedule = [{"task": "task", "arm": arm, "repeat": 0, "attempt": 0} for arm in ("B", "H")]
    frozen = {"digest": "sealed-original", "schedule": schedule}
    write_json(run / "protocol.json", frozen)
    write_json(run / "scopes.json", [])
    write_json(run / "reviewer-ledger.json", {"spent": 14})
    (run / "frozen-harness").mkdir()
    (run / "frozen-harness" / "campaign.py").write_text("archived source")
    originals = [
        {"file": "a.py", "start_line": 1, "end_line": 2, "message": "Exact original message",
         "producer": "original-producer"},
        {"file": "a.py", "start_line": 3, "end_line": 4, "message": "Other original message",
         "producer": "original-producer"},
        {"file": "a.py", "start_line": 5, "end_line": 6, "message": "Uncertain message",
         "producer": "original-producer"},
    ]
    calls = [{"sequence": 1, "tool": "read_file", "arguments": {"path": "a.py"},
              "ok": True, "result": {"text": "complete observable source"}}]
    for slot in schedule:
        directory = run / "attempts" / proof.campaign.slot_id(slot)
        write_json(directory / "findings.json", {
            "pr": {"repo": "example/repo", "pr_number": 1, "base": "base", "head": "head"},
            "agent": "reviewbench-reviewer", "findings": originals})
        write_json(directory / "result.json", {
            "status": "valid", "usage": {"credits": 7}, "findings": originals})
        (directory / "tools.jsonl").write_text(json.dumps(calls[0]) + "\n")
        events = [
            {"type": "assistant.reasoning_delta", "data": {"content": "PRIVATE_REASONING"}},
            {"type": "assistant.message", "data": {"content": "PRIVATE_INTERMEDIATE"}},
            {"type": "tool.execution_complete", "data": {
                "success": True, "model": "private-model", "result": {"content": "native result"}}},
        ]
        (directory / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
    monkeypatch.setattr(proof.campaign, "verify_protocol", lambda path, **kw: read_json(path / "protocol.json"))
    monkeypatch.setattr(proof.campaign, "validate_receipt",
                        lambda path, slot, frozen: read_json(path / "result.json"))
    return run, root / "proof", schedule, originals


def events_for(answer):
    return [
        {"type": "session.usage_checkpoint", "data": {"promptCacheBreakState": [
            {"models": {"route": {"tools": [], "tool_count": 0}}}]}},
        {"type": "assistant.message", "data": {
            "phase": "final_answer", "content": json.dumps(answer)}},
    ]


def fake_invoke(directory, prompt, allowed, mcp, **kwargs):
    assert allowed == [] and mcp is None
    assert kwargs == {"timeout": 900, "model": proof.MODEL, "reasoning": "medium",
                      "streaming": False, "diagnostic_transport": True}
    ledger = read_json(directory.parents[1] / "verification-ledger.json")
    assert ledger["slots"][directory.name]["state"] == "reserved"
    assert (directory / "started.json").is_file() and (directory / "input.json").is_file()
    answer = {"decisions": [decision(0), decision(1, "drop"), decision(2, "uncertain", [])]}
    events = events_for(answer)
    (directory / "events.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")
    return {"status": "runtime_complete", "usage": {
        "credits": 2, "current_model": proof.MODEL, "models": [proof.MODEL]}, "events": events}


def test_input_is_blinded_and_excludes_reasoning(primary):
    run, _, slots, _ = primary
    payload = proof.build_input(run, slots[0])
    text = proof._prompt(payload)
    for forbidden in ("PRIVATE_REASONING", "PRIVATE_INTERMEDIATE", "original-producer",
                      "private-model", '"arm"', '"credits"', "judge", "golden"):
        assert forbidden not in text
    assert payload["tool_results"][0]["result"]["text"] == "complete observable source"
    assert payload["observable_completions"] == [
        {"success": True}]
    assert "native result" not in text
    assert "producer" not in payload["findings"][0]


def test_snapshot_proof_uses_complete_head_files_not_mixed_patch_states(primary, monkeypatch):
    run, _, slots, _ = primary
    write_json(run / "scopes.json", [{"task": {"id": "task"}}])
    class Snapshot:
        head = "exact-head"
        def __init__(self, config):
            assert config["arm"] == "B" and "tool_log" not in config
        def blob(self, path, commit):
            assert path == "a.py" and commit == self.head
            return b"def update(existing):\n    if not existing:\n        return 'write'\n    return 'skip'\n"
        def members(self, commit):
            return {"a.py": ("100644", "blob")}
        def close(self):
            pass
    monkeypatch.setattr(proof, "Evidence", Snapshot)
    payload = proof.build_input(run, slots[0], snapshot_sources=True)
    assert len(payload["tool_results"]) == 1
    source = payload["tool_results"][0]
    assert source["tool"] == "exact_head_snapshot"
    assert source["result"]["complete_file"] is True
    assert "2:     if not existing:" in source["result"]["text"]
    assert "4:     return 'skip'" in source["result"]["text"]
    assert source["result"]["commit"] == "exact-head"
    assert "observable_completions" not in payload
    assert "complete observable source" not in proof._prompt(payload)
    assert "Other files" in payload["evidence_scope"]


def test_both_reviews_single_call_unchanged_rows_and_no_retry(primary, monkeypatch):
    run, output, slots, originals = primary
    before = proof._original_hashes(run)
    invoked = []

    def invoke(*args, **kwargs):
        invoked.append(args[0])
        return fake_invoke(*args, **kwargs)

    monkeypatch.setattr(proof.runtime, "invoke", invoke)
    summary = proof.run_proof(run, output, ["task"], cap=90)
    assert len(invoked) == 2 and summary["ledger"]["spent"] == 4
    for slot in slots:
        directory = output / "attempts" / proof.campaign.slot_id(slot)
        assert read_json(directory / "findings.json")["findings"] == [originals[0]]
        receipt = read_json(directory / "result.json")
        assert receipt["uncertain"] == 1 and "events" not in receipt
        assert receipt["artifacts"]["events.jsonl"]
    assert proof._original_hashes(run) == before
    assert read_json(output / "original-integrity.json")["unchanged"]
    proof.run_proof(run, output, ["task"], cap=90)
    assert len(invoked) == 2


def test_invalid_final_still_settles_measured_usage(primary, monkeypatch):
    run, output, _, _ = primary

    def invalid(*args, **kwargs):
        result = fake_invoke(*args, **kwargs)
        result["events"] = events_for({"decisions": []})
        return result

    monkeypatch.setattr(proof.runtime, "invoke", invalid)
    summary = proof.run_proof(run, output, cap=90)
    assert summary["ledger"]["spent"] == 4
    assert all(row["status"] == "invalid" for row in summary["results"].values())
    assert not list((output / "attempts").glob("*\\findings.json"))


@pytest.mark.parametrize("failure", ["unknown", "integrity", "wrong-model", "tools", "overrun"])
def test_failures_block_next_session_and_are_sticky(primary, monkeypatch, failure):
    run, output, _, _ = primary
    calls = []

    def fail(*args, **kwargs):
        calls.append(args[0])
        result = fake_invoke(*args, **kwargs)
        if failure == "unknown":
            result.update(status="measurement_invalid", usage=None)
        elif failure == "integrity":
            result.update(status="integrity_failed", integrity_stop=True)
        elif failure == "wrong-model":
            result["usage"].update(current_model="other", models=["other"])
        elif failure == "overrun":
            result["usage"]["credits"] = 46
        else:
            result["events"].insert(0, {"type": "tool.execution_start",
                                        "data": {"toolName": "shell"}})
        return result

    monkeypatch.setattr(proof.runtime, "invoke", fail)
    summary = proof.run_proof(run, output, cap=90)
    assert len(calls) == 1
    entry = next(iter(summary["ledger"]["slots"].values()))
    assert entry["state"] == ("unknown" if failure == "unknown" else
                              "overrun" if failure == "overrun" else "settled")
    if failure != "unknown":
        assert summary["ledger"]["terminal_stop"]["kind"] == "integrity"
    with pytest.raises(ProtocolError):
        proof.run_proof(run, output, cap=90)
    assert len(calls) == 1


def test_payload_cap_fails_before_invocation_or_reservation(primary, monkeypatch):
    run, output, _, _ = primary
    monkeypatch.setattr(proof, "MAX_INPUT_CHARS", 100)
    monkeypatch.setattr(proof.runtime, "invoke", lambda *a, **kw: pytest.fail("paid call"))
    with pytest.raises(ProtocolError, match="400000"):
        proof.run_proof(run, output, cap=90)
    assert not (output / "verification-ledger.json").exists()


def test_original_mutation_is_detected_and_halts(primary, monkeypatch):
    run, output, _, _ = primary
    calls = []

    def mutate(*args, **kwargs):
        calls.append(args[0])
        result = fake_invoke(*args, **kwargs)
        write_json(run / "reviewer-ledger.json", {"spent": 999})
        return result

    monkeypatch.setattr(proof.runtime, "invoke", mutate)
    with pytest.raises(IntegrityError, match="Original artifacts changed"):
        proof.run_proof(run, output, cap=90)
    assert len(calls) == 1
    assert read_json(output / "verification-ledger.json")["terminal_stop"]


def test_output_cannot_mix_original_roots(primary, monkeypatch, root):
    run, output, _, _ = primary
    monkeypatch.setattr(proof.runtime, "invoke", fake_invoke)
    proof.run_proof(run, output, cap=90)
    other = root / "other"
    shutil.copytree(run, other)
    with pytest.raises(IntegrityError, match="protocol changed"):
        proof.run_proof(other, output, cap=90)


def test_completed_output_tampering_blocks_new_calls(primary, monkeypatch):
    run, output, slots, _ = primary
    monkeypatch.setattr(proof.runtime, "invoke", fake_invoke)
    proof.run_proof(run, output, cap=90)
    path = output / "attempts" / proof.campaign.slot_id(slots[0]) / "decisions.json"
    write_json(path, {"decisions": []})
    with pytest.raises(IntegrityError, match="artifacts changed"):
        proof.run_proof(run, output, cap=90)


def test_reordered_decisions_use_original_indices():
    rows = [decision(2, "uncertain", []), decision(1, "drop"), decision(0)]
    assert [row["finding_index"] for row in proof.validate_decisions(
        {"decisions": rows}, [{}, {}, {}], [1])] == [0, 1, 2]


def test_raised_transport_recovers_measured_usage(primary, monkeypatch):
    run, output, _, _ = primary
    usage = {"credits": 3, "current_model": proof.MODEL, "models": [proof.MODEL]}

    def fail(directory, *args, **kwargs):
        write_json(directory / "usage.json", usage)
        raise OSError("transport interrupted")

    monkeypatch.setattr(proof.runtime, "invoke", fail)
    monkeypatch.setattr(proof.runtime, "parse_usage", lambda raw: raw)
    summary = proof.run_proof(run, output, cap=90)
    assert summary["ledger"]["spent"] == 6
    assert all(row["status"] == "invalid" for row in summary["results"].values())
    assert all(row["usage"] == usage for row in summary["results"].values())


def test_missing_inventory_is_sticky_even_with_measured_usage(primary, monkeypatch):
    run, output, _, _ = primary

    def missing(*args, **kwargs):
        outcome = fake_invoke(*args, **kwargs)
        outcome["events"] = outcome["events"][1:]
        return outcome

    monkeypatch.setattr(proof.runtime, "invoke", missing)
    summary = proof.run_proof(run, output, cap=90)
    assert summary["ledger"]["spent"] == 2
    assert summary["ledger"]["terminal_stop"]
    assert len(summary["results"]) == 1
