"""Auto providers differ in phase metadata; terminal content is not reasoning."""
import pytest

from gita.eval.reviewbench.common import ProtocolError, final_answer


def message(*, phase=None, requests=None):
    data = {"content": '{"findings":[]}', "toolRequests": requests or [],
            "reasoningText": "This field must never become the answer"}
    if phase is not None:
        data["phase"] = phase
    return {"type": "assistant.message", "data": data}


def test_phase_less_terminal_auto_response_is_accepted():
    events = [{"type": "tool.execution_complete"}, message(),
              {"type": "result", "exitCode": 0, "sessionId": "session"}]
    assert final_answer(events) == {"findings": []}


@pytest.mark.parametrize("events", [
    [message()],
    [message(), {"type": "result", "exitCode": 1}],
    [message(phase="commentary"), {"type": "result", "exitCode": 0}],
    [message(), {"type": "tool.execution_start"}, {"type": "result", "exitCode": 0}],
    [message(phase="final_answer"), message(requests=[{"name": "tool"}]),
     {"type": "result", "exitCode": 0}],
])
def test_nonterminal_or_intermediate_content_is_not_salvaged(events):
    with pytest.raises(ProtocolError):
        final_answer(events)
