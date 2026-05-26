# gita.

*— every agent needs a direction.*

Browsers, search, operating systems — everything is getting rebuilt from an
agent-first perspective, because agents are the next customers. If humans stop
writing code, humans stop using `git`. So what does `git` look like when its
user was never human to begin with?

---

## install

```sh
git clone https://github.com/DevPranjal/gita
cd gita
python -m venv .venv && .venv/bin/activate     # or .venv\Scripts\Activate.ps1
pip install -e .
```

Then, inside any git repo:

```sh
gita init        # one-time, optional — sets up .git/gita/
```

---

## 01 · diff

### show me what changed, not which bytes moved.

Same edit, two readings. The left is what an agent stares at today; the right
is what it actually wants.

```diff
# git diff — users.py
- def get_user(uid):
+ def fetch_user(uid):
      row = db.lookup('users', uid)
      if row is None:
          return None
-     return User.from_row(row)
+     user = User.from_row(row)
+     return user
```

```text
# gita diff — users.py
  modified  users.py
    rename     get_user → fetch_user  (2 ref(s))
    signature  fetch_user: def get_user(uid)  →  def fetch_user(uid)
    body       fetch_user: +1/-0
```

Rename detection runs on name-neutralised bodies (similarity ≥ 0.6, greedy
best-first, same-kind only), so **rename + body edit in one commit** still
comes back as a single rename, not as `add` + `remove`.

```sh
gita diff                  # HEAD vs working tree
gita diff HEAD~3 HEAD      # any two refs
gita diff --staged --json  # machine-readable, for agents
```

---

## 02 · explain

### commits remember what they meant.

Each commit's manifest is written next to the tree, as a small JSON blob.
`gita explain` just reads it back — no re-deriving, no re-parsing, no LLM
guessing from a diff.

```text
$ gita explain HEAD
commit a9b54e1  Pranjal Gulati <pranjal@…>  Tue May 26 12:04
    rename get_user, add caching

  modified  src/users.py
    rename     get_user → fetch_user  (2 ref(s))
    add        function fetch_user_cached
    body       fetch_user: +1/-0

  summary: 2 logic / 1 signature / 0 cosmetic
```

What the manifest actually looks like on disk:

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

Everything lives under `.git/gita/` — nothing escapes the repo. Uninstalling
gita is `rm -rf .git/gita`.

---

## 03 · ask

### walk the symbol, not the lines.

History is queryable by *thing*. Who renamed this function? Who calls it now?
Answers come from stored manifests plus one CST pass.

```text
$ gita symbol-log fetch_user
a9b54e1  rename get_user, add caching
    [src/users.py] rename get_user → fetch_user
710c2f3  faster lookup
    [src/users.py] modify body of get_user: +3/-1
d04a811  initial users module
    [src/users.py] add function get_user
```

```text
$ gita callers fetch_user
3 call site(s) of 'fetch_user':
  src/app/handlers.py:48   in UserHandler.get
  src/app/handlers.py:71   in UserHandler.put
  src/jobs/refresh.py:14   in run
```

The callers index is cached at `.git/gita/callers/<tree_sha>.json`, so it
survives branch switches when the tree is unchanged.

---

## mcp

A stdio MCP server is built in, so an agent can ask gita the same questions
you do:

```sh
gita mcp   # JSON-RPC 2.0 over stdio, protocol 2024-11-05
```

Tools exposed: `gita_diff`, `gita_status`, `gita_explain`, `gita_symbol_log`,
`gita_callers`. Each accepts a `root` argument (or falls back to
`$GITA_ROOT`, or cwd).

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

---

## tests

```sh
.venv/bin/python -m pytest -q
```

54 passing. Covers the git wrappers, similarity-based rename detection
(rename + body edit in one commit still pairs as a rename), manifest
storage, symbol-log across renames, the multi-file callers index and its
tree-sha cache, every CLI subcommand, and the MCP request/response surface.

---

The larger vision — the one this whole repo is a finger pointing at — is
in [`inspiration.md`](./inspiration.md).
