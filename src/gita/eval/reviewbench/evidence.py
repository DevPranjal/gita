"""Exact-revision, read-only broker. Corpus content is never executed."""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import hmac
import json
import re
import difflib
from pathlib import Path
import time
from typing import Any
from ...index.model import GitOid
from ...source_delivery import SourceDeliveryLedger, SourceKey

from .common import (EVIDENCE_CALL_LIMIT, EvidenceBudgetError, IntegrityError, ProtocolError, append_json, checked_path, digest, git,
                     oid, read_json, require, require_integrity, source_lines)

PAGE_CHARS = 6000
MAX_RESPONSE_CHARS = 8000
MAX_TOOL_WIRE_CHARS = 20000
COMMON_TOOLS = ("raw_diff", "read_file", "search", "list_files", "commit_log", "file_history",
                "continue_evidence")
INDEX_TOOLS = ("change_context", "index_status", "entities", "entity_context", "entity_diff", "id", "state", "source", "history", "events")


def schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


STRING = {"type": "string"}
INTEGER = {"type": "integer"}
FORCE_FULL = {"type": "boolean", "description": "Return requested source even when already delivered earlier."}
CURSOR = {"type": "string", "description": "Opaque next_cursor from this exact query; omit on first page."}
SCHEMAS = {
    "raw_diff": schema({"path": STRING, "cursor": CURSOR}),
    "read_file": schema({"path": STRING, "commit": STRING, "start_line": INTEGER,
                         "end_line": INTEGER, "cursor": CURSOR, "force_full": FORCE_FULL}, ("path",)),
    "search": schema({"text": STRING, "pattern": STRING,
                      "path": {"type": "string", "description": "Path glob alias for pattern; do not give conflicting values."},
                      "commit": STRING,
                      "cursor": CURSOR}, ("text",)),
    "list_files": schema({"pattern": STRING, "commit": STRING, "cursor": CURSOR}),
    "commit_log": schema({"cursor": CURSOR}),
    "file_history": schema({"path": STRING, "cursor": CURSOR}, ("path",)),
    "continue_evidence": schema({"cursor": CURSOR}, ("cursor",)),
    "gita_review": schema({
        "operation": {"type": "string", "enum": ["changes", "source", "context", "diff", "history", "raw_patch", "status"]},
        "path": STRING, "line": INTEGER,
        "token": {"type": "string", "description": "Prefer a simple name plus optional path, or a full EID. Separate captured scope components with :: in a qualified name."},
        "commit": STRING,
        "cursor": CURSOR, "force_full": FORCE_FULL}, ("operation",)),
    "index_status": schema({}),
    "change_context": schema({"path": STRING, "cursor": CURSOR}),
    "entities": schema({"path": STRING, "commit": STRING, "cursor": CURSOR}, ("path",)),
    "entity_context": schema({"path": STRING, "line": INTEGER, "commit": STRING, "cursor": CURSOR,
                              "force_full": FORCE_FULL},
                             ("path", "line")),
    "entity_diff": schema({"token": STRING, "path": STRING, "cursor": CURSOR}, ("token",)),
    "id": schema({"token": STRING, "commit": STRING, "cursor": CURSOR}, ("token",)),
    "state": schema({"eid": STRING, "commit": STRING}, ("eid",)),
    "source": schema({"reference": {"type": "object"}}, ("reference",)),
    "history": schema({"eid": STRING, "commit": STRING, "cursor": CURSOR}, ("eid",)),
    "events": schema({"cursor": CURSOR}),
}
DESCRIPTIONS = {
    "raw_diff": "Exact effective-merge-base to review-head unified Git patch. Optional member path.",
    "read_file": "Exact source lines at an allowed commit. Default head; same history scope in both arms.",
    "search": "Literal search across tracked text at an allowed commit; optional path glob or directory. "
              "**/ includes zero directory levels. Coverage reports matching tracked files.",
    "list_files": "List tracked paths at an allowed commit; optional glob or directory. "
                  "**/ includes zero directory levels. Coverage reports matching tracked files.",
    "commit_log": "Ordered allowed commits only, with subjects and parents qualified at the scope boundary.",
    "file_history": "Raw Git changes for a member path over the same declared allowed commit graph.",
    "continue_evidence": "Continue any previously issued next_cursor without copying the original tool arguments. Cursor must belong to this session. Preferred for all paginated evidence.",
    "gita_review": "Code evidence: context(token,path?) uses path as a hint, finds a published definition even when imported from elsewhere, or returns explicitly RAW literal context for unindexed names/constants. This is lexical evidence, not a resolved caller graph. source(token,path?) is strict indexed function/class lookup; source(path,line) gives enclosing context. changes=changed-code map for the fixed PR (optional commit may repeat its HEAD, never retarget it); diff(token,path)=entity endpoint diff; history(token,path)=verified code-change ledger; status=coverage. raw_patch(path?) is exact Git fallback. Ambiguity is explicit; no parsing/build. Use continue_evidence for pages.",
    "index_status": "Optional: inspect prebuilt index readiness and gaps. For named-function context, entities(path) or id(name) -> state/source can retrieve exact entity evidence instead of whole files. Raw Git fallback is valid. No build or mutation.",
    "change_context": "Gita review starting point: indexed changed-code map plus up to two verified persistent-entity source-diff previews when connected history permits. Returns actual code evidence, locations and explicit gaps; use entity_diff/entity_context for more. Not a complete semantic PR diff.",
    "entities": "Indexed file entities with exact occurrence references, coverage and opaque continuation.",
    "entity_context": "Read bounded enclosing indexed source at a file line, including parent function context rather than only a tiny nested callback. Exact Git-verified bytes; pages large entities. Use raw read_file if coverage is unavailable.",
    "entity_diff": "Compare exact base/head source of a uniquely resolved persistent entity in connected indexed history. Supply a function name and optional HEAD path. Preserves rename/move identity; refuses ambiguous or unqualified endpoints. This is one entity's source diff, not a complete PR diff.",
    "id": "Resolve an indexed EID or entity name at an allowed revision, preserving ambiguity.",
    "state": "Generation-qualified indexed entity state at an allowed revision.",
    "source": "Exact source of a complete previously issued occurrence reference. Over-budget is an error.",
    "history": "Indexed entity events up to an allowed revision; first-parent scopes only.",
    "events": "Indexed range events in (effective merge base, review head]; NOT an endpoint diff.",
}


