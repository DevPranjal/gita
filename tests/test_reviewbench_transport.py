"""Only explicitly recorded, non-authoritative redacted diagnostics may be quarantined."""
import json
from pathlib import Path

import pytest

from gita.eval.reviewbench.common import IntegrityError, ProtocolError
from gita.eval.reviewbench.runtime import read_events, inventories


@pytest.mark.parametrize("kind", ["user.message", "assistant.reasoning_delta"])
def test_redacted_diagnostic_is_explicitly_recorded_not_repaired(tmp_path, kind):
    bad = '{"type":"' + kind + '","data":{"content":"******"+token"}}'
    good = {"type": "result", "data": {"exitCode": 0}}
    path = tmp_path / "events.jsonl"
    path.write_text(bad + "\n" + json.dumps(good) + "\n", encoding="utf8")
    with pytest.raises(ProtocolError, match="Malformed"):
        read_events(path)
    rows = read_events(path, diagnostic_transport=True)
    assert rows[0]["type"] == "transport.non_authoritative_frame"
    assert rows[0]["data"]["kind"] == kind
    assert len(rows[0]["data"]["sha256"]) == 64
    assert "content" not in rows[0]["data"]
    assert rows[1] == good
    assert path.read_text(encoding="utf8").startswith(bad)


@pytest.mark.parametrize("kind", [
    "assistant.message", "result", "tool.execution_start", "tool.execution_complete",
    "session.tools_updated", "model.call_finished",
])
def test_malformed_authoritative_events_never_bypass_validation(tmp_path, kind):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"' + kind + '","data":{"content":"******"+token"}}\n')
    with pytest.raises(ProtocolError, match="Malformed"):
        read_events(path, diagnostic_transport=True)


def test_other_corruption_and_interleaved_records_are_not_quarantined(tmp_path):
    path = tmp_path / "events.jsonl"
    for bad in (
        '{"type":"user.message","data":{"content":broken}}\n',
        '{"type":"user.message","data":{"content":"******"+token{"type":"result"}}\n',
    ):
        path.write_text(bad)
        with pytest.raises(ProtocolError, match="Malformed"):
            read_events(path, diagnostic_transport=True)


def test_partial_reads_wait_for_physical_frame_completion(tmp_path):
    path = tmp_path / "events.jsonl"
    good = {"type": "assistant.message", "data": {"content": "a" + chr(0x2028) + "b"}}
    path.write_text(json.dumps(good, ensure_ascii=False) + '\n{"type":"res', encoding="utf8")
    assert read_events(path, partial=True) == [good]
    with pytest.raises(ProtocolError):
        read_events(path)
    path.write_text('{"type":"result","data":broken}\n')
    with pytest.raises(ProtocolError):
        read_events(path, partial=True)


def test_reviewer_input_echo_mode_keeps_tools_finals_and_reasoning_strict(tmp_path):
    path = tmp_path / "events.jsonl"
    bad_input = '{"type":"user.message","data":{"content":"******"+token"}}\n'
    forbidden = {"type": "tool.execution_start", "data": {"toolName": "shell"}}
    path.write_text(bad_input + json.dumps(forbidden) + "\n")
    events = read_events(path, input_diagnostics=True)
    assert events[0]["type"] == "transport.non_authoritative_frame"
    with pytest.raises(IntegrityError, match="Forbidden tool"):
        inventories(events, set(), final=False)
    for kind in ("assistant.message", "assistant.reasoning_delta", "result", "tool.execution_start"):
        path.write_text('{"type":"' + kind + '","data":{"content":"******"+token"}}\n')
        with pytest.raises(ProtocolError, match="Malformed"):
            read_events(path, input_diagnostics=True)
