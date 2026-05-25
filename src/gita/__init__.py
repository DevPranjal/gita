"""gita — agentic version control as a thin sidecar over git.

gita stores a structural change manifest (symbol-level ops, not text hunks)
alongside every git commit at ``.git/gita/manifests/<commit_sha>.json`` and
exposes it through a small set of read commands an agent can call:

* ``gita diff``        — manifest for working tree vs HEAD (or any two refs)
* ``gita status``      — porcelain status with op category counts
* ``gita commit``      — runs ``git commit`` then writes the manifest
* ``gita explain``     — print the manifest stored with a commit
* ``gita symbol-log``  — commits that touched a named symbol
* ``gita callers``     — call sites of a symbol, anywhere in the tree
* ``gita reindex``     — backfill manifests for commits that don't have one
* ``gita mcp``         — start an MCP server over stdio so agents can call
                         all of the above as tools
"""

__version__ = "0.1.0"
