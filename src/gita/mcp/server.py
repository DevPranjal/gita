"""MCP server exposing gita's context layers to agents.

Thin by design: every tool delegates to `gita.mcp.tools`, which is testable
without a transport. Requires the optional `mcp` extra.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from . import tools
from ..legacy import legacy_enabled

LEGACY_SERVER_INSTRUCTIONS = """\
gita gives you context diffs instead of line diffs.

Start with gita_diff for a headline and a list of changed entities. It is cheap.
Then drill only where you need to:
  gita_expand   children of a rolled-up entity ("Parent (+4 nested)")
  gita_show     the exact hunks for one entity  -- the expensive layer

gita_diff takes `filter` to restrict to entities matching a term, and
`interface_only` to show just the changes that can break a caller. Both are
exact; gita does not guess at intent.

Use gita_history with entity for one function, or base/head for a commit range.
History is indexed by default; indexed=true is a compatibility no-op.
Set legacy=true only to reconstruct unindexed history. History
includes exact-source patches within budget; brief=true skips source reads.
Inspect each event's patch_status: omitted_budget retains retrievable source
references; unavailable marks missing or non-UTF-8 source, never an empty edit.
Range events include intermediate changes; gita_diff --indexed currently uses those events
too, not a net endpoint comparison. Plain gita_diff compares endpoints.

