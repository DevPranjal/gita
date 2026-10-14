"""Explicit indexed commands, snapshot ownership, and bounded CLI envelopes."""

from __future__ import annotations

import base64
import difflib
import io
import json
import re
import sqlite3
import sys
from typing import Any

from ..context.tokens import count_tokens
from ..patches import _absolute
from ..index import builder, query, store as storage
from ..index.model import GitOid, IndexContractError
from ..index.store import IndexStore, IndexStoreError
from ..vcs.git import CommittedRepo, GitError, GitObjectError
from . import render


_VALUE_FLAGS = {
    "-C", "--repo", "--budget", "--scope", "--ref", "--since", "--until",
    "--at", "--generation", "--cursor", "--order", "--limit", "--base", "--head",
    "--workers", "--parse-timeout",
    # Consumed so free text like `--intent "fix the parser"` can never have its
    # value mistaken for a subcommand name.
    "--intent", "--filter",
}


def _arguments(argv):
    arguments = iter(argv)
    for argument in arguments:
        if argument == "--":
            break
        flag, separator, value = argument.partition("=")
        if flag in _VALUE_FLAGS:
            yield flag, value if separator else next(arguments, None)
        elif argument.startswith("-C") and argument != "-C":
            yield "-C", argument[2:]
        else:
            yield flag, value if separator else None


def intent(argv) -> str | None:
    command = None
    legacy = False
    for flag, unused_value in _arguments(argv):
        if command is None and not flag.startswith("-"):
            command = "history" if flag == "katha" else flag
        elif flag == "--legacy":
            legacy = True
    if command in ("index", "id") or command == "history" and not legacy:
        return command
    return None


def _envelope(command):
    return {
        "schema_version": 1, "command": command, "status": "unknown",
        "scope": None, "generation": None,
        "revision": {"requested": None, "resolved": None, "pinned": None},
        "boundary": None, "policy": None,
        "refs": {"input": None, "pinned": None, "current": None},
        "freshness": {"status": "unchecked"},
        "coverage": {"status": "unknown", "working_tree": "out_of_scope"},
        "results": [],
        "page": {"shown": 0, "total": None, "next_cursor": None,
                 "truncated": False, "reason": None},
        "omitted": {"count": None, "count_known": False}, "error": None,
    }


def _failure(payload, code, message, *, status=None):
    recovery = "Correct the arguments and retry."
    if code in {"not_built", "parse_mismatch", "match_mismatch", "schema_mismatch", "rebuild_required"}:
        recovery = "Run gita index for this scope; use --rebuild for an incompatible index."
    elif code == "generation_gone":
        recovery = "Restart the query against a retained generation."
    elif code in {"corrupt", "digest_collision", "verification_failed", "invariant_failure", "invalid_record", "invalid_object"}:
        recovery = "Preserve the index for diagnosis; do not overwrite it."
    elif code in {"not_found", "not_live", "ambiguous", "identity_outside_scope"}:
        recovery = "Select an exact full EID, revision, or scope; inspect historical matches."
    elif code == "budget_too_small":
        recovery = "Increase --budget."
    elif code == "verification_unavailable":
        recovery = "Verification requires the not-yet-implemented F4 verifier."
    elif code in {"busy", "resource_limit", "permission_denied", "io_error", "source_unavailable",
                  "invalid_source", "generation_in_use", "git_timeout", "git_command_failed"}:
        recovery = "Resolve the reported resource, permission, or retention condition and retry."
    payload["status"] = status or code
    payload["error"] = {
        "code": code, "message": message, "retryable": code in {"busy", "git_timeout"},
        "recovery": recovery,
    }
    return payload


def _exit(code):
    if code in {"not_found", "not_live", "ambiguous", "identity_outside_scope"}:
        return 1
    if code in {"not_built", "parse_mismatch", "match_mismatch", "schema_mismatch",
                "rebuild_required", "generation_gone", "verification_unavailable"}:
        return 3
    if code in {"corrupt", "digest_collision", "verification_failed", "invariant_failure", "invalid_record", "invalid_object"}:
        return 5
    if code == "budget_too_small":
        return 6
    if code in {"busy", "resource_limit", "permission_denied", "io_error", "source_unavailable",
                "invalid_source", "generation_in_use", "git_timeout", "git_command_failed",
                "cleanup_failed", "object_unavailable", "object_missing", "object_read_failed",
                "invalid_metadata", "record_too_large"}:
        return 4
    return 2


