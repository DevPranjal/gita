"""Tool implementations for the MCP server.

Kept free of the MCP SDK so the contract an agent depends on is testable without
a transport. Every result is navigable: it names the entities an agent can drill
into next, so progressive disclosure is discoverable rather than documented.

Each layer is a separate tool rather than a depth parameter on one tool, so that
an agent sees the cost of each step in the tool name it chooses.
"""

from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace
from typing import Any, Callable

from ..context import (
    build_view,
    count_tokens,
    entity_diff,
    expand,
    focus,
    focus_label,
)
from ..context.answer import DEFAULT_BUDGET as ANSWER_BUDGET
from ..context.answer import compose
from ..context.rank import Intent
from ..index.model import IndexContractError
from ..index.query import QueryError
from ..index.store import IndexStoreError
from ..revisions import diff_revisions
from ..telemetry import record, timed
from ..vcs.git import CommittedRepo, GitError, GitObjectError, Repo

DEFAULT_BASE = "HEAD^"
DEFAULT_HEAD = "HEAD"
DEFAULT_BUDGET = ANSWER_BUDGET

_MAX_SUGGESTIONS = 5


def _guard(fn: Callable[..., dict]) -> Callable[..., dict]:
    def wrapper(*args, **kwargs) -> dict:
        with timed() as elapsed:
            try:
                result = fn(*args, **kwargs)
            except (QueryError, IndexStoreError, IndexContractError, GitObjectError) as error:
                result = {"status": error.code, "error": {"code": error.code, "message": str(error)}}
            except GitError as error:
                result = {"error": str(error)}
            except (OSError, ValueError) as error:
                result = {"error": f"{type(error).__name__}: {error}"}

        error = result.get("error")
        record({
            "arm": "gita",
            "tool": f"gita_{fn.__name__.removesuffix('_tool')}",
            # MCP calls were logging no repo at all, so nothing in a report
            # could be grouped by repository.
            "repo": kwargs.get("repo_path") or (args[0] if args else None),
            "output_tokens": count_tokens(json.dumps(result, default=str)),
            "latency_ms": elapsed.ms,
            "ok": error is None,
            "error": str(error).split(":")[0] if error else None,
            "truncated": result.get("truncated"),
            "files": result.get("files_changed"),
        })
        return result

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _open(repo_path: str, *revs: str) -> Repo:
    repo = Repo(repo_path)
    for rev in revs:
        if not repo.resolve(rev):
            raise GitError(f"unknown revision '{rev}' in {repo_path}")
    return repo


def _suggestions(changeset) -> list[dict[str, str]]:
    """What an agent can usefully call next, cheapest first."""
    out: list[dict[str, str]] = []
    for change in changeset.material()[:_MAX_SUGGESTIONS]:
        entity = change.entity
        action = "gita_expand" if entity.is_container else "gita_show"
        out.append({"tool": action, "entity": entity.id})
    return out


@_guard
def diff_tool(repo: str, base: str = DEFAULT_BASE, head: str | None = DEFAULT_HEAD,
              budget: int = DEFAULT_BUDGET, filter: str = "",
              interface_only: bool = False, brief: bool = False,
              intent: str = "", patch: bool = False, indexed: bool = False,
              scope: str | None = None, generation: str | None = None) -> dict[str, Any]:
    """A complete answer in one call: summary, files, entities and the code.

    Pass the task you are working on as ``intent`` -- the entities it names are
    ranked first, and nothing is hidden by doing so.

    Returns `next` only when the budget forced something out; otherwise there is
    nothing left to drill into and a follow-up call would waste a whole turn.
    """
    if (scope is not None or generation is not None) and not indexed:
        raise ValueError("scope and generation require indexed=true")
    if indexed or patch:
        revisions = [base] + ([head] if head is not None else [])
        result = _command_result(repo, "diff", revisions, budget=budget, filter=filter,
                                 interface_only=interface_only, brief=brief, intent=intent,
                                 patch=patch, indexed=indexed, scope=scope, generation=generation)
        result["answer"] = result.get("text", result.get("patch", ""))
        return result
    repository = _open(repo, base, head)
    changeset = diff_revisions(repository, base, head)
    selected = focus(changeset, filter, interface_only)
    parsed = Intent.parse(intent)
    result = compose(repository, base, head, selected, budget=budget,
                     detail=not brief, intent=parsed)

    return {
        "base": base,
        "head": head,
        "answer": result.text,
        "tokens": result.tokens,
        "budget": budget,
        "truncated": result.truncated,
        "detailed": result.detailed,
        "intent_terms": list(parsed.terms),
        "files_changed": changeset.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
        "changes": [
            {"id": c.entity.id, "kind": c.kind.value,
             "interface": c.affects_interface}
            for c in selected.material()
        ],
        "next": _suggestions(selected) if result.truncated else [],
    }


