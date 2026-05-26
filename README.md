# gita.

*— every agent needs a direction.*

`git` was built for humans reading lines of text. But humans aren't the ones
writing most code anymore — agents are. And agents don't want lines. They
want answers.

**`gita` is `git` rebuilt around the questions an agent actually asks.**

---

## what changes

| the question                                | `git` answer                                 | `gita` answer                                                  |
| ------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------- |
| *"what changed in this commit?"*            | a wall of `+`/`-` lines                      | a short list of named operations on named symbols              |
| *"show me this function"*                   | `grep` for the name, then `sed -n 40,60p`    | `gita get fetch_user`                                          |
| *"show it the way it was 5 commits ago"*    | checkout, grep, sed, checkout back           | `gita get fetch_user@HEAD~5`                                   |
| *"who calls this function?"*                | `grep -rn` and hope                          | `gita callers fetch_user`                                      |
| *"when was the last commit where tests passed?"* | read commit messages, guess               | `gita last-proven pytest`                                      |
| *"every commit that touched this function"* | `git log -S` and squint                      | `gita symbol-log fetch_user`                                   |

---

## the four wins for an agent

### 1 · diffs as a list of operations

`git diff` says *"these bytes moved."*  `gita diff` says *"a function was
added here, the body of this one grew by four lines, this import appeared,
those were just reordered."*

```text
$ gita diff
manifest: 3 logic / 2 signature / 1 cosmetic op(s), 4 symbol(s)
  modified  src/api.py
    add        function rate_limit_check
    body       handle_get: +4/-1  added auth guard
    import +   functools (lru_cache)
  modified  src/users.py
    signature  fetch_user: added timeout param
    rename     UserStore → UserRepo  (3 ref(s))
  modified  src/jobs.py
    imports    reordered (5 entries, no add/remove)
  modified  config.yaml  (non-python, textual diff)
    @@ -12,7 +12,7 @@
    -  timeout: 30
    +  timeout: 60
```

The op vocabulary is small and closed: `add` / `remove` / `rename` /
`signature` / `body` / `import +` / `import -` / `imports reordered` /
`format`.  Non-Python files keep their unified diff, just labelled. The
summary line tells the agent at a glance whether this commit is *logic*
(`add`, `remove`, `body`) or just *signature* (renames, imports) or *purely
cosmetic* (reorders, whitespace) — useful for triaging what's worth a
deeper read.

> **why agents care:** an agent can decide *"do I even need to look at
> this commit?"* from the summary line alone. And when it does look, the
> answer is structured — no re-deriving rename-vs-add-vs-remove from
> textual hunks.

### 2 · symbols are first-class

`git` only knows files and lines. `gita` knows functions and classes.

```sh
gita get fetch_user                # returns the source
gita get UserHandler.put           # class methods too
gita get fetch_user@HEAD~5         # at any revision, walks renames
gita diff --symbol fetch_user      # filter diff to one thing
gita symbol-log fetch_user         # history filtered to one thing
gita callers fetch_user            # who calls it
```

> **why agents care:** the agent stops needing `grep + sed` as a substitute
> for "give me this function." It just asks.

### 3 · commits remember what they meant

Every `gita`-aware commit writes a manifest next to itself. `gita explain
HEAD` reads it back — no LLM is asked to guess intent from a `+`/`-` blob.

### 4 · proofs — *"which commit last passed?"*

Long-running agents commit often. After ten commits you want to know: which
of these does pytest still pass on? Commit messages lie. `gita prove` runs
a check and records the result against the commit:

```sh
gita prove pytest -- python -m pytest -q
gita prove mypy   -- mypy src

gita last-proven pytest          # → sha of the last green commit
gita symbol-log fetch_user       # ✓ / ✗ / · glyph per commit
```

`prove` refuses to run on a dirty tree — a proof can't lie about which
commit it covers.

> **why agents care:** an agent can bisect its own work by a real criterion
> ("last commit where tests passed and the symbol I touched still exists")
> instead of vibes.

---

## the same surface, for agents directly

A built-in MCP server exposes every command above as a tool:

```sh
gita mcp
```

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

Eight tools: `gita_diff` · `gita_status` · `gita_explain` · `gita_symbol_log`
· `gita_callers` · `gita_get` · `gita_prove` · `gita_last_proven`.

---

## install + try

```sh
git clone https://github.com/DevPranjal/gita && cd gita
python -m venv .venv && .venv\Scripts\Activate.ps1   # or source .venv/bin/activate
pip install -e .
```

Then in any repo:

```sh
gita init                                          # one-time
gita diff                                          # see structural diff
gita get <a function name in your codebase>        # try it
gita prove tests -- <your test command>            # record a proof
```

v0.2 · 116 tests passing · zero runtime deps beyond `libcst`.

The longer vision lives in [`inspiration.md`](./inspiration.md).