def _text(payload):
    lines = [f"{payload['command']}: {payload['status']}"]
    if payload["scope"] is not None:
        lines.append(f"scope {payload['scope']['name']} {payload['scope']['id']} generation {payload['generation']}")
    coverage = payload["coverage"] or {}
    lines.append(f"coverage {coverage.get('status', 'unknown')}; working tree out_of_scope")
    if payload["error"] is not None:
        error = payload["error"]
        lines.append(f"{error['code']}: {error['message']} {error['recovery']}")
    for result in payload["results"]:
        if "eid" not in result:
            lines.append(" ".join(f"{key}={value}" for key, value in result.items()))
            continue
        details = [result["eid"]]
        if "commit" in result:
            details.extend((result["commit"]["hex"], ",".join(result.get("flags", []))))
        else:
            details.append(result.get("lifecycle", "unknown"))
        for name in ("source", "destination", "occurrence", "last_known_occurrence"):
            occurrence = result.get(name)
            if occurrence is not None:
                path = occurrence.get("path") or "base64:" + occurrence["path_bytes_b64"]
                details.append(f"{name} {path}::{'::'.join(occurrence['names'])}")
        lines.append(" ".join(details))
        if "patch" in result:
            lines.append(result["patch"].rstrip("\n"))
        elif result.get("patch_status") == "omitted_budget":
            lines.append("patch omitted: budget; increase --budget or retrieve source/destination with gita_source")
        elif result.get("patch_status", "").startswith("unavailable:"):
            lines.append(f"patch {result['patch_status']}; source/destination references retained")
        elif result.get("patch_status") == "not_applicable":
            lines.append("patch: no proven before/after transition")
        elif result.get("patch_status") == "unchanged":
            lines.append("patch: source bytes unchanged")
    page = payload["page"]
    lines.append(f"shown {page['shown']} total {page['total']} omitted {payload['omitted']['count']}")
    if page.get("truncated"):
        lines.append(f"truncated: {page['reason']}")
    if page["next_cursor"]:
        lines.append(f"cursor {page['next_cursor']}")
    return "\n".join(lines)


def _serialized(payload, as_json):
    text = (json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
            if as_json else _text(payload))
    if not as_json:
        text = text.encode("ascii", errors="backslashreplace").decode("ascii")
    return text + "\n"


def _budget_failure(out, command, as_json, budget):
    payload = _failure(_envelope(command), "budget_too_small", "Budget cannot hold a complete answer.")
    text = _serialized(payload, as_json)
    if count_tokens(text) <= budget:
        render.write(out, text[:-1])
    else:
        render.write(sys.stderr, "gita: budget too small for a complete indexed response; increase --budget")
    return 6


def _emit(out, payload, as_json, budget):
    text = _serialized(payload, as_json)
    if count_tokens(text) > budget:
        return _budget_failure(out, payload["command"], as_json, budget)
    render.write(out, text[:-1])
    return 0 if payload["error"] is None else _exit(payload["error"]["code"])


def syntax_error(out, command, argv, message, default_budget):
    budget, as_json = default_budget, False
    for flag, value in _arguments(argv):
        if flag == "--json":
            as_json = True
        elif flag == "--budget":
            try:
                budget = int(value)
            except (TypeError, ValueError):
                pass
    payload = _failure(_envelope(command), "invalid_options", message)
    return _emit(out, payload, as_json, max(0, budget))