@_guard
def expand_tool(repo: str, entity: str, base: str = DEFAULT_BASE,
                head: str = DEFAULT_HEAD,
                budget: int = DEFAULT_BUDGET,
                intent: str = "") -> dict[str, Any]:
    """Descendants of a rolled-up entity, still without paying for hunks."""
    repository = _open(repo, base, head)
    changeset = diff_revisions(repository, base, head)
    lines = expand(changeset, entity, budget=budget, intent=Intent.parse(intent))
    if not lines:
        return {"error": f"no nested changes under '{entity}'", "entity": entity}

    children = [c.entity.id for c in changeset.material()
                if c.entity.id.startswith(f"{entity}::")]
    return {
        "entity": entity,
        "lines": lines,
        "tokens": count_tokens("\n".join(lines)),
        "next": [{"tool": "gita_show", "entity": child}
                 for child in children[:_MAX_SUGGESTIONS]],
    }


@_guard
def show_tool(repo: str, entity: str, base: str = DEFAULT_BASE,
              head: str = DEFAULT_HEAD) -> dict[str, Any]:
    """L2: the exact hunks for one entity. The expensive layer."""
    repository = _open(repo, base, head)
    patch = entity_diff(repository, base, head, entity)
    if not patch:
        return {"error": f"entity '{entity}' not found in either revision",
                "entity": entity}
    return {"entity": entity, "patch": patch, "tokens": count_tokens(patch)}


def _response(out: io.StringIO, code: int) -> dict[str, Any]:
    text = out.getvalue()
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": text.strip(), "exit_code": code}
    return {"status": "budget_too_small", "error": {
        "code": "budget_too_small", "message": "Increase budget to hold a complete response.",
    }}


def _command_result(repo: str, command: str, positionals=(), **options) -> dict[str, Any]:
    from ..cli import _dispatch, _parser

    parser = _parser(indexed_errors=True)
    arguments = [command, "--json", "--no-color"]
    for name, value in options.items():
        if value is None or value is False:
            continue
        flag = "--" + name.replace("_", "-")
        arguments.append(flag if value is True else f"{flag}={value}")
    if positionals:
        arguments.extend(["--", *positionals])
    args = parser.parse_args(arguments)
    out = io.StringIO()
    code = _dispatch(command, out, Repo(repo), args, False, parser)
    return _response(out, code)


