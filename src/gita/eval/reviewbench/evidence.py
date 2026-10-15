"""Exact-revision, read-only broker. Corpus content is never executed."""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import hmac
import json
from pathlib import Path
import time
from typing import Any
from ...index.model import GitOid

from .common import (EVIDENCE_CALL_LIMIT, EvidenceBudgetError, IntegrityError, ProtocolError, append_json, checked_path, digest, git,
                     oid, read_json, require, require_integrity, source_lines)

PAGE_CHARS = 6000
MAX_RESPONSE_CHARS = 8000
COMMON_TOOLS = ("raw_diff", "read_file", "search", "list_files", "commit_log", "file_history")
INDEX_TOOLS = ("index_status", "entities", "id", "state", "source", "history", "events")


def schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
CURSOR = {"type": "string", "description": "Opaque next_cursor from this exact query; omit on first page."}
SCHEMAS = {
    "raw_diff": schema({"path": STRING, "cursor": CURSOR}),
    "read_file": schema({"path": STRING, "commit": STRING, "start_line": INTEGER,
                         "end_line": INTEGER, "cursor": CURSOR}, ("path",)),
    "search": schema({"text": STRING, "pattern": STRING, "commit": STRING,
                      "cursor": CURSOR}, ("text",)),
    "list_files": schema({"pattern": STRING, "commit": STRING, "cursor": CURSOR}),
    "commit_log": schema({"cursor": CURSOR}),
    "file_history": schema({"path": STRING, "cursor": CURSOR}, ("path",)),
    "index_status": schema({}),
    "entities": schema({"path": STRING, "commit": STRING, "cursor": CURSOR}, ("path",)),
    "id": schema({"token": STRING, "commit": STRING, "cursor": CURSOR}, ("token",)),
    "state": schema({"eid": STRING, "commit": STRING}, ("eid",)),
    "source": schema({"reference": {"type": "object"}}, ("reference",)),
    "history": schema({"eid": STRING, "commit": STRING, "cursor": CURSOR}, ("eid",)),
    "events": schema({"cursor": CURSOR}),
}
DESCRIPTIONS = {
    "raw_diff": "Exact effective-merge-base to review-head unified Git patch. Optional member path.",
    "read_file": "Exact source lines at an allowed commit. Default head; same history scope in both arms.",
    "search": "Literal search across all tracked text source at an allowed commit; optional path glob.",
    "list_files": "List every tracked member path at an allowed commit; optional glob.",
    "commit_log": "Ordered allowed commits only, with subjects and parents qualified at the scope boundary.",
    "file_history": "Raw Git changes for a member path over the same declared allowed commit graph.",
    "index_status": "Optional: inspect prebuilt index readiness and gaps. For named-function context, entities(path) or id(name) -> state/source can retrieve exact entity evidence instead of whole files. Raw Git fallback is valid. No build or mutation.",
    "entities": "Indexed file entities with exact occurrence references, coverage and opaque continuation.",
    "id": "Resolve an indexed EID or entity name at an allowed revision, preserving ambiguity.",
    "state": "Generation-qualified indexed entity state at an allowed revision.",
    "source": "Exact source of a complete previously issued occurrence reference. Over-budget is an error.",
    "history": "Indexed entity events up to an allowed revision; first-parent scopes only.",
    "events": "Indexed range events in (effective merge base, review head]; NOT an endpoint diff.",
}


def inventory(arm: str) -> list[dict]:
    require(arm in ("B", "H"), "Unknown arm")
    names = COMMON_TOOLS + (INDEX_TOOLS if arm == "H" else ())
    return [{"name": name, "description": DESCRIPTIONS[name], "inputSchema": SCHEMAS[name]}
            for name in names]


