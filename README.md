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
| *"which commit broke the tests?"*           | `git bisect run` from scratch                | `gita bisect-proven pytest`                                    |
| *"re-run my checks after every commit"*     | hand-rolled hook                             | `gita hooks install` + `gita auto enable pytest -- ...`        |

---

## the four wins for an agent

1. **diffs as operations, not hunks.** `add` / `remove` / `rename` / `signature` / `body` / `import +` / `import -` / `imports reordered` / `format` — small closed vocabulary, summary line first.
2. **symbols are first-class.** `gita get fetch_user@HEAD~5`, `gita callers`, `gita symbol-log` — no `grep + sed` as a substitute for "give me this function."
3. **proofs, not commit messages.** `gita prove` records a check against a commit; `gita last-proven` answers *"which commit last passed?"* honestly; `gita bisect-proven` narrows a regression to the first failing commit.
4. **post-commit hook re-proves automatically.** `gita hooks install` + `gita auto enable pytest -- …` — every commit gets its proof without you remembering.

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
· `gita_callers` · `gita_get` · `gita_prove` · `gita_last_proven`
· `gita_bisect_proven` (nine, as of v0.3).

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

v0.3 · 193 tests passing · zero runtime deps beyond `libcst`.

The longer vision lives in [`inspiration.md`](./inspiration.md).
