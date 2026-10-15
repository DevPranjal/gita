"""Follow-up execution fixes preserve evidence limits and complete accounting."""
import json
import sys

import pytest

from gita.eval.reviewbench import campaign, evidence, judge, runtime
from gita.eval.reviewbench.common import IntegrityError, ProtocolError, append_json, digest, read_json


def test_native_auto_prompt_uses_stdin_not_windows_command_line(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "copilot.exe")
    prompt = "x" * 50000
    args = runtime.command(tmp_path, prompt, [], None)
    assert prompt not in args
    assert "-p" not in args
    assert "--deny-tool" not in args
    assert args[args.index("--available-tools") + 1] == "review-no-such-tool"


def test_denied_finalization_attempt_is_accounted_without_new_evidence(tmp_path):
    events, calls = [], []
    for i in range(1, 42):
        result = ({"text": "permitted"} if i <= 40 else
                  {"error": {"code": "evidence_budget", "message": "Finalize without more evidence"},
                   "next_cursor": None, "coverage": {"status": "unavailable"},
                   "omitted": {"unknown": True}})
        events.append({"type": "tool.execution_start",
                       "data": {"toolName": "review-raw_diff", "arguments": {}, "toolCallId": str(i)}})
        events.append({"type": "tool.execution_complete", "data": {"toolCallId": str(i)}})
        calls.append({"sequence": i, "request_id": str(i), "tool": "raw_diff", "arguments": {},
                      "argument_digest": digest({}), "result": result,
                      "result_digest": digest(result), "ok": i <= 40})
    path = tmp_path / "tools.jsonl"
    for call in calls:
        append_json(path, call)
    checked = campaign.audit_calls(events, path)
    assert checked["evidence_calls"] == 41
    assert checked["denied_over_budget"] == 1
    assert checked["executed_call_attempts"] == 40

    bad = calls[-1]
    bad["result"] = {"text": "Forbidden extra evidence"}
    bad["result_digest"] = digest(bad["result"])
    bad["ok"] = True
    path.write_text("\n".join(json.dumps(call) for call in calls) + "\n")
    with pytest.raises(IntegrityError):
        campaign.audit_calls(events, path)


def test_judge_follows_raw_diff_continuation_without_truncating_context(monkeypatch):
    class Fake:
        def __init__(self, config):
            pass

        def paths(self, commit):
            return []

        def raw_diff(self, cursor=""):
            return ({"text": "first", "truncated": True, "next_cursor": "next"} if not cursor else
                    {"text": "second", "truncated": False, "next_cursor": None})

        def close(self):
            pass

    monkeypatch.setattr(judge, "Evidence", Fake)
    result = judge.source_context({"task": {"head": "a" * 40}, "effective_base": "b" * 40}, [], [])
    assert result["patch"] == "firstsecond"


def test_index_tool_guidance_does_not_mandate_invocation():
    text = evidence.DESCRIPTIONS["index_status"].lower()
    assert "optional" in text and "source" in text and "entities" in text


def test_invoke_delivers_exact_utf8_prompt_over_stdin(tmp_path, monkeypatch):
    captured = tmp_path / "captured.bin"
    args = [sys.executable, "-c",
            f"import sys; from pathlib import Path; Path({str(captured)!r}).write_bytes(sys.stdin.buffer.read())"]
    monkeypatch.setattr(runtime, "command", lambda *args_, **kwargs: args)
    directory = tmp_path / "attempt"
    directory.mkdir()
    prompt = "x" * 50000 + "\n" + chr(0x03BB) + "\r\n"
    result = runtime.invoke(directory, prompt, [], None, timeout=10)
    assert captured.read_bytes() == prompt.encode("utf8")
    assert read_json(directory / "invocation.json")["prompt_transport"] == "utf8-stdin"
    assert result["usage"] is None


def test_exhausted_broker_does_not_execute_another_query():
    broker = evidence.Evidence.__new__(evidence.Evidence)
    broker.calls, broker.log = 40, None
    result = broker.execute("raw_diff", {})
    assert result == {"error": {
        "code": "evidence_budget",
        "message": "Evidence call limit reached; finalize the review without further evidence"},
        "next_cursor": None, "coverage": {"status": "unavailable"}, "omitted": {"unknown": True}}


def test_judge_refuses_cyclic_raw_diff_cursor(monkeypatch):
    class Fake:
        def __init__(self, config):
            pass

        def paths(self, commit):
            return []

        def raw_diff(self, cursor=""):
            return {"text": "page", "truncated": True, "next_cursor": "same"}

        def close(self):
            pass

    monkeypatch.setattr(judge, "Evidence", Fake)
    with pytest.raises(ProtocolError, match="Cyclic"):
        judge.source_context({"task": {"head": "a" * 40}, "effective_base": "b" * 40}, [], [])