class Evidence:
    def __init__(self, config: dict):
        self.config = config
        self.repo = Path(config["repo"])
        self.base = oid(config["effective_base"])
        self.head = oid(config["task"]["head"])
        self.allowed = tuple(config["allowed_commits"])
        require(len(self.allowed) == len(set(self.allowed)) and bool(self.allowed), "Invalid commit scope")
        for commit in self.allowed:
            oid(commit)
        require(self.base in self.allowed and self.head in self.allowed, "Endpoint outside task scope")
        self.arm = config["arm"]
        self.tools = {tool["name"] for tool in inventory(self.arm)}
        self.calls = 0
        self.log = Path(config["tool_log"]) if config.get("tool_log") else None
        self.secret = config["cursor_secret"].encode()
        self.issued: dict[str, dict] = {}
        self.sessions: dict[str, Any] = {}
        self.repository = None
        self._paths: dict[str, dict[str, tuple[str, str]]] = {}
        self._blobs: dict[tuple[str, str], bytes] = {}
        # Check task scope against actual Git ancestry, not a self-consistent manifest hash.
        reachable = set(git(self.repo, "rev-list", self.head, config["task"]["base"])
                        .decode().splitlines())
        require_integrity(set(self.allowed) <= reachable, "Allowed graph contains future/unreachable commits")
        actual_base = git(self.repo, "merge-base", config["task"]["base"], self.head).decode().strip()
        require_integrity(actual_base == self.base, "Wrong effective merge base")
        graph = config.get("graph", {})
        parent_rows = git(self.repo, "show", "-s", "--format=%H %P",
                          "--no-walk=unsorted", *self.allowed).decode().splitlines()
        actual_graph = {}
        for row in parent_rows:
            try:
                fields = row.split()
                commit, parents = oid(fields[0]), [oid(value) for value in fields[1:]]
            except (ProtocolError, IndexError) as error:
                raise IntegrityError("Malformed Git parent graph") from error
            require_integrity(commit not in actual_graph, "Duplicate Git parent record")
            actual_graph[commit] = parents
        require_integrity(set(actual_graph) == set(self.allowed), "Incomplete Git parent graph")
        for commit in self.allowed:
            expected = [parent for parent in actual_graph[commit] if parent in self.allowed]
            require_integrity(graph.get(commit) == expected, "Wrong scoped parent graph")

    def close(self) -> None:
        for session in self.sessions.values():
            session.close()
        if self.repository is not None:
            self.repository.close()

    def commit(self, value: str | None = None) -> str:
        value = self.head if value is None else oid(value)
        require(value in self.allowed, "Revision outside task allowlist")
        return value

    def members(self, commit: str) -> dict[str, tuple[str, str]]:
        commit = self.commit(commit)
        if commit not in self._paths:
            entries = {}
            for record in git(self.repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
                if not record:
                    continue
                fields, raw_path = record.split(b"\t", 1)
                mode, kind, blob = fields.decode().split()
                try:
                    path = raw_path.decode("utf8", "strict")
                    checked_path(path)
                except (UnicodeError, ProtocolError):
                    continue
                if kind == "blob":
                    entries[path] = (mode, blob)
            self._paths[commit] = entries
        return self._paths[commit]

    def paths(self, commit: str) -> list[str]:
        return list(self.members(commit))

    def blob(self, path: str, commit: str) -> bytes:
        path, commit = checked_path(path), self.commit(commit)
        members = self.members(commit)
        require(path in members, "File not present at requested exact revision")
        key = (commit, path)
        if key not in self._blobs:
            blob = git(self.repo, "cat-file", "blob", members[path][1])
            require_integrity(hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest()
                              == members[path][1], "Git source digest mismatch")
            self._blobs[key] = blob
        return self._blobs[key]

    def provenance(self, commit: str | None = None) -> dict:
        return {"task_scope": self.config["scope_digest"],
                "commit": self.commit(commit), "effective_base": self.base,
                "head": self.head, "source": "exact_git_objects",
                "allowed_commit_count": len(self.allowed)}

    def _encode(self, value: dict) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(payload).decode() + "." + hmac.new(
            self.secret, payload, "sha256").hexdigest()

    def _decode(self, token: str, binding: str) -> dict:
        try:
            encoded, signature = token.split(".")
            payload = base64.b64decode(encoded, altchars=b"-_", validate=True)
            require(hmac.compare_digest(signature, hmac.new(self.secret, payload, "sha256").hexdigest()),
                    "Tampered cursor")
            value = json.loads(payload)
            require(value["binding"] == binding, "Cursor belongs to a different query/scope")
            return value
        except (ValueError, KeyError, TypeError) as error:
            raise ProtocolError("Invalid cursor") from error

    def page(self, text: str, name: str, arguments: dict, cursor: str = "") -> dict:
        binding = digest([self.config["scope_digest"], name, arguments, digest(text)])
        offset = 0 if not cursor else self._decode(cursor, binding)["offset"]
        require(type(offset) is int and 0 <= offset <= len(text), "Invalid continuation offset")
        end = min(len(text), offset + PAGE_CHARS)
        while True:
            result = {"text": text[offset:end], "next_cursor": self._encode(
                {"binding": binding, "offset": end}) if end < len(text) else None,
                "coverage": {"source": "git", "total_characters": len(text),
                             "shown_characters": end - offset},
                "omitted": {"characters": len(text) - end}, "truncated": end < len(text)}
            if len(json.dumps(result, ensure_ascii=True)) <= MAX_RESPONSE_CHARS - 2000:
                return result
            if end - offset <= 1:
                raise EvidenceBudgetError("Raw evidence metadata exceeds output budget")
            end = offset + max(1, (end - offset) // 2)

    def raw_diff(self, path: str = "", cursor: str = "") -> dict:
        if path:
            checked_path(path)
            require(path in set(self.paths(self.base)) | set(self.paths(self.head)), "Nonmember diff path")
        patch = git(self.repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=3",
                    self.base, self.head, "--", *([path] if path else [])).decode("utf8", "replace")
        return {**self.page(patch, "raw_diff", {"path": path}, cursor),
                "provenance": self.provenance()}

    def read_file(self, path: str, commit: str | None = None, start_line: int = 1,
                  end_line: int = 200, cursor: str = "") -> dict:
        commit = self.commit(commit)
        require(type(start_line) is int and type(end_line) is int
                and 1 <= start_line <= end_line < start_line + 1000, "Invalid source line request")
        source = self.blob(path, commit)
        require(b"\0" not in source, "Binary file; not a text source oracle")
        lines = source_lines(source)
        require(start_line <= max(1, len(lines)), "Source start line out of range")
        rendered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1)
                             if start_line <= i <= end_line)
        args = {"path": path, "commit": commit, "start_line": start_line, "end_line": end_line}
        result = {**self.page(rendered, "read_file", args, cursor), "file_lines": len(lines),
                  "provenance": self.provenance(commit), "blob": self.members(commit)[path][1]}
        try:
            source.decode("utf8", "strict")
            lossy = False
        except UnicodeDecodeError:
            lossy = True
        result["coverage"].update(text_encoding="utf8_replace" if lossy else "utf8", lossy_decoding=lossy)
        return result

    def list_files(self, pattern: str = "*", commit: str | None = None, cursor: str = "") -> dict:
        commit = self.commit(commit)
        require(type(pattern) is str and len(pattern) <= 500, "Invalid path glob")
        text = "\n".join(p for p in self.paths(commit) if fnmatch.fnmatchcase(p, pattern))
        return {**self.page(text, "list_files", {"pattern": pattern, "commit": commit}, cursor),
                "provenance": self.provenance(commit)}

    def search(self, text: str, pattern: str = "*", commit: str | None = None, cursor: str = "") -> dict:
        commit = self.commit(commit)
        require(type(text) is str and 0 < len(text) <= 500 and not any(c in text for c in "\0\r\n"),
                "Expected a nonempty literal single-line query")
        require(type(pattern) is str and len(pattern) <= 500, "Invalid path glob")
        # Git grep reads only one fixed tree and does not interpret the literal as an option.
        found = git(self.repo, "grep", "-n", "-I", "-F", "-e", text, commit,
                    "--", accepted=(0, 1)).decode("utf8", "replace")
        matches = []
        prefix = commit + ":"
        for line in found.split("\n"):
            if not line:
                continue
            if not line.startswith(prefix):
                raise ProtocolError("Search emitted unexpected revision")
            relative = line[len(prefix):]
            # Path membership is verified independently of git grep's rendering.
            for path in self.paths(commit):
                if relative.startswith(path + ":") and fnmatch.fnmatchcase(path, pattern):
                    matches.append(relative)
                    break
        return {**self.page("\n".join(matches), "search",
                           {"text": text, "pattern": pattern, "commit": commit}, cursor),
                "provenance": self.provenance(commit)}

    def commit_log(self, cursor: str = "") -> dict:
        rows = []
        for commit in self.allowed:
            subject = git(self.repo, "show", "-s", "--format=%s", commit).decode("utf8", "replace").strip()
            rows.append({"commit": commit, "subject": subject,
                         "allowed_parents": self.config["graph"][commit]})
        return {**self.page(json.dumps(rows, ensure_ascii=True), "commit_log", {}, cursor),
                "provenance": self.provenance(), "boundary": self.config["history_boundary"]}

    def file_history(self, path: str, cursor: str = "") -> dict:
        path = checked_path(path)
        require(any(path in self.members(commit) for commit in self.allowed), "Nonmember history path")
        patches = []
        for commit in self.allowed:
            for parent in self.config["graph"][commit]:
                patch = git(self.repo, "diff", "--no-ext-diff", "--no-textconv",
                            parent, commit, "--", path).decode("utf8", "replace")
                if patch:
                    patches.append(f"commit {commit}\nparent {parent}\n{patch}")
        return {**self.page("\n".join(patches), "file_history", {"path": path}, cursor),
                "provenance": self.provenance(), "boundary": self.config["history_boundary"]}

    def index_status(self) -> dict:
        return {"contexts": self.config.get("indexes", []),
                "preparation": self.config.get("index_preparation"),
                "history_ready": self.config.get("history_ready", False),
                "coverage": {"status": "available" if self.config.get("indexes") else "unavailable"},
                "omitted": {"history": not self.config.get("history_ready", False)},
                "next_cursor": None, "provenance": self.provenance()}

    def _index(self, commit: str):
        from ...index.query import open_index
        from ...vcs.git import CommittedRepo
        records = [i for i in self.config.get("indexes", []) if commit in i["commits"]]
        require(bool(records), "Indexed evidence unavailable for this revision; common Git tools remain available")
        selected = records[0]
        if selected["generation"] not in self.sessions:
            if self.repository is None:
                self.repository = CommittedRepo(self.repo)
            session = open_index(self.repository, scope=selected["scope"],
                                 generation=selected["generation"])
            try:
                require_integrity(session.context.id == selected["context"], "Index context changed")
                require_integrity(session.generation == selected["generation"], "Index generation changed")
                require_integrity(session.scope == selected["scope"], "Index scope changed")
                require_integrity(session.tip.digest.hex() == selected["tip"], "Index generation tip changed")
                require_integrity(set(selected["commits"]) <= set(self.allowed), "Index source scope expanded")
            except BaseException:
                session.close()
                raise
            self.sessions[selected["generation"]] = session
        return self.sessions[selected["generation"]]

    def _occurrences(self, value: Any, session: Any) -> None:
        try:
            self._checked_occurrences(value, session)
        except IntegrityError:
            raise
        except (ProtocolError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
            raise IntegrityError("Malformed or out-of-scope returned indexed provenance") from error

    def _checked_occurrences(self, value: Any, session: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._occurrences(item, session)
        elif isinstance(value, dict):
            if {"generation", "path_bytes_b64", "span", "blob", "commit", "id"} <= set(value):
                require_integrity(value["generation"] == session.generation, "Wrong occurrence generation")
                commit = self.commit(value["commit"]["hex"])
                path = base64.b64decode(value["path_bytes_b64"], validate=True).decode("utf8", "strict")
                require_integrity(path == value["path"], "Occurrence source path tamper")
                blob = self.blob(path, commit)
                require_integrity(value["blob"]["hex"] == self.members(commit)[path][1], "Occurrence blob tamper")
                span = value["span"]
                start, end = span["start_byte"], span["end_byte"]
                require_integrity(type(start) is int and type(end) is int and 0 <= start <= end <= len(blob),
                                  "Occurrence source bounds tamper")
                expected = blob[start:end]
                require_integrity(session.source(value) == expected, "Indexed source differs from independent Git source")
                require_integrity(span["start_line"] == 1 + blob[:start].count(b"\n"),
                                  "Occurrence start-line tamper")
                expected_end = 1 + blob[:end].count(b"\n")
                if end > start and blob[end - 1:end] == b"\n":
                    expected_end -= 1
                require_integrity(span["end_line"] == max(span["start_line"], expected_end),
                                  "Occurrence end-line tamper")
                self.issued[digest(value)] = value
                return
            for key, item in value.items():
                if key == "generation":
                    require_integrity(item == session.generation, "Wrong returned generation")
                if key == "task_scope":
                    require_integrity(item == self.config["scope_digest"], "Wrong returned task scope")
                if key == "scope":
                    require_integrity(item == session.scope, "Wrong returned index scope")
                if key in ("identity_context", "context_id"):
                    require_integrity(item == session.context.id, "Wrong returned identity context")
                if key in ("commit", "parent", "anchor", "tip") and type(item) is dict and "hex" in item:
                    require_integrity(item.get("object_format") == "sha1", "Wrong returned commit object format")
                    self.commit(item["hex"])
                elif key in ("commit", "parent", "anchor", "tip") and type(item) is str:
                    self.commit(item)
                self._occurrences(item, session)

    def _query(self, name: str, commit: str, arguments: dict, cursor: str = "") -> dict:
        commit = self.commit(commit)
        session = self._index(commit)
        selected = GitOid.from_hex("sha1", commit)
        binding = digest([self.config["scope_digest"], session.generation, name, commit, arguments])
        native_cursor = self._decode(cursor, binding)["native"] if cursor else None
        limit = 30
        while True:
            if name == "entities":
                result = session.entities_in(arguments["path"].encode("utf8"), at=selected,
                                             limit=limit, cursor=native_cursor)
            elif name == "id":
                result = session.resolve(arguments["token"], at=selected, historical=False,
                                         limit=limit, cursor=native_cursor)
            elif name == "state":
                result = session.state_at(arguments["eid"], at=selected)
            elif name == "history":
                require(self.config["history_ready"], "Indexed history unavailable across separate endpoint contexts")
                result = session.history(arguments["eid"], at=selected, limit=limit, cursor=native_cursor)
            else:
                require(self.config["history_ready"], "No common first-parent generation; use raw_diff")
                result = session.events_in(GitOid.from_hex("sha1", self.base), selected,
                                           limit=limit, cursor=native_cursor)
            require_integrity(type(result) is dict, "Malformed returned indexed evidence")
            native_next = result.get("page", {}).get("next_cursor")
            if "page" in result:
                result["page"]["next_cursor"] = self._encode(
                    {"binding": binding, "native": native_next}) if native_next else None
            result.update(provenance={**self.provenance(commit), "generation": session.generation,
                                      "identity_context": session.context.id,
                                      "source": "indexed_verified_against_git"})
            result.setdefault("coverage", {"status": "generation_qualified"})
            result.setdefault("omitted", {"count": 0})
            result.setdefault("next_cursor", result.get("page", {}).get("next_cursor"))
            if len(json.dumps(result, ensure_ascii=True)) <= MAX_RESPONSE_CHARS:
                break
            if name == "state" or limit <= 1:
                raise EvidenceBudgetError("Indexed evidence output limit; narrow request")
            limit = max(1, limit // 2)
        self._occurrences(result, session)
        return result

    def entities(self, path: str, commit: str | None = None, cursor: str = "") -> dict:
        checked_path(path)
        return self._query("entities", self.commit(commit), {"path": path}, cursor)

    def id(self, token: str, commit: str | None = None, cursor: str = "") -> dict:
        return self._query("id", self.commit(commit), {"token": token}, cursor)

    def state(self, eid: str, commit: str | None = None) -> dict:
        return self._query("state", self.commit(commit), {"eid": eid})

    def history(self, eid: str, commit: str | None = None, cursor: str = "") -> dict:
        return self._query("history", self.commit(commit), {"eid": eid}, cursor)

    def events(self, cursor: str = "") -> dict:
        return self._query("events", self.head, {}, cursor)

    def source(self, reference: dict) -> dict:
        require(digest(reference) in self.issued, "Unissued or tampered source reference")
        commit = self.commit(reference["commit"]["hex"])
        session = self._index(commit)
        self._occurrences(reference, session)
        source = session.source(reference)
        span = reference["span"]
        require_integrity(source == self.blob(reference["path"], commit)[span["start_byte"]:span["end_byte"]],
                          "Returned indexed source changed after verification")
        if len(source) > PAGE_CHARS:
            raise EvidenceBudgetError("Exact indexed source exceeds source-output cap")
        return {"text": source.decode("utf8", "strict"), "reference": reference,
                "provenance": {**self.provenance(commit), "generation": session.generation,
                               "identity_context": session.context.id},
                "coverage": {"exact_span": True}, "omitted": {"bytes": 0}, "next_cursor": None}

    def execute(self, name: str, arguments: dict, request_id: str | None = None) -> dict:
        start = time.monotonic()
        self.calls += 1
        try:
            if self.calls > EVIDENCE_CALL_LIMIT:
                raise EvidenceBudgetError("Evidence call limit reached; finalize the review without further evidence")
            require(name in self.tools, "Tool unavailable in this arm")
            require(type(arguments) is dict, "Invalid tool arguments")
            require(set(arguments) <= set(SCHEMAS[name]["properties"]), "Unexpected tool argument")
            require(set(SCHEMAS[name]["required"]) <= set(arguments), "Missing tool argument")
            result = getattr(self, name)(**arguments)
            if len(json.dumps(result, ensure_ascii=True)) > MAX_RESPONSE_CHARS:
                raise EvidenceBudgetError(
                    "Evidence exceeds safe SDK output budget; narrow the request or use raw source lines")
            ok = True
        except (ProtocolError, ValueError, TypeError, KeyError, RuntimeError) as error:
            code = getattr(error, "code", "refused")
            if code in ("corrupt", "invalid_source", "match_mismatch", "parse_mismatch"):
                code = IntegrityError.code
            result, ok = {"error": {"code": code,
                                   "message": str(error)}, "next_cursor": None,
                          "coverage": {"status": "unavailable"}, "omitted": {"unknown": True}}, False
        if self.log is not None:
            append_json(self.log, {"sequence": self.calls, "request_id": request_id, "tool": name,
                                   "arguments": arguments, "argument_digest": digest(arguments),
                                   "result_digest": digest(result), "ok": ok, "result": result,
                                   "seconds": time.monotonic() - start})
        return result


def serve(config: dict) -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    import anyio
    evidence = Evidence(config)
    async def list_tools():
        return [Tool(**tool) for tool in inventory(config["arm"])]

    async def call_tool(name: str, arguments: dict, request_id: str):
        result = evidence.execute(name, arguments, request_id)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=True))]

    if hasattr(Server, "list_tools"):
        server = Server("review")
        server.list_tools()(list_tools)

        @server.call_tool()
        async def call_legacy(name: str, arguments: dict):
            return await call_tool(name, arguments, str(server.request_context.request_id))
    else:
        async def on_list(context, parameters):
            return ListToolsResult(tools=await list_tools())

        async def on_call(context, parameters):
            content = await call_tool(parameters.name, parameters.arguments or {}, str(context.request_id))
            result = json.loads(content[0].text)
            return CallToolResult(content=content, isError="error" in result)

        server = Server("review", on_list_tools=on_list, on_call_tool=on_call)

    async def run():
        try:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            evidence.close()
    anyio.run(run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    serve(read_json(parser.parse_args().config))
