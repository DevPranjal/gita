# gita.

*— every agent needs a direction.*

`git` was built for humans reading lines. Agents read *symbols*, ask *who calls
this*, and need to know *which commit last passed the tests*. `gita` is a thin
layer on top of `git` that answers those questions directly — no LLM
re-derivation from a textual diff, no scraping commit messages for intent.

v0.2 · 116 tests · zero runtime deps beyond `libcst`.

---

## install

```sh
git clone https://github.com/DevPranjal/gita && cd gita
python -m venv .venv && .venv\Scripts\Activate.ps1   # or source .venv/bin/activate
pip install -e .
gita init   # one-time, inside any repo — creates .git/gita/
```

---

## the five things gita does

### 1 · `diff` — what changed, not which bytes moved

Rename detection runs on name-neutralised bodies, so **rename + body edit in
one commit** comes back as one rename, not add+remove.

```text
$ gita diff
modified  src/users.py
  rename     get_user → fetch_user  (2 ref(s))
  signature  fetch_user: def get_user(uid) → def fetch_user(uid)
  body       fetch_user: +1/-0
```

Scope to a single symbol; pipe to an agent:

```sh
gita diff --symbol fetch_user
gita diff HEAD~3 HEAD --json
```

Non-Python files don't disappear — they come back with `parseable: false` and
a textual diff, so the manifest never lies about what moved.

### 2 · `get` — fetch a symbol's source, at any revision

The substrate gap. Agents shouldn't have to `grep` + `sed -n` to read a
function.

```text
$ gita get UserHandler.put
src/app/handlers.py:64-71  function UserHandler.put

    def put(self, uid, payload):
        user = fetch_user(uid)
        ...
```

Walks one rename hop backward, so `gita get fetch_user@HEAD~5` still resolves
when the symbol was called `get_user` back then. Ambiguous bare names exit 2
with qualified candidates; missing symbols exit 1.

### 3 · `prove` + `last-proven` — commit-keyed check results

> *"what's the last commit where pytest passed?"*

Stop guessing from commit messages. `prove` runs a command, captures the
result, and writes it next to the commit. Refuses to run on a dirty tree —
proofs can't lie about which commit they cover.

```sh
gita prove pytest -- python -m pytest -q     # ✓ pytest  (exit 0, 1832 ms)
gita prove mypy   -- mypy src                # ✗ mypy    (exit 1, 412 ms)

gita last-proven pytest          # → 07ac270…  (latest green for that check)
gita last-proven --symbol parse  # latest green commit where 'parse' still exists
```

### 4 · `symbol-log` + `explain` — history that knows what it meant

Every commit's manifest is stored at write time (`.git/gita/manifests/<sha>.json`).
`explain` reads it back; `symbol-log` filters history to one thing and threads
proof glyphs through it.

```text
$ gita symbol-log fetch_user
07ac270  ✓  mcp: expose get/prove/last-proven
0745f59  ✓  proofs: record/query check results
e99880d  ·  get: walk one rename hop backward
    [src/users.py] rename get_user → fetch_user
```

`✓` all checks green · `✗` something failed · `·` no proof recorded.

### 5 · `callers` — call sites, cached by tree

```text
$ gita callers fetch_user
3 call site(s) of 'fetch_user':
  src/app/handlers.py:48   in UserHandler.get
  src/app/handlers.py:71   in UserHandler.put
  src/jobs/refresh.py:14   in run
```

Cached at `.git/gita/callers/<tree_sha>.json`, so the index survives branch
switches when the tree is unchanged.

---

## mcp

Same surface, for agents. JSON-RPC 2.0 over stdio, protocol `2024-11-05`.

```sh
gita mcp
```

Eight tools: `gita_diff` (with `symbol` filter), `gita_status`, `gita_explain`,
`gita_symbol_log`, `gita_callers`, `gita_get`, `gita_prove`, `gita_last_proven`.
Ambiguous lookups return JSON-RPC `-32602` with candidates in `data.candidates`;
missing proofs return `-32000` with a hint.

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

## what it costs you

Everything lives under `.git/gita/` — manifests, callers index, proofs.
Nothing escapes the repo. Uninstalling is `rm -rf .git/gita`.

```sh
.venv\Scripts\python -m pytest -q   # 116 passed
```

The larger vision is in [`inspiration.md`](./inspiration.md).