The indexed workflow is gita_index(action="status"), an explicit build if needed,
then gita_id -> gita_history/gita_state -> gita_source. gita_entities lists a
file's occurrences. Reads never build an index and exclude uncommitted work.
Keep returned scopes, generations, occurrences and cursors together. Report
partial coverage or ambiguity instead of treating it as proof of absence.
Use budget/limit and continuation cursors; source is never silently truncated.
gita_index build/gc mutate only the local index; verify is not yet available.
There is no caller graph, semantic equivalence proof, or merge-safety verdict.
"""

SERVER_INSTRUCTIONS = """\
Gita provides published indexed entity evidence.
Start with gita_index(action="status"); build explicitly if needed.
Use gita_id -> gita_history/gita_state -> gita_source.
gita_entities lists a file's recorded occurrences.
History is indexed by default; indexed is a compatibility no-op.
Range history returns intermediate events, not endpoint diffs.
Reads never build or parse; working-tree/staged/untracked queries are out of scope.
Keep scope, generation, occurrence references and continuation cursors together.
Report ambiguity and partial coverage. Source is never silently truncated.
Patches are included within budget unless brief=true; inspect patch_status.
Verification is unavailable; there is no semantic equivalence or merge-safety verdict.
"""


def _default_repo() -> str:
    return os.environ.get("GITA_REPO", ".")


def build_server(repo_path: str | None = None):
    """Construct the MCP server. Imported lazily so the CLI works without the SDK."""
    enabled = legacy_enabled()
    # Absolute import: this resolves to the SDK, not to the enclosing gita.mcp.
    try:
        from mcp.server import MCPServer as _Server  # SDK 2.x
    except ImportError:  # pragma: no cover - depends on installed SDK generation
        try:
            from mcp.server.fastmcp import FastMCP as _Server  # SDK 1.x
        except ImportError as error:
            raise RuntimeError(
                "the MCP server needs the optional dependency: "
                "pip install 'gita[mcp]'"
            ) from error

    root = repo_path or _default_repo()
    instructions = SERVER_INSTRUCTIONS
    if enabled:
        instructions += "\nLegacy opt-in enabled (GITA_ENABLE_LEGACY=1).\n" + LEGACY_SERVER_INSTRUCTIONS
    server = _Server("gita", instructions=instructions)

    def legacy_tool():
        return server.tool() if enabled else lambda function: function

    @legacy_tool()
    def gita_diff(base: str = tools.DEFAULT_BASE, head: str | None = tools.DEFAULT_HEAD,
                  budget: int = tools.DEFAULT_BUDGET, filter: str = "",
                  interface_only: bool = False, repo: str = "", brief: bool = False,
                  intent: str = "", patch: bool = False, indexed: bool = False,
                  scope: str | None = None, generation: str | None = None) -> dict:
        """Context diff between two revisions: headline plus changed entities.

        `filter` restricts to entities matching a term; `interface_only` shows
        potential interface changes, not proven breakage. Pass base="HEAD",
        head=null for uncommitted work. indexed=true reads range events, not net
        endpoint changes, and requires a built index.
        """
        return tools.diff_tool(repo or root, base=base, head=head, budget=budget,
                               filter=filter, interface_only=interface_only, brief=brief,
                               intent=intent, patch=patch, indexed=indexed, scope=scope,
                               generation=generation)

    @legacy_tool()
    def gita_expand(entity: str, base: str = tools.DEFAULT_BASE,
                    head: str = tools.DEFAULT_HEAD,
                    budget: int = tools.DEFAULT_BUDGET, repo: str = "", intent: str = "") -> dict:
        """List the changed descendants of a rolled-up entity."""
        return tools.expand_tool(repo or root, entity, base=base, head=head,
                                 budget=budget, intent=intent)

    @legacy_tool()
    def gita_show(entity: str, base: str = tools.DEFAULT_BASE,
                  head: str = tools.DEFAULT_HEAD, repo: str = "") -> dict:
        """Exact hunks for one entity. Call only after diff or expand named it."""
        return tools.show_tool(repo or root, entity, base=base, head=head)

    @server.tool()
    def gita_history(entity: str | None = None, base: str | None = None,
                     head: str | None = None, indexed: bool = False,
                     scope: str | None = None, generation: str | None = None,
                     at: str | None = None, order: Literal["newest", "oldest"] | None = None,
                     limit: int = 20, cursor: str | None = None, brief: bool = False,
                     budget: int = tools.DEFAULT_BUDGET, repo: str = "",
                     legacy: bool = False) -> dict:
        """Trace one entity, or supply base/head for changes throughout a commit range.

        Persistent evidence is the default; indexed is a compatibility no-op.
        legacy=true requires GITA_ENABLE_LEGACY=1 and reconstructs old history.
        Indexed ranges are oldest-first.
        For indexed entity history use at, not base/head. Keep returned cursor,
        scope and pinned range together. Reads never build an index.
        Patches are included within budget unless brief=true. patch_status
        reports omitted patches while source/destination references remain usable.
        """
        return tools.history_tool(repo or root, entity=entity, base=base, head=head,
                                  indexed=indexed, scope=scope, generation=generation,
                                  at=at, order=order, limit=limit, cursor=cursor,
                                  brief=brief, budget=budget, legacy=legacy)

    @server.tool()
    def gita_id(entity: str, scope: str | None = None, generation: str | None = None,
                at: str | None = None, historical: bool = False, limit: int = 20,
                cursor: str | None = None, budget: int = tools.DEFAULT_BUDGET,
                repo: str = "") -> dict:
        """Resolve a persistent entity identity; ambiguity returns candidates, never a guess."""
        return tools.id_tool(repo or root, entity, scope=scope, generation=generation, at=at,
                             historical=historical, limit=limit, cursor=cursor, budget=budget)

    @server.tool()
    def gita_index(action: Literal["status", "build", "verify", "gc"] = "status",
                   scope: str | None = None, ref: str | None = None, since: str | None = None,
                   rebuild: bool = False, workers: int | None = None,
                   parse_timeout: float | None = None, generation: str | None = None,
                   source_check: bool = False, budget: int = tools.DEFAULT_BUDGET,
                   repo: str = "") -> dict:
        """Inspect index status or explicitly build/update or collect a generation.

        Build/gc write local index storage, not Git. gc requires generation.
        Verification currently returns verification_unavailable.
        """
        return tools.index_tool(repo or root, action=action, scope=scope, ref=ref, since=since,
                                rebuild=rebuild, workers=workers, parse_timeout=parse_timeout,
                                generation=generation, source_check=source_check, budget=budget)

    @server.tool()
    def gita_state(eid: str, at: str | None = None, scope: str = "default",
                   generation: str | None = None, budget: int = tools.DEFAULT_BUDGET,
                   repo: str = "") -> dict:
        """Get present/removed/absent/unknown state and source occurrence for a full EID."""
        return tools.state_tool(repo or root, eid, at=at, scope=scope,
                                generation=generation, budget=budget)

    @server.tool()
    def gita_source(occurrence: dict[str, Any], scope: str = "default",
                    generation: str | None = None, budget: int = tools.DEFAULT_BUDGET,
                    repo: str = "") -> dict:
        """Get exact committed source using an occurrence returned by id/state/history/entities.

        Content is UTF-8 or base64 with explicit encoding; budget overflow is an
        error, never silently truncated source. Reuse the occurrence's scope.
        """
        return tools.source_tool(repo or root, occurrence, scope=scope,
                                 generation=generation, budget=budget)

    @server.tool()
    def gita_entities(path: str, at: str | None = None, scope: str = "default",
                      generation: str | None = None, limit: int = 20,
                      cursor: str | None = None, budget: int = tools.DEFAULT_BUDGET,
                      repo: str = "") -> dict:
        """List indexed entities in a repository-relative file, with pinned pagination."""
        return tools.entities_tool(repo or root, path, at=at, scope=scope,
                                   generation=generation, limit=limit, cursor=cursor, budget=budget)

    return server


def serve(repo_path: str | None = None, transport: str = "stdio") -> int:
    build_server(repo_path).run(transport=transport)
    return 0