def _validate(command, args):
    if args.budget < 0:
        raise query.QueryError("invalid_budget", "Budget must be nonnegative")
    if args.scope is not None and (not args.scope or "\0" in args.scope):
        raise query.QueryError("invalid_scope", "Select a nonempty scope name")
    if args.scope is not None:
        try:
            args.scope.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise query.QueryError("invalid_scope", "Scope must be strict UTF-8") from error
    if args.generation is not None and re.fullmatch(r"gen1_[0-9a-f]{32}", args.generation) is None:
        raise query.QueryError("invalid_generation", "Expected a full generation ID")
    if command == "index":
        diagnostic = args.status or args.verify or args.gc
        if diagnostic and (args.workers is not None or args.parse_timeout is not None):
            raise query.QueryError("invalid_options", "--workers and --parse-timeout are build-only")
        _build_limits(args)
        if diagnostic and (args.ref is not None or args.since is not None or args.rebuild):
            raise query.QueryError("invalid_options", "--ref, --since and --rebuild are build-only")
        if args.source_check and not args.verify:
            raise query.QueryError("invalid_options", "--source-check requires --verify")
        if args.gc and args.generation is None:
            raise query.QueryError("invalid_options", "--gc requires --generation")
        if args.generation is not None and not (args.gc or args.verify):
            raise query.QueryError("invalid_options", "--generation requires --gc or --verify")
    else:
        query.IndexSession._limit(args.limit)
        if command == "id" and not args.entity:
            raise query.QueryError("invalid_query", "An entity token is required")
        if command == "history":
            if not args.entity:
                if not (args.since or args.base) or not (args.until or args.head):
                    raise query.QueryError("invalid_query", "Select an entity or two explicit commits")
                if args.at is not None or args.order not in (None, "oldest"):
                    raise query.QueryError("invalid_options", "Range history uses chronological order and rejects --at")
            elif any(getattr(args, name) is not None for name in ("base", "head", "since", "until")):
                raise query.QueryError("invalid_options", "Indexed entity history rejects legacy revisions; use --at")


def _build_limits(args):
    options = {}
    if args.workers is not None:
        options["workers"] = args.workers
    if args.parse_timeout is not None:
        options["parse_timeout_seconds"] = args.parse_timeout
    return builder.BuildLimits(**options)


def _cursor(args, command):
    cursor = args.cursor
    if cursor is None:
        return None
    try:
        if not 0 < len(cursor) <= 4096 or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
            raise ValueError("Invalid cursor encoding")
        fields = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (type(fields) is not dict or set(fields) != {
            "version", "scope", "generation", "context", "action", "filter", "at", "order", "last",
        } or type(fields["version"]) is not int or fields["version"] != 1
            or query.IndexSession._encode_cursor(fields) != cursor):
            raise ValueError("Invalid cursor fields")
        for name, pattern in (("generation", r"gen1_[0-9a-f]{32}"), ("context", r"ctx1_[0-9a-f]{64}")):
            if type(fields[name]) is not str or re.fullmatch(pattern, fields[name]) is None:
                raise ValueError("Invalid cursor identity")
        if (fields["scope"] != (args.scope if args.scope is not None else "default")
                or type(fields["filter"]) is not dict or type(fields["last"]) is not list
                or fields["action"] not in (("resolve",) if command == "id" else
                                            (("resolve", "history") if args.entity else ("events_in",)))
                or args.generation is not None and fields["generation"] != args.generation):
            raise ValueError("Cursor does not select this query")
        if fields["action"] == "events_in":
            if type(fields["at"]) is not list or len(fields["at"]) != 2:
                raise ValueError("Invalid range endpoints")
            for endpoint in fields["at"]:
                query._json_oid(endpoint)
        else:
            query._json_oid(fields["at"])
        return fields
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError, query.QueryError) as error:
        raise query.QueryError("invalid_cursor", "Malformed or mismatched continuation cursor") from error


def _oid(oid):
    return None if oid is None else {"object_format": oid.object_format, "hex": oid.digest.hex()}


def _provenance(payload, record, context):
    payload.update(
        scope={"name": record["scope"], "id": record["scope_id"], "first_parent": True},
        generation=record["id"],
        boundary={"kind": context.boundary, "anchor": _oid(context.anchor),
                  "parents": [_oid(parent) for parent in context.parents]},
        policy={"parse_fingerprint": context.parse_fingerprint.hex(),
                "match_fingerprint": context.match_fingerprint.hex(), "context": context.id},
    )
    payload["revision"]["pinned"] = _oid(record["tip"])
    payload["refs"]["pinned"] = _oid(record["tip"])


