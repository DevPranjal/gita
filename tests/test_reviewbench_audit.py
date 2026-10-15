"""Audit regressions. All model execution is mocked; cleanup uses owned helpers."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import time

import pytest

from gita.eval.reviewbench import campaign, runtime
from gita.eval.reviewbench.common import IntegrityError, digest, pr_identity, read_json, write_json
from gita.eval.reviewbench.evidence import Evidence, inventory


def fault_broker(directory, fault, monkeypatch):
    head = "b" * 40
    broker = object.__new__(Evidence)
    broker.repo = directory
    broker.head, broker.base = head, "a" * 40
    broker.allowed = (broker.base, head)
    broker.arm = "H"
    broker.calls = 0
    broker.tools = {row["name"] for row in inventory("H")}
    broker.log = directory / "tools.jsonl"
    broker.secret = b"synthetic"
    broker.sessions = {}
    broker.issued = {}
    broker.repository = None
    broker.config = {"scope_digest": "synthetic-scope", "history_ready": True,
                     "indexes": [{"commits": [head], "scope": "scope",
                                  "generation": "expected-generation", "context": "expected-context",
                                  "tip": head}]}
    reference = {"id": "occurrence", "generation": "wrong-generation", "blob": {},
                 "commit": {"hex": head}, "path_bytes_b64": "", "span": {}}
    session = SimpleNamespace(
        generation="expected-generation", context=SimpleNamespace(id="wrong-context"),
        tip=SimpleNamespace(digest=bytes.fromhex(head)), close=lambda: None,
        entities_in=lambda *_, **__: {"results": [reference]})
    if fault == "generation":
        broker._index = lambda _: session
    else:
        monkeypatch.setattr("gita.vcs.git.CommittedRepo", lambda _: SimpleNamespace(close=lambda: None))
        monkeypatch.setattr("gita.index.query.open_index", lambda *_, **__: session)
    return broker


@pytest.mark.parametrize("fault", ["generation", "context"])
def test_wrong_index_provenance_cannot_be_valid_empty_review(tmp_path, monkeypatch, fault):
    task = {"id": "synthetic-task", "repo": "https://github.com/synthetic/repo", "pr_number": 1,
            "base": "a" * 40, "head": "b" * 40}
    scope = {"task": task}
    slot = {"task": task["id"], "arm": "H", "repeat": 0, "attempt": 0}
    protocol = {"digest": "synthetic-protocol", "dataset_digest": "synthetic-dataset"}

    def invoke(directory, *_args, **_kwargs):
        broker = fault_broker(directory, fault, monkeypatch)
        result = broker.execute("entities", {"path": "code.py"})
        # The error must be typed at its source, independent of English wording.
        assert result["error"]["code"] == "integrity"
        broker.close()
        return {"status": "runtime_complete", "usage": {"credits": 0.1}, "wall_seconds": 0,
                "events": [
                    {"type": "tool.execution_start", "data": {
                        "toolCallId": "call", "toolName": "review-entities", "arguments": {"path": "code.py"}}},
                    {"type": "tool.execution_complete", "data": {"toolCallId": "call"}},
                    {"type": "assistant.message", "data": {"phase": "final_answer", "content": json.dumps({
                        "pr": pr_identity(task), "agent": "reviewbench-reviewer", "findings": []})}},
                ]}

    monkeypatch.setattr(campaign, "invoke", invoke)
    monkeypatch.setattr(campaign, "Evidence", lambda _: SimpleNamespace(close=lambda: None))
    result = campaign.attempt(scope, slot, protocol, tmp_path / "attempt")
    assert result["status"] == "integrity_failed"
    assert result["integrity_stop"] is True
    assert result["findings"] is None


def test_settled_integrity_stop_is_sticky_across_repeated_run(tmp_path, monkeypatch):
    slots = [{"task": "task", "arm": arm, "repeat": 0, "attempt": 0} for arm in ("H", "B")]
    protocol = {"digest": "protocol", "dataset_digest": "dataset", "schedule": slots}
    write_json(tmp_path / "scopes.json", [{"task": {"id": "task"}}])
    monkeypatch.setattr(campaign, "verify_protocol", lambda _: protocol)
    monkeypatch.setattr(campaign, "validate_receipt", lambda directory, *_: read_json(directory / "result.json"))
    invocations = []

    def attempt(_scope, slot, _protocol, directory, **_):
        invocations.append(slot)
        directory.mkdir(parents=True)
        row = {"status": "output_or_evidence_invalid", "findings": None,
               "integrity_stop": True, "error_code": "integrity",
               "error": "Corrupt indexed evidence; campaign integrity gate", "usage": {"credits": 0.1}}
        write_json(directory / "result.json", row)
        return row

    monkeypatch.setattr(campaign, "attempt", attempt)
    first = campaign.run(tmp_path, task_limit=1, audit_approval="protocol")
    assert len(invocations) == 1
    assert first["stop_reason"]
    # Losing a derived summary must not erase the ledger's terminal stop.
    (tmp_path / "campaign.json").unlink()
    second = campaign.run(tmp_path, task_limit=1, audit_approval="protocol")
    assert len(invocations) == 1
    assert second["stop_reason"] == first["stop_reason"]
    assert second["ledger"]["terminal_stop"]["protocol"] == "protocol"


def test_resume_checks_prior_integrity_receipt_before_any_new_reservation(tmp_path, monkeypatch):
    slots = [{"task": "task", "arm": arm, "repeat": 0, "attempt": 0} for arm in ("H", "B")]
    frozen = {"digest": "protocol", "dataset_digest": "dataset", "schedule": slots}
    write_json(tmp_path / "scopes.json", [{"task": {"id": "task"}}])
    ledger = runtime.CreditLedger(tmp_path / "reviewer-ledger.json", "protocol", runtime.REVIEWER_CREDITS)
    key = campaign.slot_id(slots[0])
    ledger.reserve(key)
    ledger.settle(key, 0.1)
    directory = tmp_path / "attempts" / key
    write_json(directory / "result.json", {
        "status": "integrity_failed", "integrity_stop": True,
        "error_code": "integrity", "error": "Wrong occurrence generation", "usage": {"credits": 0.1}})
    monkeypatch.setattr(campaign, "verify_protocol", lambda _: frozen)
    monkeypatch.setattr(campaign, "validate_receipt", lambda directory, *_: read_json(directory / "result.json"))
    calls = []
    monkeypatch.setattr(campaign, "attempt", lambda *_, **__: calls.append("launched"))
    result = campaign.run(tmp_path, task_limit=1, audit_approval="protocol")
    assert calls == []
    assert result["ledger"]["terminal_stop"]["slot"] == key


def test_stopped_protocol_cannot_be_copied_to_fresh_root_for_same_approval(tmp_path):
    original, copied = tmp_path / "original", tmp_path / "copied"
    body = {"run_root_digest": digest(str(original.resolve()))}
    write_json(copied / "protocol.json", {**body, "digest": digest(body)})
    with pytest.raises(IntegrityError, match="different run root"):
        campaign.verify_protocol(copied)


def test_terminal_reviewer_integrity_stop_blocks_judging(tmp_path, monkeypatch):
    from gita.eval.reviewbench import judge
    protocol = {"digest": "protocol", "dataset_digest": "dataset"}
    run_root = tmp_path / "run"
    ledger = runtime.CreditLedger(run_root / "reviewer-ledger.json", "protocol", runtime.REVIEWER_CREDITS)
    ledger.halt_integrity("failed", "typed integrity failure")
    monkeypatch.setattr(judge, "verify_protocol", lambda _: protocol)
    monkeypatch.setattr(judge, "verify", lambda _: {"digest": "dataset"})
    monkeypatch.setattr(judge, "invoke", lambda *_, **__: pytest.fail("A stopped protocol launched a judge"))
    with pytest.raises(IntegrityError, match="blocks reviewer and judge"):
        judge.grade(tmp_path / "corpus", run_root, tmp_path / "grader")


def test_settled_judge_integrity_stop_also_blocks_reviewers(tmp_path, monkeypatch):
    from gita.eval.reviewbench import judge
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "protocol", 200)
    monkeypatch.setattr(judge, "invoke", lambda *_, **__: {
        "status": "integrity_failed", "integrity_stop": True, "usage": {"credits": 0.1},
        "error": "Forbidden exposed judge tools"})
    with pytest.raises(IntegrityError):
        judge._judge_call(tmp_path / "judge-attempt", "synthetic", ledger, "judge", time.time() + 15)
    assert ledger.snapshot()["slots"]["judge"]["state"] == "settled"
    assert ledger.snapshot()["spent"] == 0.1
    frozen = {"digest": "protocol", "dataset_digest": "dataset", "schedule": [
        {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}]}
    write_json(tmp_path / "scopes.json", [{"task": {"id": "task"}}])
    monkeypatch.setattr(campaign, "verify_protocol", lambda _: frozen)
    monkeypatch.setattr(campaign, "attempt", lambda *_, **__: pytest.fail("Stopped protocol launched reviewer"))
    result = campaign.run(tmp_path, task_limit=1, audit_approval="protocol")
    assert result["ledger"]["terminal_stop"]["slot"] == "judge"


@pytest.mark.parametrize("failed_write", ["halt", "receipt"])
def test_judge_integrity_persistence_failure_keeps_durable_block(tmp_path, monkeypatch, failed_write):
    from gita.eval.reviewbench import judge
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "protocol", 200)
    monkeypatch.setattr(judge, "invoke", lambda *_, **__: {
        "status": "integrity_failed", "integrity_stop": True,
        "usage": {"credits": 0.1}, "error": "Forbidden judge inventory"})
    if failed_write == "halt":
        original = runtime.write_json
        def fail_halt(path, value):
            if path.name == "judge-ledger.json" and value.get("terminal_stop"):
                raise OSError("injected halt write failure")
            return original(path, value)
        monkeypatch.setattr(runtime, "write_json", fail_halt)
    else:
        original = judge.write_json
        def fail_receipt(path, value):
            if path.name == "result.json":
                raise OSError("injected receipt write failure")
            return original(path, value)
        monkeypatch.setattr(judge, "write_json", fail_receipt)
    with pytest.raises(IntegrityError):
        judge._judge_call(tmp_path / "judge-attempt", "synthetic", ledger, "judge", time.time() + 15)
    state = ledger.snapshot()
    assert state.get("terminal_stop") is not None or state["slots"]["judge"]["state"] in ("reserved", "unknown")
    with pytest.raises((IntegrityError, campaign.ProtocolError)):
        judge._judge_call(tmp_path / "next-judge", "synthetic", ledger, "next", time.time() + 15)


def test_startup_reconciles_settled_integrity_judge_receipt_without_marker(tmp_path, monkeypatch):
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "protocol", 200)
    ledger.reserve("prior")
    receipt = tmp_path / "prior-judge-result.json"
    write_json(receipt, {"status": "integrity_failed", "integrity_stop": True,
                         "usage": {"credits": 0.1}, "error": "Wrong judge tools"})
    state = ledger.snapshot()
    state["slots"]["prior"]["receipt_path"] = str(receipt)
    write_json(ledger.path, state)
    ledger.settle("prior", 0.1)
    frozen = {"digest": "protocol", "dataset_digest": "dataset", "schedule": [
        {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}]}
    write_json(tmp_path / "scopes.json", [{"task": {"id": "task"}}])
    monkeypatch.setattr(campaign, "verify_protocol", lambda _: frozen)
    monkeypatch.setattr(campaign, "attempt", lambda *_, **__: pytest.fail("Reconciliation launched another model"))
    result = campaign.run(tmp_path, task_limit=1, audit_approval="protocol")
    assert result["ledger"]["terminal_stop"]["protocol"] == "protocol"


def test_receipt_write_error_reaps_actual_parent_and_descendant(tmp_path, monkeypatch):
    import psutil
    directory = tmp_path / "attempt"
    directory.mkdir()
    child_receipt = directory / "descendant.json"
    script = (
        "import subprocess,sys,time,json; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
        f"Path({str(child_receipt)!r}).write_text(json.dumps({{'pid':child.pid}})); "
        "time.sleep(60)"
    )
    monkeypatch.setattr(runtime, "command", lambda *_: [sys.executable, "-c", script])
    real_write = runtime.write_json
    pids = []
    helpers = []

    def disk_error(path, value):
        if path.name != "process.json":
            return real_write(path, value)
        pids.append(value["pid"])
        deadline = time.monotonic() + 10
        while not child_receipt.exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        assert child_receipt.exists(), "Owned helper did not spawn its descendant"
        pids.append(read_json(child_receipt)["pid"])
        helpers.extend(psutil.Process(pid) for pid in pids)
        raise OSError("injected process receipt disk failure")

    monkeypatch.setattr(runtime, "write_json", disk_error)
    try:
        result = runtime.invoke(directory, "synthetic no-model helper", [], None, timeout=15)
        assert result["status"] == "measurement_invalid"
        assert "receipt disk failure" in result["error"]
        for helper in helpers:
            if helper.is_running():
                helper.wait(timeout=5)
        assert not any(helper.is_running() for helper in helpers)
    finally:
        # A red regression must not leave its intentionally long-lived helper running.
        for helper in helpers:
            if helper.is_running():
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(helper.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    helper.kill()
