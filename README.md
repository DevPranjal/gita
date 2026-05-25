# gita & gitpp

> Versioning **logic**, not text. Tools for agentic version control.

This repo ships two Python packages:

| Package | What it does |
| --- | --- |
| **`gita`** | A git-native sidecar. Wraps a real git repo, gives you semantic diffs, per-symbol history (`symbol-log`), a cross-file callers index, and an MCP server so coding agents can ask *"what did this commit actually change?"* without re-deriving it from raw text. |
| **`gitpp`** | The original research prototype: a from-scratch CST-aware VCS (no git underneath) that merges concurrent agent edits structurally. The thesis-prover for [`inspiration.md`](./inspiration.md). |

If you just want something usable on a real repo today → use **`gita`**.
If you want to see structural merge proven on hand-crafted scenarios → use **`gitpp`**.

---

## Install

```powershell
git clone https://github.com/DevPranjal/gita
cd gita
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Now both `gita` and `gitpp` are on your PATH.

---

## `gita` — git-native semantic layer

Run it inside any git repo:

```powershell
gita init                  # create .git/gita/ store (optional; commands work without it)
gita status                # porcelain + manifest summary of unstaged changes
gita diff                  # HEAD vs working tree, structural
gita diff HEAD~3 HEAD      # any two refs
gita diff --staged --json  # machine-readable for agents
gita commit -m "msg"       # like git commit, plus writes a manifest
gita explain <ref>         # what *actually* changed in that commit
gita symbol-log fetch_user # every commit that touched this symbol, across renames
gita callers fetch_user    # every call site, multi-file, with line numbers
gita reindex               # back-fill manifests for older commits
```

### What's in a manifest

Each commit's manifest is a JSON document of structural operations, not a text
diff:

```json
{
  "kind": "manifest",
  "from": "abc123…",
  "to":   "def456…",
  "files": [{
    "path": "users.py",
    "status": "modified",
    "ops": [
      {"op": "rename_symbol", "from": "get_user", "to": "fetch_user"},
      {"op": "modify_body",   "name": "fetch_user", "added": 1, "removed": 0}
    ]
  }],
  "summary": {"logic_ops": 2, "signature_ops": 1, "cosmetic_ops": 0}
}
```

Rename detection uses similarity over name-neutralised bodies (threshold 0.6,
greedy best-first, same-kind only). That means **rename + body edit in the
same commit** still reports as a rename, not as `add` + `remove`.

### MCP server for coding agents

```powershell
gita mcp   # stdio JSON-RPC 2.0, protocol version 2024-11-05
```

Tools exposed: `gita_diff`, `gita_status`, `gita_explain`, `gita_symbol_log`,
`gita_callers`. All take a `root` argument (or fall back to `$GITA_ROOT` /
cwd).

Register it once in your editor (example for clients that read an
`mcpServers` map):

```json
{
  "mcpServers": {
    "gita": {
      "command": "gita",
      "args": ["mcp"],
      "env": { "GITA_ROOT": "${workspaceFolder}" }
    }
  }
}
```

### Storage model

- Manifests: `.git/gita/manifests/<commit_sha>.json` (one per commit, local
  to the clone — not pushed by default).
- Callers index: `.git/gita/callers/<tree_sha>.json` (keyed by tree sha so it
  survives branch switches with identical trees).
- Nothing escapes `.git/`. Uninstalling gita = `rm -rf .git/gita`.

---

## `gitpp` — structural merge research prototype

`gitpp` stores Python code as a content-addressed tree of **CST nodes**
(Concrete Syntax Tree, via [LibCST]) instead of file blobs, and merges
concurrent edits by reasoning over the tree rather than character lines.

The thesis we are trying to prove:

> Two agents can make changes that traditional Git reports as conflicts, and
> `gitpp` merges them automatically and correctly because it understands the
> structure of the code.

See [`SPEC.md`](./SPEC.md) for the merge policy table and
[`tests/scenarios/`](./tests/scenarios/) for the canonical conflict cases.

---

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

72 passing, 2 xfailed (the two `gitpp` scenarios that are intentionally
unfinished and tracked in [`SPEC.md`](./SPEC.md)).

[LibCST]: https://libcst.readthedocs.io/