def inventory(arm: str, *, workflow: bool = False, history_ready: bool = True) -> list[dict]:
    require(arm in ("B", "H"), "Unknown arm")
    indexed = INDEX_TOOLS
    if workflow:
        indexed = ("change_context", "entity_context", "entity_diff", "id", "history", "index_status")
        if not history_ready:
            indexed = tuple(name for name in indexed if name not in ("history", "entity_diff"))
    names = COMMON_TOOLS + (indexed if arm == "H" else ())
    return [{"name": name, "description": (
        "Gita index readiness, declared coverage and connected-history availability; no build or mutation."
        if workflow and name == "index_status" else DESCRIPTIONS[name]), "inputSchema": SCHEMAS[name]}
            for name in names]


def review_operations(config: dict) -> list[str]:
    return [op for op in SCHEMAS["gita_review"]["properties"]["operation"]["enum"]
            if config.get("history_ready") is True or op not in ("diff", "history")]


def tool_inventory(config: dict) -> list[dict]:
    if config.get("engine_frontend") and config["arm"] == "H" and config.get("indexes"):
        names = tuple(name for name in COMMON_TOOLS if name != "raw_diff") + ("gita_review",)
        tools = []
        for name in names:
            shape = SCHEMAS[name]
            if name == "gita_review":
                properties = shape["properties"]
                shape = {**shape, "properties": {
                    **properties, "operation": {
                        **properties["operation"], "enum": review_operations(config)}}}
                if config.get("file_source_enabled"):
                    shape["properties"]["path"] = {
                        "type": "string", "description": "Exact file or a named lookup hint. "
                        "For source, path alone (no token/line) retrieves the whole indexed file."}
            description = DESCRIPTIONS[name]
            if name == "gita_review" and config.get("history_ready") is not True:
                description += " Connected indexed history is unavailable; diff/history operations are not exposed."
            tools.append({"name": name, "description": description, "inputSchema": shape})
        return tools
    tools = inventory(config["arm"], workflow=config.get("indexed_workflow", False),
                      history_ready=config.get("history_ready", False))
    if config.get("indexed_workflow") and not config.get("indexes"):
        return [tool for tool in tools if tool["name"] in COMMON_TOOLS + ("index_status",)]
    return tools


