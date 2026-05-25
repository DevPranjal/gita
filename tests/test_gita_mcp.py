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
        "gita_diff", "gita_status", "gita_explain", "gita_symbol_log", "gita_callers"
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
