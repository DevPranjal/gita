"""MCP server exposing gita's context layers to agents.

Thin by design: every tool delegates to `gita.mcp.tools`, which is testable
without a transport. Requires the optional `mcp` extra.
"""

from __future__ import annotations

import os

from . import tools

SERVER_INSTRUCTIONS = """\
gita gives you context diffs instead of line diffs.

Start with gita_diff for a headline and a list of changed entities. It is cheap.
Then drill only where you need to:
  gita_expand   children of a rolled-up entity ("Parent (+4 nested)")
  gita_show     the exact hunks for one entity  -- the expensive layer
  gita_savings  what this cost versus a raw git diff

gita_diff takes `filter` to restrict to entities matching a term, and
`interface_only` to show just the changes that can break a caller. Both are
exact; gita does not guess at intent.

Every result carries a `next` field naming the entities you can act on.
Prefer these over reading a raw `git diff`: the same change typically costs
50-100x fewer tokens here, and formatting-only noise is already removed.
"""


def _default_repo() -> str:
    return os.environ.get("GITA_REPO", ".")


def build_server(repo_path: str | None = None):
    """Construct the MCP server. Imported lazily so the CLI works without the SDK."""
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
    server = _Server("gita", instructions=SERVER_INSTRUCTIONS)

    @server.tool()
    def gita_diff(base: str = tools.DEFAULT_BASE, head: str = tools.DEFAULT_HEAD,
                  budget: int = tools.DEFAULT_BUDGET, filter: str = "",
                  interface_only: bool = False, repo: str = "") -> dict:
        """Context diff between two revisions: headline plus changed entities.

        `filter` restricts to entities matching a term; `interface_only` shows
        only changes that can break a caller.
        """
        return tools.diff_tool(repo or root, base=base, head=head, budget=budget,
                               filter=filter, interface_only=interface_only)

    @server.tool()
    def gita_expand(entity: str, base: str = tools.DEFAULT_BASE,
                    head: str = tools.DEFAULT_HEAD,
                    budget: int = tools.DEFAULT_BUDGET, repo: str = "") -> dict:
        """List the changed descendants of a rolled-up entity."""
        return tools.expand_tool(repo or root, entity, base=base, head=head,
                                 budget=budget)

    @server.tool()
    def gita_show(entity: str, base: str = tools.DEFAULT_BASE,
                  head: str = tools.DEFAULT_HEAD, repo: str = "") -> dict:
        """Exact hunks for one entity. Call only after diff or expand named it."""
        return tools.show_tool(repo or root, entity, base=base, head=head)

    @server.tool()
    def gita_savings(base: str = tools.DEFAULT_BASE, head: str = tools.DEFAULT_HEAD,
                     repo: str = "") -> dict:
        """Token cost of the context diff versus the raw git diff."""
        return tools.savings_tool(repo or root, base=base, head=head)

    return server


def serve(repo_path: str | None = None, transport: str = "stdio") -> int:
    build_server(repo_path).run(transport=transport)
    return 0
