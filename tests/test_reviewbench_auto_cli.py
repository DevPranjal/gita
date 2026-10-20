"""Native auto configuration and campaign stop signals; no model calls."""

import json
from types import SimpleNamespace
import sys

import pytest

from gita.eval.reviewbench import __main__ as cli
from gita.eval.reviewbench import campaign, runtime


def test_auto_does_not_request_unsupported_reasoning_override(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda name: "copilot.exe")
    args = runtime.command(tmp_path, "JSON only", [], None)
    assert args[args.index("--model") + 1] == "auto"
    assert "--reasoning-effort" not in args
    assert args[args.index("--available-tools") + 1] == "review-no-such-tool"
    assert args[args.index("--max-ai-credits") + 1] == "30"


def test_new_protocol_reserves_headroom_for_failed_initial_canary(tmp_path, monkeypatch):
    scopes = [{"task": {"id": str(i)}} for i in range(25)]
    monkeypatch.setattr(campaign, "verify", lambda root: {
        "digest": "dataset", "pin": "a" * 40,
        "starter_ids": [scope["task"]["id"] for scope in scopes],
    })
    monkeypatch.setattr(campaign, "policy_digest", lambda: "policy")
    monkeypatch.setattr(campaign, "git", lambda *args: b"b" * 40)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout="1.0.97"))
    monkeypatch.setattr("shutil.which", lambda name: "copilot.exe")
    result = campaign.protocol(tmp_path, scopes, {"policy_digest": "policy"})
    assert result["budgets"]["reviewer_credits"] == 400
    assert result["reasoning_effort"] == "auto-provider-default"


@pytest.mark.parametrize("operation,result,expected", [
    ("run", {"stop_reason": "Missing usage; campaign stopped"}, 1),
    ("run", {"stop_reason": None}, 0),
    ("grade", {"pending": [{"task": "x", "reason": "judge failed"}]}, 1),
    ("grade", {"pending": []}, 0),
])
def test_cli_does_not_return_success_for_stopped_execution(
    monkeypatch, capsys, operation, result, expected, tmp_path,
):
    args = ["reviewbench", operation, "--run-root", str(tmp_path / "run")]
    if operation == "run":
        args += ["--task-limit", "1", "--audit-approval", "approved"]
        monkeypatch.setattr(cli, "run", lambda *args, **kwargs: result)
    else:
        args += ["--root", str(tmp_path / "corpus"), "--grader", str(tmp_path / "grader")]
        monkeypatch.setattr(cli, "grade", lambda *args, **kwargs: result)
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == expected
    assert json.loads(capsys.readouterr().out) == result