def _metadata(payload, session, repository, at=None, input_ref=None):
    with session._reading():
        record = session._store.generation(session.generation)
        _provenance(payload, record, session.context)
        selected, sequence = session._at(at)
        payload["revision"].update(requested=None if at is None else _oid(selected), resolved=_oid(selected))
        payload["coverage"] = session._coverage(sequence)
    if input_ref is not None:
        payload["refs"]["input"] = input_ref
        try:
            current = repository.resolve(input_ref)
        except (GitObjectError, OSError):
            pass
        else:
            payload["refs"]["current"] = _oid(current)
            payload["freshness"]["status"] = "current" if current == session.tip else "behind"
    return selected


def _page_payload(base, page, command, reason):
    payload = {**base, **page}
    payload["page"] = {**page["page"], "truncated": page["omitted"]["count"] not in (None, 0),
                       "reason": reason if page["omitted"]["count"] not in (None, 0) else None}
    if page["status"] in ("not_found", "ambiguous"):
        code = page["status"]
        _failure(payload, code, "No matching identity." if code == "not_found" else "More than one identity matches.")
    elif command == "id" and page["status"] == "unique" and any(
        result["lifecycle"] in ("absent", "removed") for result in page["results"]
    ):
        _failure(payload, "not_live", "The identity is not live at this revision.", status=page["status"])
    return payload


def _event_patch(session, event):
    source, destination = event["source"], event["destination"]
    if ((source is None and "added" not in event["flags"])
            or (destination is None and "removed" not in event["flags"])):
        return {"patch_status": "not_applicable"}
    before = b"" if source is None else session.source(source)
    after = b"" if destination is None else session.source(destination)
    if before == after:
        return {"patch_status": "unchanged"}
    try:
        old_lines = before.decode("utf-8", errors="strict").splitlines(keepends=True)
        new_lines = after.decode("utf-8", errors="strict").splitlines(keepends=True)
    except UnicodeDecodeError:
        return {"patch_status": "unavailable:non_utf8_source"}

    def label(occurrence, side):
        if occurrence is None:
            return "/dev/null"
        path = occurrence["path"] or "base64:" + occurrence["path_bytes_b64"]
        return f"{side}/{path}::{'::'.join(occurrence['names'])}"

    lines = difflib.unified_diff(old_lines, new_lines, fromfile=label(source, "a"),
                                 tofile=label(destination, "b"))
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                    for line in lines)
    patch = _absolute(patch, 0 if source is None else source["span"]["start_line"] - 1,
                      0 if destination is None else destination["span"]["start_line"] - 1)
    return {"patch_status": "included", "patch": patch}


def _history_patches(payload, session, args):
    for position, event in enumerate(payload["results"]):
        if "patch_status" not in event:
            continue
        try:
            details = _event_patch(session, event)
        except query.QueryError as error:
            if error.code != "source_unavailable":
                raise
            details = {"patch_status": f"unavailable:{error.code}"}
        payload["results"][position] = {**event, **details}
        if count_tokens(_serialized(payload, args.as_json)) > args.budget:
            payload["results"][position] = event
    return payload


def _emit_page(out, base, fetch, args, *, session=None):
    patches = session is not None and base["command"] == "history" and not args.brief

    def payload_for(limit, reason):
        payload = _page_payload(base, fetch(limit), base["command"], reason)
        if patches:
            payload["results"] = [
                {**result, "patch_status": "omitted_budget"} if "flags" in result else result
                for result in payload["results"]
            ]
        return payload

    def emit(payload):
        if patches:
            _history_patches(payload, session, args)
        return _emit(out, payload, args.as_json, args.budget)

    if count_tokens(_serialized(base, args.as_json)) > args.budget:
        return _budget_failure(out, base["command"], args.as_json, args.budget)
    payload = payload_for(args.limit, "limit")
    if count_tokens(_serialized(payload, args.as_json)) <= args.budget:
        return emit(payload)
    lower, upper = 1, min(args.limit - 1, len(payload["results"]) - 1)
    best = None
    while lower <= upper:
        limit = (lower + upper) // 2
        candidate = payload_for(limit, "budget")
        if count_tokens(_serialized(candidate, args.as_json)) <= args.budget:
            best, lower = candidate, limit + 1
        else:
            upper = limit - 1
    if best is None:
        return _budget_failure(out, base["command"], args.as_json, args.budget)
    return emit(best)


