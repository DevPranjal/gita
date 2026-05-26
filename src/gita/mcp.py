"""Minimal MCP server over stdio.

Implements just enough of the Model Context Protocol (JSON-RPC 2.0,
``initialize`` + ``tools/list`` + ``tools/call``) to expose gita's read
surface to an agent. No external SDK — one stdin/stdout loop, line-delimited
JSON, content-length headers are optional and we accept both forms.

Tools exposed:

* ``gita_diff``       — manifest for working tree or two refs
* ``gita_status``     — porcelain status + summary
* ``gita_explain``    — manifest for a single commit
* ``gita_symbol_log`` — commits touching a symbol
* ``gita_callers``    — call sites of a symbol

Every tool takes an optional ``root`` arg (path to the git repo). If
omitted, we use ``$GITA_ROOT`` env var, else cwd.

This server is read-only on purpose: an agent should propose changes, not
silently rewrite history.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from . import callers as callers_mod
from . import git as gx
from . import history as history_mod
from . import lookup as lookup_mod
from . import proofs as proofs_mod
from .cli import _filter_manifest
from .diff import build_for_refs, build_for_working_tree


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "gita"
SERVER_VERSION = "0.2.0"


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------


def _resolve_root(args: dict[str, Any]) -> Path:
    raw = args.get("root") or os.environ.get("GITA_ROOT") or os.getcwd()
    return gx.discover_root(Path(raw))


def _tool_diff(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    staged = bool(args.get("staged"))
    from_ref = args.get("from_ref")
    to_ref = args.get("to_ref")
    symbol = args.get("symbol")
    if staged:
        manifest = build_for_working_tree(root, staged=True)
    elif from_ref is None and to_ref is None:
        manifest = build_for_working_tree(root, staged=False)
    elif to_ref is None:
        manifest = build_for_refs(root, from_ref, "HEAD")
    else:
        manifest = build_for_refs(root, from_ref, to_ref)
    if symbol:
        manifest = _filter_manifest(manifest, only=None, exclude=None, symbol=symbol)
    return manifest


def _tool_status(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    entries = gx.status(root)
    manifest = build_for_working_tree(root, staged=False)
    return {
        "branch": gx.current_branch(root),
        "head": gx.head_sha(root),
        "entries": [
            {
                "path": e.path,
                "index": e.index_status,
                "work": e.work_status,
                "orig_path": e.orig_path,
            }
            for e in entries
        ],
        "summary": manifest["summary"],
    }


def _tool_explain(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    ref = args.get("ref") or "HEAD"
    sha = gx.rev_parse(root, ref)
    manifest = history_mod.manifest_for(root, sha)
    meta = gx.commit_meta(root, sha)
    return {
        "commit": sha,
        "author": meta.author_name,
        "email": meta.author_email,
        "timestamp": meta.timestamp,
        "message": meta.message,
        "manifest": manifest,
    }


def _tool_symbol_log(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    name = args["name"]
    max_count = args.get("max_count")
    entries = history_mod.symbol_log(root, name, max_count=max_count)
    return {"name": name, "commits": entries}


def _tool_callers(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    name = args["name"]
    ref = args.get("ref")
    hits = callers_mod.find(root, name, ref=ref)
    return {"name": name, "ref": ref, "callers": hits}


def _tool_get(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    name = args["name"]
    rev = args.get("rev") or "HEAD"
    sym = lookup_mod.get(root, name, rev=rev)
    return {
        "name": sym.name,
        "kind": sym.kind,
        "path": sym.path,
        "line_start": sym.line_start,
        "line_end": sym.line_end,
        "signature": sym.signature,
        "body": sym.body,
        "rev": sym.rev,
        "requested_as": sym.requested_as,
        "parent": sym.parent,
    }


def _tool_prove(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    name = args["name"]
    cmd = list(args["cmd"])
    result = proofs_mod.record(root, name, cmd=cmd)
    return {
        "name": result.name,
        "ok": result.ok,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
    }


def _tool_last_proven(args: dict[str, Any]) -> dict[str, Any]:
    root = _resolve_root(args)
    name = args.get("name")
    symbol = args.get("symbol")
    ref = args.get("ref") or "HEAD"
    sha = proofs_mod.last_proven(root, name, symbol=symbol, ref=ref)
    return {"sha": sha, "name": name, "symbol": symbol, "ref": ref}


TOOLS: dict[str, tuple[Callable[[dict[str, Any]], Any], dict[str, Any]]] = {
    "gita_diff": (
        _tool_diff,
        {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Path inside the git repo (default: cwd)."},
                "from_ref": {"type": ["string", "null"], "description": "Base ref (commit/branch)."},
                "to_ref": {"type": ["string", "null"], "description": "Target ref (default: working tree)."},
                "staged": {"type": "boolean", "description": "If true, diff HEAD vs index."},
                "symbol": {"type": ["string", "null"], "description": "Keep only ops mentioning NAME."},
            },
        },
    ),
    "gita_status": (
        _tool_status,
        {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
            },
        },
    ),
    "gita_explain": (
        _tool_explain,
        {
            "type": "object",
            "required": ["ref"],
            "properties": {
                "root": {"type": "string"},
                "ref": {"type": "string", "description": "Commit sha or ref name."},
            },
        },
    ),
    "gita_symbol_log": (
        _tool_symbol_log,
        {
            "type": "object",
            "required": ["name"],
            "properties": {
                "root": {"type": "string"},
                "name": {"type": "string", "description": "Symbol name (function or class)."},
                "max_count": {"type": ["integer", "null"]},
            },
        },
    ),
    "gita_callers": (
        _tool_callers,
        {
            "type": "object",
            "required": ["name"],
            "properties": {
                "root": {"type": "string"},
                "name": {"type": "string"},
                "ref": {"type": ["string", "null"], "description": "Default HEAD."},
            },
        },
    ),
    "gita_get": (
        _tool_get,
        {
            "type": "object",
            "required": ["name"],
            "properties": {
                "root": {"type": "string"},
                "name": {"type": "string", "description": "Symbol name."},
                "rev": {"type": ["string", "null"], "description": "Default HEAD; uses one-hop rename walk on miss."},
            },
        },
    ),
    "gita_prove": (
        _tool_prove,
        {
            "type": "object",
            "required": ["name", "cmd"],
            "properties": {
                "root": {"type": "string"},
                "name": {"type": "string", "description": "Check name, e.g. 'pytest'."},
                "cmd": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command to run; recorded against HEAD.",
                },
            },
        },
    ),
    "gita_last_proven": (
        _tool_last_proven,
        {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "name": {"type": ["string", "null"], "description": "Optional check name; default = all recorded checks must pass."},
                "symbol": {"type": ["string", "null"], "description": "Additionally require symbol to exist at the commit."},
                "ref": {"type": ["string", "null"], "description": "Default HEAD."},
            },
        },
    ),
}


TOOL_DESCRIPTIONS = {
    "gita_diff": "Structural change manifest (symbol-level ops). Default: HEAD vs working tree.",
    "gita_status": "Porcelain status of the working tree + manifest op-category counts.",
    "gita_explain": "Manifest stored with a single commit. Compact summary of what the commit did.",
    "gita_symbol_log": "Every commit whose manifest touches a named symbol (rename-aware).",
    "gita_callers": "Call sites of a symbol across the whole tree at a given ref (default HEAD).",
    "gita_get": "Source of a named symbol at a rev. One-hop backward rename walk when missing at rev.",
    "gita_prove": "Run a command and record the result as a proof on HEAD. Refuses dirty trees.",
    "gita_last_proven": "Newest commit reachable from ref whose recorded checks satisfy the filter.",
}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------


def _make_response(req_id: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def handle_request(req: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC request. Returns the response dict, or None for notifications."""
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}
    is_notification = req_id is None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "initialized" or method == "notifications/initialized":
            return None  # notification
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": TOOL_DESCRIPTIONS[name],
                        "inputSchema": schema,
                    }
                    for name, (_fn, schema) in TOOLS.items()
                ]
            }
        elif method == "tools/call":
            name = params.get("name")
            tool_args = params.get("arguments") or {}
            if name not in TOOLS:
                if is_notification:
                    return None
                return _make_response(req_id, error={"code": -32601, "message": f"unknown tool: {name}"})
            fn, _schema = TOOLS[name]
            payload = fn(tool_args)
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        elif method == "ping":
            result = {}
        else:
            if is_notification:
                return None
            return _make_response(req_id, error={"code": -32601, "message": f"unknown method: {method}"})
    except lookup_mod.Ambiguous as exc:
        if is_notification:
            return None
        return _make_response(
            req_id,
            error={
                "code": -32602,
                "message": f"ambiguous symbol: {exc.name!r}",
                "data": {"candidates": list(exc.candidates)},
            },
        )
    except proofs_mod.NoProofs as exc:
        if is_notification:
            return None
        return _make_response(
            req_id,
            error={
                "code": -32000,
                "message": f"no proofs recorded; try 'gita prove <check> -- <cmd>' ({exc})",
            },
        )
    except Exception as exc:
        if is_notification:
            return None
        return _make_response(
            req_id,
            error={
                "code": -32000,
                "message": str(exc),
                "data": {"traceback": traceback.format_exc()},
            },
        )

    if is_notification:
        return None
    return _make_response(req_id, result=result)


def serve_stdio() -> None:
    """Line-delimited JSON-RPC loop over stdin/stdout."""
    in_stream = sys.stdin
    out_stream = sys.stdout
    for raw in in_stream:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            out_stream.write(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }) + "\n")
            out_stream.flush()
            continue
        resp = handle_request(req)
        if resp is not None:
            out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
            out_stream.flush()
