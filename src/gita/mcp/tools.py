"""Tool implementations for the MCP server.

Kept free of the MCP SDK so the contract an agent depends on is testable without
a transport. Every result is navigable: it names the entities an agent can drill
into next, so progressive disclosure is discoverable rather than documented.

Each layer is a separate tool rather than a depth parameter on one tool, so that
an agent sees the cost of each step in the tool name it chooses.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..context import build_view, count_tokens, entity_diff, expand, query_view
from ..revisions import diff_revisions
from ..telemetry import record, timed
from ..vcs.git import GitError, Repo

DEFAULT_BASE = "HEAD^"
DEFAULT_HEAD = "HEAD"
DEFAULT_BUDGET = 1000

_MAX_SUGGESTIONS = 5


def _guard(fn: Callable[..., dict]) -> Callable[..., dict]:
    def wrapper(*args, **kwargs) -> dict:
        with timed() as elapsed:
            try:
                result = fn(*args, **kwargs)
            except GitError as error:
                result = {"error": str(error)}
            except (OSError, ValueError) as error:
                result = {"error": f"{type(error).__name__}: {error}"}

        record({
            "arm": "gita",
            "tool": f"gita_{fn.__name__.removesuffix('_tool')}",
            "output_tokens": count_tokens(json.dumps(result, default=str)),
            "latency_ms": elapsed.ms,
            "ok": "error" not in result,
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
def diff_tool(repo: str, base: str = DEFAULT_BASE, head: str = DEFAULT_HEAD,
              budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """L0 headline plus a budgeted L1 entity view for a range of revisions."""
    repository = _open(repo, base, head)
    changeset = diff_revisions(repository, base, head)
    view = build_view(changeset, budget=budget)

    return {
        "base": base,
        "head": head,
        "l0": view.l0,
        "l1": view.l1,
        "tokens": view.tokens,
        "budget": budget,
        "truncated": view.truncated,
        "files_changed": changeset.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
        "changes": [
            {"id": c.entity.id, "kind": c.kind.value,
             "interface": c.affects_interface}
            for c in changeset.material()
        ],
        "next": _suggestions(changeset),
    }


@_guard
def expand_tool(repo: str, entity: str, base: str = DEFAULT_BASE,
                head: str = DEFAULT_HEAD,
                budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """Descendants of a rolled-up entity, still without paying for hunks."""
    repository = _open(repo, base, head)
    changeset = diff_revisions(repository, base, head)
    lines = expand(changeset, entity, budget=budget)
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


@_guard
def ask_tool(repo: str, question: str, base: str = DEFAULT_BASE,
             head: str = DEFAULT_HEAD,
             budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """A view narrowed to the changes a question is about."""
    repository = _open(repo, base, head)
    changeset = diff_revisions(repository, base, head)
    view = query_view(changeset, question, budget=budget)

    return {
        "question": question,
        "base": base,
        "head": head,
        "l0": view.l0,
        "l1": view.l1,
        "tokens": view.tokens,
        "truncated": view.truncated,
        "next": _suggestions(changeset),
    }


@_guard
def savings_tool(repo: str, base: str = DEFAULT_BASE,
                 head: str = DEFAULT_HEAD,
                 budget: int = DEFAULT_BUDGET) -> dict[str, Any]:
    """What this context diff costs versus sending the raw git diff."""
    repository = _open(repo, base, head)
    changeset = diff_revisions(repository, base, head)
    view = build_view(changeset, budget=budget)
    raw_tokens = count_tokens(repository.raw_diff(base, head, changeset.paths()))

    return {
        "base": base,
        "head": head,
        "raw_tokens": raw_tokens,
        "l0_tokens": count_tokens(view.l0),
        "l1_tokens": view.tokens,
        "reduction": (1 - view.tokens / raw_tokens) if raw_tokens else 0.0,
        "files_changed": changeset.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
    }


TOOLS = {
    "gita_diff": diff_tool,
    "gita_expand": expand_tool,
    "gita_show": show_tool,
    "gita_ask": ask_tool,
    "gita_savings": savings_tool,
}
