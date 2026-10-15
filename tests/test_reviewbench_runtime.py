from pathlib import Path
import subprocess
import sys

import pytest

from gita.eval.reviewbench.common import ProtocolError, digest, read_json, write_json
from gita.eval.reviewbench.runtime import invoke
from gita.eval.reviewbench.campaign import audit_calls, validate_receipt


def test_timeout_kills_owned_process_and_retains_unknown_usage(tmp_path, monkeypatch):
    # This is harness-only Python, never repository source or a model invocation.
    monkeypatch.setattr("gita.eval.reviewbench.runtime.command",
                        lambda *_: [sys.executable, "-c", "import time; time.sleep(60)"])
    directory = tmp_path / "attempt"
    directory.mkdir()
    result = invoke(directory, "synthetic", [], None, timeout=0.5)
    assert result["status"] == "measurement_invalid"
    assert result["usage"] is None
    assert "wall-time" in result["error"]
    assert result["wall_seconds"] < 15
    assert (directory / "events.jsonl").exists()
    assert (directory / "stderr.txt").exists()
    assert not (directory / "findings.json").exists()


def test_mcp_schema_and_tool_completions_correlate(tmp_path):
    arguments = {"path": "code.py"}
    result = {"text": "exact source"}
    path = tmp_path / "tools.jsonl"
    import json
    path.write_text(json.dumps({"tool": "read_file", "ok": True,
                               "argument_digest": digest(arguments),
                               "result_digest": digest(result), "result": result}) + "\n", encoding="utf8")
    start = {"type": "tool.execution_start", "data": {
        "toolCallId": "call-1", "toolName": "review-read_file", "arguments": arguments}}
    end = {"type": "tool.execution_complete", "data": {"toolCallId": "call-1"}}
    assert audit_calls([start, end], path)["evidence_calls"] == 1
    for events in ([start], [start, {"type": "tool.execution_complete", "data": {"toolCallId": "other"}}]):
        with pytest.raises(ProtocolError):
            audit_calls(events, path)


def test_resume_rejects_swapped_arm_repeated_attempt_or_raw_tamper(tmp_path):
    slot = {"task": "task", "arm": "B", "repeat": 0, "attempt": 0}
    protocol = {"digest": "protocol", "dataset_digest": "dataset"}
    expected = {**slot, "protocol_digest": "protocol", "dataset_digest": "dataset",
                "model": "auto", "task_tuple_digest": "task"}
    from gita.eval.reviewbench.common import file_digest
    raw = tmp_path / "events.jsonl"
    raw.write_text("{}\n", encoding="utf8")
    receipt = {"identity": expected, "ended": "now", "status": "runtime_failed",
               "artifacts": {raw.name: file_digest(raw)}}
    write_json(tmp_path / "result.json", receipt)
    assert validate_receipt(tmp_path, slot, protocol)["status"] == "runtime_failed"
    for changed in ({**slot, "arm": "H"}, {**slot, "repeat": 1}, {**slot, "attempt": 1}):
        with pytest.raises(ProtocolError, match="identity"):
            validate_receipt(tmp_path, changed, protocol)
    write_json(tmp_path / "result.json", {**receipt, "findings": []})
    with pytest.raises(ProtocolError, match="Failed attempt"):
        validate_receipt(tmp_path, slot, protocol)
    write_json(tmp_path / "result.json", {**receipt, "status": "valid", "findings": [], "usage": None})
    with pytest.raises(ProtocolError, match="valid empty"):
        validate_receipt(tmp_path, slot, protocol)
    write_json(tmp_path / "result.json", receipt)
    raw.write_text("tampered\n", encoding="utf8")
    with pytest.raises(ProtocolError, match="tampered"):
        validate_receipt(tmp_path, slot, protocol)


def test_judge_no_mock_lexical_provider_fallback(tmp_path, monkeypatch):
    from gita.eval.reviewbench.judge import official
    monkeypatch.setattr("gita.eval.reviewbench.judge.verify", lambda _: {})
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ProtocolError, match="no mock fallback"):
        official(tmp_path, [])
