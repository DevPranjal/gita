import copy
import json
from types import SimpleNamespace

import pytest

from gita.eval.reviewbench import runtime_contracts as contracts
from gita.eval.reviewbench.common import ProtocolError, digest


def qualified():
    return {"node_trace_callback": {
        "probe": contracts.NODE_CALLBACK_PROBE,
        "probe_digest": digest(contracts.NODE_CALLBACK_PROBE),
        "observed": {"node": "v22.19.0", "cases": [
            {"name": "sync_error", "errors": 1, "callbacks": 1},
            {"name": "async_error", "errors": 1, "callbacks": 1},
            {"name": "success", "errors": 0, "callbacks": 1}]}}}


def test_probe_uses_only_authored_builtin_code_and_ignores_node_preloads(monkeypatch):
    def run(args, **kwargs):
        assert args == ["node", "--input-type=commonjs", "-e", contracts.NODE_CALLBACK_PROBE]
        assert "NODE_OPTIONS" not in kwargs["env"] and "NODE_PATH" not in kwargs["env"]
        assert kwargs["check"] and kwargs["timeout"] == 15
        return SimpleNamespace(stdout=json.dumps(qualified()["node_trace_callback"]["observed"]))

    monkeypatch.setenv("NODE_OPTIONS", "--require untrusted.js")
    monkeypatch.setenv("NODE_PATH", "untrusted")
    monkeypatch.setattr(contracts.subprocess, "run", run)
    assert contracts.prepare_contracts() == qualified()


@pytest.mark.parametrize("change", ["result", "boolean", "source", "digest", "version"])
def test_wrong_or_forged_runtime_evidence_fails_closed(change):
    value = qualified()
    node = value["node_trace_callback"]
    if change in ("result", "boolean"):
        node["observed"]["cases"][0]["errors"] = 0 if change == "result" else True
    elif change == "source":
        node["probe"] += "\n"
    elif change == "digest":
        node["probe_digest"] = "bad"
    else:
        node["observed"]["node"] = "unknown"
    with pytest.raises(ProtocolError):
        contracts.validate_contracts(value)


def test_context_supplement_is_source_triggered_and_preserves_original():
    original = {"snapshots": [{"text": "channel.traceCallback(call, 0, {}, null, cb)"}]}
    before = copy.deepcopy(original)
    result = contracts.supplement_context(original, qualified())
    assert original == before
    assert result["snapshots"] == original["snapshots"]
    assert result["verified_runtime_evidence"]["node_trace_callback"] == qualified()["node_trace_callback"]
    unrelated = {"snapshots": [{"text": "def other(): pass"}]}
    assert contracts.supplement_context(unrelated, qualified()) is unrelated
