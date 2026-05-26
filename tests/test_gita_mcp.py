"""Tests for the MCP server (in-process; no subprocess needed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gita import mcp

from conftest import commit_file


def test_initialize_returns_protocol_version() -> None:
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "gita"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list_exposes_all_tools() -> None:
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "gita_diff", "gita_status", "gita_explain", "gita_symbol_log",
        "gita_callers", "gita_get", "gita_prove", "gita_last_proven",
        "gita_bisect_proven",
    }
    for tool in resp["result"]["tools"]:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_initialized_notification_returns_none() -> None:
    assert mcp.handle_request({
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }) is None


def test_unknown_method_returns_error() -> None:
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
    assert resp["error"]["code"] == -32601


def test_tool_call_diff_returns_manifest_json(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    s2 = commit_file(git_repo, "m.py", "def f():\n    return 2\n", "edit")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {
            "name": "gita_diff",
            "arguments": {"root": str(git_repo), "from_ref": s1, "to_ref": s2},
        },
    })
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["kind"] == "manifest"
    assert payload["from"] == s1
    assert payload["to"] == s2


def test_tool_call_status(git_repo: Path) -> None:
    commit_file(git_repo, "m.py", "x = 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "gita_status", "arguments": {"root": str(git_repo)}},
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["branch"] == "main"
    assert payload["head"]


def test_tool_call_symbol_log(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "init")
    commit_file(git_repo, "u.py", "def foo():\n    return 2\n", "edit")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {
            "name": "gita_symbol_log",
            "arguments": {"root": str(git_repo), "name": "foo"},
        },
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["name"] == "foo"
    assert len(payload["commits"]) == 2


def test_tool_call_callers(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "m.py",
        "def f():\n    return 1\n\ndef g():\n    return f()\n",
        "init",
    )
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {
            "name": "gita_callers",
            "arguments": {"root": str(git_repo), "name": "f"},
        },
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert any(h["caller"] == "g" for h in payload["callers"])


def test_tool_call_explain(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {
            "name": "gita_explain",
            "arguments": {"root": str(git_repo), "ref": sha},
        },
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["commit"] == sha
    assert payload["manifest"]["summary"]["logic_ops"] >= 1


def test_unknown_tool_returns_error(git_repo: Path) -> None:
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "gita_nope", "arguments": {"root": str(git_repo)}},
    })
    assert resp["error"]["code"] == -32601


def test_tool_call_error_propagated(git_repo: Path) -> None:
    """Bad ref → server returns error rather than crashing the loop."""
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {
            "name": "gita_explain",
            "arguments": {"root": str(git_repo), "ref": "deadbeef"},
        },
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32000


def test_ping_returns_empty_result() -> None:
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 11, "method": "ping"})
    assert resp["result"] == {}


# ---------------------------------------------------------------------------
# phase 4 — get / prove / last-proven + diff symbol filter
# ---------------------------------------------------------------------------


def test_mcp_gita_get_returns_symbol(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 20, "method": "tools/call",
        "params": {
            "name": "gita_get",
            "arguments": {"root": str(git_repo), "name": "foo"},
        },
    })
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["name"] == "foo"
    assert payload["kind"] == "function"
    assert "def foo" in payload["body"]


def test_mcp_gita_get_ambiguous_returns_jsonrpc_error(git_repo: Path) -> None:
    commit_file(
        git_repo,
        "u.py",
        "def foo():\n    return 1\n\nclass C:\n    def foo(self):\n        return 2\n",
        "init",
    )
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 21, "method": "tools/call",
        "params": {
            "name": "gita_get",
            "arguments": {"root": str(git_repo), "name": "foo"},
        },
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert resp["error"]["data"]["candidates"]


def test_mcp_gita_get_not_found_returns_jsonrpc_error(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 22, "method": "tools/call",
        "params": {
            "name": "gita_get",
            "arguments": {"root": str(git_repo), "name": "nope"},
        },
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32000


def test_mcp_gita_prove_records_and_returns_result(git_repo: Path) -> None:
    import sys as _sys
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 23, "method": "tools/call",
        "params": {
            "name": "gita_prove",
            "arguments": {
                "root": str(git_repo),
                "name": "pytest",
                "cmd": [_sys.executable, "-c", "pass"],
            },
        },
    })
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["name"] == "pytest"
    assert (git_repo / ".git" / "gita" / "proofs" / f"{sha}.json").exists()


def test_mcp_gita_last_proven_no_proofs_returns_error(git_repo: Path) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 24, "method": "tools/call",
        "params": {
            "name": "gita_last_proven",
            "arguments": {"root": str(git_repo)},
        },
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32000
    assert "prove" in resp["error"]["message"].lower()


def test_mcp_gita_diff_supports_symbol_arg(git_repo: Path) -> None:
    s1 = commit_file(
        git_repo, "m.py", "def foo():\n    return 1\n\ndef bar():\n    return 2\n", "init"
    )
    s2 = commit_file(
        git_repo, "m.py", "def foo():\n    return 9\n\ndef bar():\n    return 8\n", "edit both"
    )
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 25, "method": "tools/call",
        "params": {
            "name": "gita_diff",
            "arguments": {
                "root": str(git_repo), "from_ref": s1, "to_ref": s2,
                "symbol": "foo",
            },
        },
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    ops = [op for fe in payload["files"] for op in fe.get("ops", [])]
    names = {op.get("name") for op in ops}
    assert "foo" in names
    assert "bar" not in names


def test_mcp_server_version_is_0_3_0() -> None:
    resp = mcp.handle_request({"jsonrpc": "2.0", "id": 26, "method": "initialize"})
    assert resp["result"]["serverInfo"]["version"] == "0.3.0"


def test_mcp_gita_bisect_proven_returns_suspect(git_repo: Path) -> None:
    """End-to-end: cached good proof on A, failing proof on B → suspect is B."""
    import sys as _sys
    from gita import proofs as proofs_mod

    a = commit_file(git_repo, "s.py", "def foo():\n    return 1\n", "init")
    # Record passing proof against A directly so we have a baseline.
    proofs_mod.proofs_dir(git_repo).mkdir(parents=True, exist_ok=True)
    (proofs_mod.proof_path(git_repo, a)).write_text(
        json.dumps({
            "commit": a,
            "checks": {"pytest": {"ok": True, "exit_code": 0, "duration_ms": 1,
                                  "ran_at": "2026-01-01T00:00:00Z",
                                  "cmd": [_sys.executable, "-c", "pass"],
                                  "stdout_head": "", "stdout_tail": "", "truncated": False}},
        }),
        encoding="utf-8",
    )
    b = commit_file(git_repo, "s.py", "def foo():\n    return 99\n", "break")
    (proofs_mod.proof_path(git_repo, b)).write_text(
        json.dumps({
            "commit": b,
            "checks": {"pytest": {"ok": False, "exit_code": 1, "duration_ms": 1,
                                  "ran_at": "2026-01-01T00:00:01Z",
                                  "cmd": [_sys.executable, "-c", "pass"],
                                  "stdout_head": "", "stdout_tail": "", "truncated": False}},
        }),
        encoding="utf-8",
    )

    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 27, "method": "tools/call",
        "params": {
            "name": "gita_bisect_proven",
            "arguments": {"root": str(git_repo), "name": "pytest"},
        },
    })
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["suspect"] == b
    assert payload["from_sha"] == a
    assert payload["reason"] == "first_failure"
    assert payload["checks_used"] == ["pytest"]


def test_mcp_gita_bisect_proven_no_baseline_returns_error(git_repo: Path) -> None:
    commit_file(git_repo, "s.py", "x = 1\n", "init")
    resp = mcp.handle_request({
        "jsonrpc": "2.0", "id": 28, "method": "tools/call",
        "params": {
            "name": "gita_bisect_proven",
            "arguments": {"root": str(git_repo), "name": "pytest"},
        },
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32000