@_guard
def history_tool(repo: str, entity: str | None = None, base: str | None = None,
                 head: str | None = None, indexed: bool = False,
                 scope: str | None = None, generation: str | None = None,
                 at: str | None = None, order: str | None = None,
                 limit: int = 20, cursor: str | None = None, brief: bool = False,
                 budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """History of one entity, or transitions across two explicit commits.

    Indexed entity history follows supported lineage. Indexed ranges are
    chronological events, not endpoint diffs; resume with the returned range.
    Exact-source patches are included within budget by default. brief=true
    skips source reads; patch_status distinguishes budget and source omissions.
    """
    if not entity and (base is None or head is None):
        raise ValueError("Select an entity or both base and head")
    return _command_result(repo, "history", [entity] if entity else [],
                           since=base, until=head, indexed=indexed, scope=scope,
                           generation=generation, at=at, order=order, limit=limit,
                           cursor=cursor, brief=brief, budget=budget)


@_guard
def id_tool(repo: str, entity: str, scope: str | None = None,
            generation: str | None = None, at: str | None = None,
            historical: bool = False, limit: int = 20, cursor: str | None = None,
            budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Resolve a name, locator or identity prefix without guessing ambiguous matches."""
    return _command_result(repo, "id", [entity], scope=scope, generation=generation,
                           at=at, historical=historical, limit=limit, cursor=cursor, budget=budget)


@_guard
def index_tool(repo: str, action: str = "status", scope: str | None = None,
               ref: str | None = None, since: str | None = None, rebuild: bool = False,
               workers: int | None = None, parse_timeout: float | None = None,
               generation: str | None = None, source_check: bool = False,
               budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Inspect status, explicitly build/update, verify, or collect an index generation.

    Build and gc write local index storage, never Git source or commits.
    Verification reports unavailable until the independent verifier is implemented.
    """
    if action not in {"status", "build", "verify", "gc"}:
        raise ValueError("action must be status, build, verify or gc")
    return _command_result(repo, "index", status=action == "status", verify=action == "verify",
                           gc=action == "gc", scope=scope, ref=ref, since=since, rebuild=rebuild,
                           workers=workers, parse_timeout=parse_timeout, generation=generation,
                           source_check=source_check, budget=budget)


def _read_query(repo, command, fetch, *, scope, generation, at, budget, limit=20, cursor=None):
    from ..cli import indexed as adapter
    from ..index import query

    out = io.StringIO()
    payload = adapter._envelope(command)
    args = SimpleNamespace(as_json=True, budget=budget, limit=limit)
    try:
        if budget < 0:
            raise QueryError("invalid_budget", "Budget must be nonnegative")
        query.IndexSession._limit(limit)
        minimum = adapter._failure(adapter._envelope(command), "budget_too_small", "Increase budget.")
        if count_tokens(adapter._serialized(minimum, True)) > budget:
            raise QueryError("budget_too_small", "Increase budget to hold a complete response")
        with CommittedRepo(repo) as repository:
            if cursor is not None:
                try:
                    if type(cursor) is not str or not 0 < len(cursor) <= 4096:
                        raise ValueError("Invalid cursor length")
                    fields = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4),
                                                       altchars=b"-_", validate=True))
                    if fields["action"] != "entities_in" or command != "entities":
                        raise ValueError("Wrong cursor action")
                    if generation is not None and generation != fields["generation"]:
                        raise ValueError("Wrong cursor generation")
                    generation = fields["generation"]
                    selected = query._json_oid(fields["at"])
                    if at is not None and repository.resolve(at) != selected:
                        raise ValueError("Wrong cursor revision")
                    at = selected
                except (KeyError, TypeError, ValueError, RecursionError, QueryError, GitObjectError) as error:
                    raise QueryError("invalid_cursor", "Malformed or mismatched cursor") from error
            with query.open_index(repository, scope, generation) as session:
                selected = adapter._metadata(payload, session, repository, at)
                if command == "entities":
                    def page(page_limit):
                        return fetch(session, selected, page_limit, cursor)
                    code = adapter._emit_page(out, payload, page, args)
                else:
                    result = fetch(session, selected)
                    payload.update(status="ok", results=[result])
                    payload["page"].update(shown=1, total=1)
                    payload["omitted"].update(count=0, count_known=True)
                    code = adapter._emit(out, payload, True, budget)
    except (QueryError, IndexStoreError, IndexContractError, GitObjectError) as error:
        adapter._failure(payload, error.code, str(error))
        code = adapter._emit(out, payload, True, max(0, budget))
    return _response(out, code)


@_guard
def state_tool(repo: str, eid: str, at: str | None = None, scope: str = "default",
               generation: str | None = None, budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """State of a full EID at a committed revision: present, removed, absent or unknown."""
    return _read_query(repo, "state", lambda session, selected: session.state_at(eid, selected),
                       scope=scope, generation=generation, at=at, budget=budget)


@_guard
def entities_tool(repo: str, path: str, at: str | None = None, scope: str = "default",
                  generation: str | None = None, limit: int = 20, cursor: str | None = None,
                  budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Page exact occurrences in a repository-relative file at a pinned revision."""
    return _read_query(repo, "entities",
                       lambda session, selected, size, continuation: session.entities_in(
                           path.encode("utf-8"), selected, limit=size, cursor=continuation),
                       scope=scope, generation=generation, at=at, budget=budget, limit=limit, cursor=cursor)


@_guard
def source_tool(repo: str, occurrence: dict[str, Any], scope: str = "default",
                generation: str | None = None, budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Retrieve exact committed bytes from a returned occurrence, never worktree text.

    Content is UTF-8 text when lossless, otherwise base64. A budget error is
    returned rather than truncating source. The occurrence pins its generation.
    """
    from ..index.query import _json_oid

    at = _json_oid(occurrence.get("commit"))
    pinned = occurrence.get("generation")
    if generation is not None and generation != pinned:
        raise QueryError("invalid_source", "Generation differs from the occurrence")

    def fetch(session, selected):
        raw = session.source(occurrence)
        try:
            content, encoding = raw.decode("utf-8", errors="strict"), "utf-8"
        except UnicodeDecodeError:
            content, encoding = base64.b64encode(raw).decode("ascii"), "base64"
        return {"occurrence": occurrence, "content": content, "encoding": encoding, "byte_length": len(raw)}

    return _read_query(repo, "source", fetch, scope=scope, generation=pinned, at=at, budget=budget)


TOOLS = {
    "gita_diff": diff_tool,
    "gita_expand": expand_tool,
    "gita_show": show_tool,
    "gita_history": history_tool,
    "gita_id": id_tool,
    "gita_index": index_tool,
    "gita_state": state_tool,
    "gita_source": source_tool,
    "gita_entities": entities_tool,
}