def _queries(command, out, repository, args, payload):
    fields = _cursor(args, command)
    generation = args.generation if fields is None else fields["generation"]
    if command == "history" and not args.entity:
        scope = args.scope if args.scope is not None else "default"
        try:
            base = repository.resolve(args.since or args.base)
            head = repository.resolve(args.until or args.head)
        except GitObjectError as error:
            raise query.QueryError("invalid_cursor" if fields else "invalid_revision", "Range endpoints must resolve to commits") from error
        endpoints = [_oid(base), _oid(head)]
        if fields is not None and fields["at"] != endpoints:
            raise query.QueryError("invalid_cursor", "Range differs from the cursor; use the returned pinned range")
        with query.open_index(repository, scope, generation) as session:
            _metadata(payload, session, repository, head)
            payload["range"] = {"base": endpoints[0], "head": endpoints[1]}
            def fetch(limit):
                return session.events_in(base, head, limit=limit, cursor=args.cursor)
            return _emit_page(out, payload, fetch, args, session=session)
    at = args.at
    if fields is not None:
        if at is None:
            at = query._json_oid(fields["at"])
        else:
            try:
                at = repository.resolve(at)
            except GitObjectError as error:
                raise query.QueryError("invalid_cursor", "Cursor revision is unavailable") from error
            if _oid(at) != fields["at"]:
                raise query.QueryError("invalid_cursor", "Cursor revision differs from --at")
    scope = args.scope if args.scope is not None else "default"
    with query.open_index(repository, scope, generation) as session:
        try:
            selected = _metadata(payload, session, repository, at)
        except query.QueryError as error:
            if fields is not None and error.code == "outside_scope":
                raise query.QueryError("invalid_cursor", "Cursor revision is outside its generation") from error
            raise
        if command == "id":
            def fetch(limit):
                return session.resolve(args.entity, at=selected, historical=args.historical,
                                       cursor=args.cursor, limit=limit)
        else:
            resolving = fields is not None and fields["action"] == "resolve"
            candidates = session.resolve(args.entity, at=selected, limit=args.limit,
                                         cursor=args.cursor if resolving else None)
            if candidates["status"] != "unique":
                if fields is not None and not resolving:
                    raise query.QueryError("invalid_cursor", "Cursor identity no longer resolves in this query")
                def fetch(limit):
                    return session.resolve(args.entity, at=selected, limit=limit,
                                           cursor=args.cursor if resolving else None)
            else:
                if resolving:
                    raise query.QueryError("invalid_cursor", "A resolution cursor cannot page event history")
                eid = candidates["results"][0]["eid"]
                def fetch(limit):
                    return session.history(eid, at=selected, order=args.order or "newest",
                                           limit=limit, cursor=args.cursor)
        return _emit_page(out, payload, fetch, args, session=session)