def matches_path(path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if not any(c in pattern for c in "*?["):
        return path.startswith(pattern.rstrip("/") + "/")
    if "**" not in pattern.split("/"):
        return False
    parts = path.split("/")
    positions = {0}
    for component in pattern.split("/"):
        if component == "**":
            positions = set(range(min(positions), len(parts) + 1)) if positions else set()
        else:
            positions = {i + 1 for i in positions
                         if i < len(parts) and fnmatch.fnmatchcase(parts[i], component)}
        if not positions:
            return False
    return len(parts) in positions


class Evidence:
    def __init__(self, config: dict):
        self.config = config
        large_pages = config.get("large_evidence_pages", False)
        require(type(large_pages) is bool, "Large-evidence-page selection must be boolean")
        self.max_response_chars = 16000 if large_pages else MAX_RESPONSE_CHARS
        self.page_chars = 14000 if large_pages else PAGE_CHARS
        require(type(config.get("file_source_enabled", False)) is bool,
                "File-source selection must be boolean")
        self.repo = Path(config["repo"])
        self.base = oid(config["effective_base"])
        self.head = oid(config["task"]["head"])
        self.allowed = tuple(config["allowed_commits"])
        require(len(self.allowed) == len(set(self.allowed)) and bool(self.allowed), "Invalid commit scope")
        for commit in self.allowed:
            oid(commit)
        require(self.base in self.allowed and self.head in self.allowed, "Endpoint outside task scope")
        self.arm = config["arm"]
        self.tools = {tool["name"] for tool in tool_inventory(config)}
        self.calls = 0
        self.log = Path(config["tool_log"]) if config.get("tool_log") else None
        self.secret = config["cursor_secret"].encode()
        self.issued: dict[str, dict] = {}
        self.cursor_values: dict[str, dict] = {}
        self.continuations: dict[str, tuple[str, dict]] = {}
        self.sessions: dict[str, Any] = {}
        self.repository = None
        self._paths: dict[str, dict[str, tuple[str, str]]] = {}
        self._blobs: dict[tuple[str, str], bytes] = {}
        self._change_context: dict[str, str] = {}
        self.source_delivery = SourceDeliveryLedger(max_chars=500_000, max_lines=3000)
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
        token = "cur_" + hmac.new(self.secret, payload, "sha256").hexdigest()[:24]
        self.cursor_values[token] = json.loads(payload)
        return token

    def _decode(self, token: str, binding: str) -> dict:
        try:
            require(token in self.cursor_values, "Unissued or tampered cursor")
            value = self.cursor_values[token]
            require(value["binding"] == binding, "Cursor belongs to a different query/scope")
            return value
        except (ValueError, KeyError, TypeError) as error:
            raise ProtocolError("Invalid cursor") from error

    def fits_response(self, result: dict, *, reserve: int = 0) -> bool:
        serialized = json.dumps(result, ensure_ascii=True)
        return (len(serialized) <= self.max_response_chars - reserve
                and len(json.dumps(serialized, ensure_ascii=True)) <= MAX_TOOL_WIRE_CHARS - reserve)

    def page(self, text: str, name: str, arguments: dict, cursor: str = "") -> dict:
        binding = digest([self.config["scope_digest"], name, arguments, digest(text)])
        offset = 0 if not cursor else self._decode(cursor, binding)["offset"]
        require(type(offset) is int and 0 <= offset <= len(text), "Invalid continuation offset")
        end = min(len(text), offset + self.page_chars)
        low, high, best = offset + 1, end, None
        while True:
            result = {"text": text[offset:end], "next_cursor": self._encode(
                {"binding": binding, "offset": end}) if end < len(text) else None,
                "coverage": {"source": "git", "total_characters": len(text),
                             "shown_characters": end - offset},
                "omitted": {"characters": len(text) - end}, "truncated": end < len(text)}
            if self.fits_response(result, reserve=2000):
                best = result
                low = end + 1
            else:
                high = end - 1
            if low > high:
                if best is None:
                    raise EvidenceBudgetError("Raw evidence metadata exceeds output budget")
                return best
            end = (low + high) // 2

    def continue_evidence(self, cursor: str) -> dict:
        require(cursor in self.continuations, "Continuation was not issued in this session")
        name, arguments = self.continuations[cursor]
        require(name in self.tools and name != "continue_evidence", "Unavailable continuation tool")
        return getattr(self, name)(**{**arguments, "cursor": cursor})

    def gita_review(self, operation: str, path: str = "", line: int | None = None,
                    token: str = "", commit: str | None = None, cursor: str = "",
                    force_full: bool = False) -> dict:
        require(operation in review_operations(self.config),
                "Review operation unavailable for this index preparation")
        if operation == "changes":
            require(line is None and not token and not force_full,
                    "changes accepts path, cursor and an optional fixed HEAD commit")
            require(commit is None or self.commit(commit) == self.head,
                    "changes cannot retarget the fixed PR; commit must equal its HEAD")
            result = self.change_context(path, cursor)
        elif operation == "source":
            if token:
                require(line is None, "source accepts token or path/line, not both")
                result = self.named_source(token, path, commit, cursor, force_full)
            elif line is None and path and self.config.get("file_source_enabled"):
                checked_path(path)
                require(path in self.members(self.commit(commit)),
                        "Whole-file source requires an exact member file")
                result = self.named_source("<module>", path, commit, cursor, force_full)
                result["coverage"]["lookup"] = "exact_indexed_file_root"
            else:
                require(path and line is not None, "source requires token or path/line")
                result = self.entity_context(path, line, commit, cursor, force_full)
        elif operation == "context":
            require(token and line is None, "context requires token and optional path hint")
            result = self.symbol_context(token, path, commit, cursor, force_full)
        elif operation == "diff":
            require(token and line is None and commit is None and not force_full,
                    "diff requires token and optional path/cursor")
            result = self.entity_diff(token, path, cursor)
        elif operation == "history":
            require(token and line is None and not force_full, "history requires a token")
            resolved = self.id(token, commit)
            matches = [r for r in resolved["results"] if r.get("occurrence")
                       and (not path or r["occurrence"]["path"] == path)]
            require(resolved.get("next_cursor") is None and len(matches) == 1,
                    "History token missing/ambiguous; use a qualified name and path")
            result = self.history(matches[0]["eid"], commit, cursor)
        elif operation == "raw_patch":
            require(line is None and not token and commit is None and not force_full,
                    "raw_patch accepts only path and cursor")
            result = self.raw_diff(path, cursor)
        else:
            require(not path and line is None and not token and commit is None and not cursor
                    and not force_full, "status accepts no selectors")
            result = self.index_status()
        result["operation"] = operation
        return result

    def raw_diff(self, path: str = "", cursor: str = "") -> dict:
        if path:
            checked_path(path)
            require(path in set(self.paths(self.base)) | set(self.paths(self.head)), "Nonmember diff path")
        patch = git(self.repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=3",
                    self.base, self.head, "--", *([path] if path else [])).decode("utf8", "replace")
        return {**self.page(patch, "raw_diff", {"path": path}, cursor),
                "provenance": self.provenance()}

    def read_file(self, path: str, commit: str | None = None, start_line: int = 1,
                  end_line: int = 200, cursor: str = "", force_full: bool = False) -> dict:
        require(type(force_full) is bool, "force_full must be boolean")
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
        result["coverage"]["empty_source"] = len(source) == 0
        return result

    def list_files(self, pattern: str = "*", commit: str | None = None, cursor: str = "") -> dict:
        commit = self.commit(commit)
        require(type(pattern) is str and len(pattern) <= 500, "Invalid path glob")
        members = [p for p in self.paths(commit) if matches_path(p, pattern)]
        result = {**self.page("\n".join(members), "list_files",
                             {"pattern": pattern, "commit": commit}, cursor),
                  "provenance": self.provenance(commit)}
        result["coverage"]["matched_files"] = len(members)
        return result

    def search(self, text: str, pattern: str = "*", commit: str | None = None,
               cursor: str = "", path: str | None = None) -> dict:
        commit = self.commit(commit)
        if path is not None:
            require(type(path) is str and bool(path), "Expected a nonempty path glob")
            require(pattern == "*" or pattern == path, "Conflicting path and pattern globs")
            pattern = path
        require(type(text) is str and 0 < len(text) <= 500 and not any(c in text for c in "\0\r\n"),
                "Expected a nonempty literal single-line query")
        require(type(pattern) is str and len(pattern) <= 500, "Invalid path glob")
        members = [p for p in self.paths(commit) if matches_path(p, pattern)]
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
            for path in members:
                if relative.startswith(path + ":"):
                    matches.append(relative)
                    break
        result = {**self.page("\n".join(matches), "search",
                             {"text": text, "pattern": pattern, "commit": commit}, cursor),
                  "provenance": self.provenance(commit)}
        result["coverage"]["matched_files"] = len(members)
        return result

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

    def change_context(self, path: str = "", cursor: str = "") -> dict:
        if path:
            checked_path(path)
            require(path in set(self.paths(self.base)) | set(self.paths(self.head)),
                    "Nonmember change-context path")
        if path not in self._change_context:
            paths = [p.decode("utf8", "strict") for p in git(
                self.repo, "diff", "--no-ext-diff", "--name-only", "-z",
                self.base, self.head, "--", *([":(literal)" + path] if path else [])).split(b"\0") if p]
            lines = ["Indexed changed-code map; anchors are Git lines, not identity/semantic diff claims."]
            preview_candidates = []
            for member in paths:
                if member not in self.members(self.head):
                    lines.append(f"{member}: deleted at HEAD; use base raw/source evidence.")
                    continue
                patch = git(self.repo, "diff", "--no-ext-diff", "--no-textconv", "--unified=0",
                            self.base, self.head, "--", ":(literal)" + member).decode("utf8", "replace")
                anchors = [(int(m.group(1)), int(m.group(2) or "1")) for m in re.finditer(
                    r"(?m)^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", patch)]
                session = self._index(self.head)
                refs, native_cursor = [], None
                while True:
                    page = session.entities_in(member.encode("utf8"), at=GitOid.from_hex("sha1", self.head),
                                               limit=1000, cursor=native_cursor)
                    refs.extend(page["results"])
                    native_cursor = page["page"]["next_cursor"]
                    if not native_cursor:
                        break
                shown = set()
                for start, count in anchors:
                    candidates = [ref for ref in refs if count
                                  and ref["span"]["start_line"] <= start + count - 1
                                  and ref["span"]["end_line"] >= start]
                    named = [r for r in candidates if r["names"] != ["<module>"]]
                    selected = named or candidates
                    bounded = [r for r in selected
                               if r["span"]["end_byte"] - r["span"]["start_byte"] <= 4000]
                    if bounded:
                        selected = [r for r in bounded if not any(
                            other["span"]["start_byte"] <= r["span"]["start_byte"]
                            and other["span"]["end_byte"] >= r["span"]["end_byte"]
                            and other["span"]["end_byte"] - other["span"]["start_byte"]
                            > r["span"]["end_byte"] - r["span"]["start_byte"]
                            for other in bounded)]
                    if not selected:
                        lines.append(f"{member}:{start} (+{count} lines): no indexed entity; raw fallback required.")
                    for ref in selected:
                        if ref["id"] in shown:
                            continue
                        self._occurrences(ref, session)
                        shown.add(ref["id"])
                        span = ref["span"]
                        lines.append(f"{member}:{max(start, span['start_line'])} "
                                     f"[{span['start_line']}-{span['end_line']}] "
                                     + ".".join(ref["names"]))
                        if ref["names"] != ["<module>"]:
                            test_path = bool(re.search(
                                r"(^|/)(__tests__|tests?|specs?)(/|[._])|[._](test|spec)[.]", member))
                            preview_candidates.append((test_path, len(ref["names"]),
                                                       member, ref["names"][-1]))
                if not anchors:
                    lines.append(f"{member}: non-text/mode-only or no textual anchor; raw fallback required.")
            previews, attempted = [], set()
            if self.config.get("history_ready"):
                for _, _, member, token in sorted(set(preview_candidates)):
                    if len(attempted) >= 6 or len(previews) >= 2:
                        break
                    attempted.add((member, token))
                    try:
                        diff = self.entity_diff(token, member)
                    except IntegrityError:
                        raise
                    except ProtocolError as error:
                        lines.append(f"Indexed source preview unavailable for {member} {token}: {error}")
                        continue
                    if diff["text"]:
                        status = "partial preview; request entity_diff for continuation" if diff["truncated"] else "complete entity source diff"
                        previews.append(f"{member} :: {token} ({status})\n{diff['text']}")
            intro = ("Gita verified indexed source previews (at most two; not the whole PR):\n"
                     + "\n\n".join(previews) + "\n\n") if previews else ""
            self._change_context[path] = intro + "\n".join(lines)
        return {**self.page(self._change_context[path], "change_context", {"path": path}, cursor),
                "provenance": {**self.provenance(), "source": "git_anchors_with_verified_indexed_entities"},
                "availability": {"history_ready": self.config.get("history_ready", False),
                                 "published_coverage": [i["build_status"] for i in self.config.get("indexes", [])],
                                 "gaps_are_not_absence": True},
                "semantic_endpoint_diff": False}

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
        compact_history = name == "history" and self.config.get("engine_frontend", False)
        binding = digest([self.config["scope_digest"], session.generation, name, commit, arguments,
                          "code_history_v1" if compact_history else "native"])
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
            if compact_history:
                self._occurrences(result, session)
                result = self._code_history(result, session)
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
            if self.fits_response(result):
                break
            if name == "state" or limit <= 1:
                raise EvidenceBudgetError("Indexed evidence output limit; narrow request")
            limit = max(1, limit // 2)
        self._occurrences(result, session)
        return result

    def _code_history(self, result: dict, session: Any) -> dict:
        from ...cli.indexed import _event_patch

        def location(reference):
            if reference is None:
                return None
            return {key: reference[key] for key in ("commit", "path", "names", "span")}

        events = []
        for event in result["results"]:
            patch = _event_patch(session, event)
            if len(json.dumps(patch, ensure_ascii=True)) > self.page_chars:
                patch = {"patch_status": "omitted:source_patch_budget",
                         "omitted_patch_characters": len(patch.get("patch", ""))}
            events.append({
                "id": event["id"], "eid": event["eid"], "commit": event["commit"],
                "parent": event["parent"], "flags": event["flags"],
                "material": event["material"], "decision": event["decision"],
                "source": location(event["source"]), "destination": location(event["destination"]),
                **patch})
        return {**result, "results": events, "history_view": "verified_entity_code_changes",
                "source_guidance": (
                    "Patches compare exact indexed entity bytes at the recorded commits, not the whole PR. "
                    "An omitted/unavailable patch is not unchanged code; use source(path,line,commit) "
                    "or raw evidence. Metadata locations are not complete occurrence references.")}

    def entities(self, path: str, commit: str | None = None, cursor: str = "") -> dict:
        checked_path(path)
        return self._query("entities", self.commit(commit), {"path": path}, cursor)

    def id(self, token: str, commit: str | None = None, cursor: str = "") -> dict:
        return self._query("id", self.commit(commit), {"token": token}, cursor)

    def symbol_context(self, token: str, path: str = "", commit: str | None = None,
                       cursor: str = "", force_full: bool = False) -> dict:
        from ...index.query import QueryError
        require(type(token) is str and bool(token), "Context requires a nonempty token")
        if path:
            checked_path(path)
        commit = self.commit(commit)
        try:
            return self.named_source(token, path, commit, cursor, force_full)
        except QueryError as error:
            if error.code != "not_found":
                raise
        if path:
            try:
                result = self.named_source(token, "", commit, cursor, force_full)
                result["resolution"] = {
                    "requested_path_hint": path, "scope": "global_published_name_match",
                    "binding": "Lexical match outside the hint; verify imports/callers before assuming a binding."}
                return result
            except QueryError as error:
                if error.code != "not_found":
                    raise
        require(path and path in self.members(commit),
                "No indexed match and no exact member path for raw context; use search")
        require(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", token) is not None,
                "Raw context requires a simple identifier; use search for other text")
        lines = source_lines(self.blob(path, commit))
        pattern = re.compile(r"(?<![A-Za-z_0-9])" + re.escape(token) + r"(?![A-Za-z_0-9])")
        matches = [i for i, text in enumerate(lines, 1) if pattern.search(text)]
        require(bool(matches), "No indexed or literal source match; use search")
        start, end = max(1, matches[0] - 15), min(len(lines), matches[0] + 25)
        text = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
        result = self.page(text, "raw_symbol_context", {
            "token": token, "path": path, "commit": commit}, cursor)
        result.update(
            source_location={"path": path, "start_line": start, "end_line": end},
            provenance={**self.provenance(commit), "source": "raw_literal_context_after_index_miss"},
            resolution={"index_status": "not_found_not_absence", "kind": "literal_references_not_definitions",
                        "matching_lines": matches[:100], "matching_lines_omitted": max(0, len(matches) - 100)})
        result["coverage"].update(exact_git_lines=True, indexed_entity=False,
                                  complete_entity=False, other_matching_context_not_shown=(
                                      result["truncated"] or any(i < start or i > end for i in matches)))
        return result

    def named_source(self, token: str, path: str = "", commit: str | None = None,
                     cursor: str = "", force_full: bool = False) -> dict:
        from ...index.query import QueryError
        from ...mcp.tools import DEFAULT_BUDGET, source_tool
        require(type(token) is str and bool(token), "Named source requires a nonempty token")
        require(type(force_full) is bool, "force_full must be boolean")
        if path:
            checked_path(path)
        commit = self.commit(commit)
        session = self._index(commit)
        locator = token
        if path and not token.startswith(("eid1_", "oeid1_", path + "::")):
            selector = path
            if path not in self.members(commit) and any(
                    member.startswith(path + "/") for member in self.members(commit)):
                selector += "/"
            locator = selector + "::" + token
        response = source_tool(str(self.repo), entity=locator, at=commit,
                               scope=session.scope, generation=session.generation,
                               budget=(4 * self.max_response_chars
                                       if self.max_response_chars > MAX_RESPONSE_CHARS else DEFAULT_BUDGET))
        if response.get("error"):
            error = response["error"]
            if error["code"] == "ambiguous":
                matches = session.resolve(locator, at=GitOid.from_hex("sha1", commit), limit=10)
                require_integrity(matches["status"] == "ambiguous" and matches["page"]["total"] > 1,
                                  "Public/native source ambiguity disagrees")
                self._occurrences(matches["results"], session)
                candidates = [{"eid": row["eid"], "path": row["occurrence"]["path"],
                               "names": row["occurrence"]["names"], "span": row["occurrence"]["span"]}
                              for row in matches["results"] if row["occurrence"] is not None]
                return {"status": "ambiguous", "candidates": candidates,
                        "text": "No source selected. Choose a candidate EID with source(token), "
                                "or narrow the locator/path. These are lexical matches, not resolved calls.",
                        "next_cursor": None,
                        "coverage": {"source": "indexed_candidates_only", "complete_entity": False,
                                     "total_matches": matches["page"]["total"], "absence_claim": False},
                        "omitted": {"count": matches["page"]["total"] - len(candidates),
                                    "count_known": True, "reason": "candidate_limit_or_non_live"}}
            if error["code"] in ("corrupt", "digest_collision", "verification_failed",
                                 "invariant_failure", "invalid_record", "invalid_object", "invalid_source"):
                raise IntegrityError("Named source adapter refused invalid indexed evidence")
            if error["code"] == "not_found":
                raise QueryError("not_found", "No indexed name/locator match; coverage is not absence. "
                                 "Use source(path,line) or common search/read_file.")
            raise QueryError(error["code"], error["message"]
                             + "; use source(path,line) or common search/read_file for raw fallback")
        require_integrity(response["generation"] == session.generation
                          and len(response["results"]) == 1, "Named source generation/result changed")
        payload = response["results"][0]
        reference = payload["occurrence"]
        self._occurrences(reference, session)
        actual_path = reference["path"]
        require_integrity(reference["commit"]["hex"] == commit, "Named source revision changed")
        require(not path or actual_path == path or actual_path.startswith(path + "/"),
                "Named source does not match the requested path scope")
        require(payload["encoding"] == "utf-8",
                "Named source is non-UTF8; inspect explicit raw evidence instead")
        raw = payload["content"].encode("utf8", "strict")
        blob = self.blob(actual_path, commit)
        span = reference["span"]
        require_integrity(raw == blob[span["start_byte"]:span["end_byte"]]
                          and payload["byte_length"] == len(raw), "Named source bytes changed")
        lines = source_lines(blob)
        start, end = span["start_line"], span["end_line"]
        text = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1)) if lines else ""
        result = self.page(text, "named_source", {
            "token": token, "path": path, "commit": commit, "occurrence": reference["id"]}, cursor)
        result.update(entity={"occurrence": reference["id"], "names": reference["names"],
                              "path": actual_path, "span": span},
                      provenance={**self.provenance(commit), "generation": session.generation,
                                  "identity_context": session.context.id,
                                  "source": "public_named_source_verified_against_git"},
                      window={"start_line": start, "end_line": end, "whole_entity": True},
                      history_available=self.config.get("history_ready", False))
        result["coverage"].update(
            source="indexed", exact_git_lines=True,
            complete_entity=not cursor and not result["truncated"],
            lookup="unique_name_locator_or_eid", ambiguity="refused_not_guessed")
        if not lines:
            require_integrity(not raw and span["start_byte"] == span["end_byte"] == 0,
                              "Empty indexed source disagrees with its source span")
            result["file_lines"] = 0
            result["coverage"]["empty_source"] = True
        return result

    def entity_context(self, path: str, line: int, commit: str | None = None,
                       cursor: str = "", force_full: bool = False) -> dict:
        require(type(force_full) is bool, "force_full must be boolean")
        checked_path(path)
        commit = self.commit(commit)
        require(type(line) is int and line >= 1, "Entity line must be a positive integer")
        session = self._index(commit)
        selected = GitOid.from_hex("sha1", commit)
        candidates, native_cursor = [], None
        while True:
            page = session.entities_in(path.encode("utf8"), at=selected,
                                       limit=1000, cursor=native_cursor)
            require_integrity(type(page.get("results")) is list, "Malformed entity context page")
            for ref in page["results"]:
                self._occurrences(ref, session)
                span = ref["span"]
                if span["start_line"] <= line <= span["end_line"]:
                    candidates.append(ref)
            native_cursor = page["page"]["next_cursor"]
            if not native_cursor:
                break
        require(bool(candidates),
                "No indexed entity at this line; inspect coverage and use raw read_file")
        named = [ref for ref in candidates if ref["names"] != ["<module>"]]
        choices = named or candidates
        bounded = [ref for ref in choices
                   if ref["span"]["end_byte"] - ref["span"]["start_byte"] <= 4000]
        reference = (max(bounded, key=lambda ref: (
            ref["span"]["end_byte"] - ref["span"]["start_byte"], ref["id"])) if bounded else
            min(choices, key=lambda ref: (
                ref["span"]["end_byte"] - ref["span"]["start_byte"], ref["id"])))
        source = session.source(reference)
        text = source.decode("utf8", "strict")
        window = None
        if self.config.get("integrated_source"):
            span = reference["span"]
            start, end = span["start_line"], span["end_line"]
            if len(source) > 4000:
                start, end = max(start, line - 20), min(end, line + 30)
            lines = source_lines(self.blob(path, commit))
            text = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
            window = {"start_line": start, "end_line": end,
                      "whole_entity": start == span["start_line"] and end == span["end_line"]}
        result = self.page(text, "entity_context", {
            "path": path, "line": line, "commit": commit, "occurrence": reference["id"]}, cursor)
        result.update(entity={"occurrence": reference["id"], "names": reference["names"],
                              "path": path, "span": reference["span"]},
                      provenance={**self.provenance(commit), "generation": session.generation,
                                  "identity_context": session.context.id,
                                  "source": "indexed_verified_against_git"},
                      history_available=self.config.get("history_ready", False))
        result["coverage"].update(source="indexed", exact_entity_source=True,
                                  complete_entity=not cursor and not result["truncated"])
        if window is not None:
            result["window"] = window
            result["coverage"].update(
                complete_entity=window["whole_entity"] and not cursor and not result["truncated"],
                exact_entity_source=False, exact_git_lines=True,
                outside_window="Not returned; use explicit source lines for more context")
        return result

    def entity_diff(self, token: str, path: str = "", cursor: str = "") -> dict:
        require(self.config.get("history_ready") is True, "Connected indexed history required")
        if path:
            checked_path(path)
        session = self._index(self.head)
        require(session is self._index(self.base), "Endpoint generations differ")
        matches, native_cursor = [], None
        while True:
            page = session.resolve(token, at=GitOid.from_hex("sha1", self.head),
                                   historical=False, limit=1000, cursor=native_cursor)
            self._occurrences(page, session)
            matches.extend(r for r in page["results"]
                           if r.get("occurrence") and (not path or r["occurrence"]["path"] == path))
            native_cursor = page["page"]["next_cursor"]
            if not native_cursor:
                break
        require(len(matches) == 1, "Entity name missing or ambiguous; narrow the HEAD path or use id")
        eid = matches[0]["eid"]
        states = [session.state_at(eid, at=GitOid.from_hex("sha1", commit))
                  for commit in (self.base, self.head)]
        for state in states:
            self._occurrences(state, session)
            require_integrity(state["eid"] == eid, "Returned endpoint identity mismatch")
            require(state["state"] == "present" and state.get("occurrence"),
                    "Entity lacks qualified present state at both endpoints; use raw_diff")
        before, after = [state["occurrence"] for state in states]
        require_integrity(after["id"] == matches[0]["occurrence"]["id"],
                          "Resolved entity differs from endpoint state")
        texts = [session.source(ref).decode("utf8", "strict").splitlines(keepends=True)
                 for ref in (before, after)]
        pieces = difflib.unified_diff(
            *texts, fromfile=f"{before['path']}:{before['span']['start_line']}@{self.base}",
            tofile=f"{after['path']}:{after['span']['start_line']}@{self.head}")
        patch = "".join(line if line.endswith("\n") else
                        line + "\n\\ No newline at end of entity\n" for line in pieces)
        return {**self.page(patch, "entity_diff", {"eid": eid, "path": path}, cursor),
                "eid": eid, "before": {"path": before["path"], "span": before["span"]},
                "after": {"path": after["path"], "span": after["span"]},
                "provenance": {**self.provenance(), "generation": session.generation,
                               "identity_context": session.context.id,
                               "source": "persistent_entity_exact_endpoint_source"},
                "scope": "one qualified entity; not a complete PR diff"}

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
        if len(source) > self.page_chars:
            raise EvidenceBudgetError("Exact indexed source exceeds source-output cap")
        return {"text": source.decode("utf8", "strict"), "reference": reference,
                "provenance": {**self.provenance(commit), "generation": session.generation,
                               "identity_context": session.context.id},
                "coverage": {"exact_span": True}, "omitted": {"bytes": 0}, "next_cursor": None}

    def _prepare_source_delivery(self, name: str, arguments: dict, result: dict):
        if (name not in ("read_file", "entity_context")
                or not self.config.get("integrated_source") or self.arm != "H"
                or result.get("truncated") or arguments.get("cursor")
                or result.get("status") == "ambiguous"
                or result.get("coverage", {}).get("lossy_decoding")):
            return None
        commit = self.commit(arguments.get("commit"))
        path = ((result.get("entity", {}).get("path") or result.get("source_location", {}).get("path"))
                if name == "entity_context"
                else arguments.get("path"))
        require_integrity(type(path) is str and bool(path), "Reusable source path missing")
        lines = source_lines(self.blob(path, commit))
        if result.get("text") == "":
            require_integrity(not lines and result.get("file_lines") == 0
                              and result.get("coverage", {}).get("empty_source") is True,
                              "Empty reusable response disagrees with committed source")
            return None
        numbered = {}
        for displayed in result["text"].split("\n"):
            number, separator, text = displayed.partition(": ")
            require_integrity(bool(separator) and number.isdecimal(), "Unnumbered reusable source")
            index = int(number)
            require_integrity(1 <= index <= len(lines) and lines[index - 1] == text,
                              "Reusable source differs from exact Git line")
            numbered[index] = text
        if not numbered:
            return None
        key = SourceKey(commit, path, self.members(commit)[path][1])
        start, end = min(numbered), max(numbered)
        delivery = self.source_delivery.render(
            key, numbered, start=start, end=end, force_full=arguments.get("force_full", False))
        receipt = f"evidence-call-{self.calls}"
        if delivery.previously_delivered_ranges:
            result["text"] = "\n".join(f"{line.number}: {line.text}" for line in delivery.new_lines)
            if not result["text"]:
                result["text"] = "Already delivered; see receipts. force_full=true repeats."
            result["previously_delivered"] = [
                {"start_line": item.start, "end_line": item.end, "receipt": item.receipt_id}
                for item in delivery.previously_delivered_ranges]
            result["coverage"].update(reused_source_lines=sum(
                item.end - item.start + 1 for item in delivery.previously_delivered_ranges),
                newly_delivered_lines=len(delivery.new_lines),
                complete_entity=False, source_available_across_receipts=True)
            result["omitted"]["already_delivered_lines"] = result["coverage"]["reused_source_lines"]
        result["provenance"] = {key: value for key, value in result["provenance"].items()
                                if key in ("commit", "generation", "identity_context", "source")}
        result["coverage"]["shown_characters"] = len(result["text"])
        result["source_receipt"] = receipt
        return key, {line.number: line.text for line in delivery.new_lines}, receipt

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
            effective_name, effective_arguments = (
                self.continuations[arguments["cursor"]] if name == "continue_evidence" else
                (name, arguments))
            # Continuations never claim partial source pages as complete reusable lines.
            source_name = ("entity_context" if name == "gita_review"
                           and arguments["operation"] in ("source", "context") else name)
            pending = (None if name == "continue_evidence" else
                       self._prepare_source_delivery(source_name, arguments, result))
            if not self.fits_response(result):
                raise EvidenceBudgetError(
                    "Evidence exceeds safe SDK output budget; narrow the request or use raw source lines")
            if pending is not None:
                key, lines, receipt = pending
                self.source_delivery.register_completed(key, lines, receipt_id=receipt)
            if result.get("next_cursor"):
                self.continuations[result["next_cursor"]] = (effective_name, {
                    key: value for key, value in effective_arguments.items() if key != "cursor"})
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
        return [Tool(**tool) for tool in tool_inventory(config)]

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
