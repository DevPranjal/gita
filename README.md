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
| *"what changed in this commit?"*            | a pile of `+`/`-` lines                      | *"`get_user` was renamed to `fetch_user`, body +1/-0"*         |
| *"show me this function"*                   | `grep` for the name, then `sed -n 40,60p`    | `gita get fetch_user`                                          |
| *"show it the way it was 5 commits ago"*    | checkout, grep, sed, checkout back           | `gita get fetch_user@HEAD~5`  (follows renames)                |
| *"who calls this function?"*                | `grep -rn` and hope                          | `gita callers fetch_user`                                      |
| *"when was the last commit where tests passed?"* | read commit messages, guess               | `gita last-proven pytest`                                      |
| *"every commit that touched this function"* | `git log -S` and squint                      | `gita symbol-log fetch_user`                                   |

---

## the four wins for an agent

### 1 · diffs that name what moved

A rename plus a body edit in the *same* commit is what trips every diff tool
on earth. `git` shows it as one function deleted and another added — so the
agent burns tokens re-deriving "oh, this was a rename."

`gita` does that work once, at commit time, and stores the answer:

```text
$ gita diff
modified  src/users.py
  rename     get_user → fetch_user  (2 ref(s))
  signature  fetch_user: def get_user(uid) → def fetch_user(uid)
  body       fetch_user: +1/-0
```

> **why agents care:** no LLM round-trips to re-discover intent from a textual
> diff. The structural answer is in the manifest.

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
of these does pytest still pass on? Commit messages lie. `gita prove` runs a
check and records the result against the commit:

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