def _index(out, repository, repo, args, payload):
    scope = args.scope if args.scope is not None else "default"
    if args.gc:
        with IndexStore.open(IndexStore.locate(repo), read_only=False) as store:
            with store.transaction():
                record = store.generation(args.generation)
                if record["scope"] != scope:
                    raise query.QueryError("outside_scope", "Generation belongs to a different scope")
                _provenance(payload, record, store._context(record["context_id"]))
                store.collect_generation(args.generation)
        payload["status"] = "collected"
        payload["results"] = [{"collected": args.generation}]
    elif args.status or args.verify:
        try:
            with query.open_index(repository, scope, args.generation) as session:
                _metadata(payload, session, repository, input_ref="HEAD")
                payload["status"] = "partial" if payload["coverage"]["status"] == "partial" else "ready"
        except query.QueryError as error:
            if not args.status or error.code != "not_built":
                raise
            payload["status"] = "not_built"
        if args.verify:
            _failure(payload, "verification_unavailable", "Indexed verification is not implemented.", status="unverified")
    else:
        ref = "HEAD" if args.ref is None else args.ref
        try:
            pinned = repository.resolve(ref)
        except GitObjectError as error:
            if error.code in ("git_command_failed", "invalid_revision", "object_missing"):
                raise query.QueryError("invalid_revision", "--ref must resolve to an available commit") from error
            raise
        since = args.since
        if since is not None:
            try:
                since = repository.resolve(since).digest.hex()
            except GitObjectError as error:
                raise query.QueryError("boundary_unavailable", "Cannot resolve the explicit committed boundary") from error
        path = IndexStore.locate(repo)
        if path.exists():
            store = IndexStore.open(path, read_only=False)
        else:
            if since is not None and since != pinned.digest.hex():
                boundary = GitOid.from_hex(pinned.object_format, since)
                shallow = repository.shallow_boundaries()
                current = pinned
                while current != boundary:
                    if current in shallow:
                        raise query.QueryError("boundary_unavailable", "Requested boundary is outside shallow history")
                    try:
                        committed = repository.commit(current)
                    except GitObjectError as error:
                        if error.code != "object_missing":
                            raise
                        raise query.QueryError("boundary_unavailable", "First-parent history is unavailable locally") from error
                    if not committed.parents:
                        raise query.QueryError("boundary_unavailable", "Requested boundary is not reachable on the first-parent chain")
                    current = committed.parents[0]
            try:
                store = IndexStore.create(path)
            except IndexStoreError as error:
                if error.code != "exists":
                    raise
                store = IndexStore.open(path, read_only=False)
        with store:
            result = builder.build(repository, store, scope=scope, ref=pinned.digest.hex(),
                                   since=since, rebuild=args.rebuild, limits=_build_limits(args))
        with query.open_index(repository, scope, result.generation) as session:
            _metadata(payload, session, repository, input_ref=ref)
        payload["revision"]["requested"] = _oid(pinned)
        payload["status"] = result.status
        payload["results"] = [{"commits": result.commits, "parsed_files": result.parsed_files,
                               "cached_files": result.cached_files}]
    count = len(payload["results"])
    payload["page"].update(shown=count, total=count)
    payload["omitted"].update(count=0, count_known=True)
    return _emit(out, payload, args.as_json, args.budget)


def handle(command, out, repo, args):
    payload: dict[str, Any] = _envelope(command)
    pending = io.StringIO()
    try:
        _validate(command, args)
        minimum = _failure(_envelope(command), "budget_too_small", "Budget cannot hold a complete answer.")
        if count_tokens(_serialized(minimum, args.as_json)) > args.budget:
            code = _budget_failure(pending, command, args.as_json, args.budget)
        else:
            with CommittedRepo(repo.root) as repository:
                if command == "index":
                    code = _index(pending, repository, repo, args, payload)
                else:
                    code = _queries(command, pending, repository, args, payload)
    except (builder.BuildError, query.QueryError, IndexStoreError, IndexContractError, GitObjectError) as error:
        _failure(payload, error.code, str(error))
    except sqlite3.Error as error:
        classified = storage._storage_error(error, None)
        _failure(payload, classified.code, str(classified))
    except (OSError, GitError) as error:
        _failure(payload, "permission_denied" if isinstance(error, PermissionError) else "io_error", str(error))
    else:
        text = pending.getvalue()
        pending.close()
        if text:
            render.write(out, text.removesuffix("\n"))
        return code
    pending.close()
    payload["results"] = []
    payload["page"] = {"shown": 0, "total": None, "next_cursor": None,
                       "truncated": False, "reason": None}
    payload["omitted"] = {"count": None, "count_known": False}
    return _emit(out, payload, args.as_json, max(0, args.budget))