# Journal

Running log of the autonomous session. Failures recorded as prominently as wins.

---

## Session 1 — fix the three iteration-3 regressions

**Method:** every diagnosis came from `evals/runs/20260810-201333/` artifacts —
the telemetry, the agent's actual shell commands, the answers. Not intuition.

### What the artifacts showed

| task | what the agent actually did |
| --- | --- |
| gin-history | `gita history SaveUploadedFile` → **ok=False**, then `git log -L` |
| ripgrep-walker | gita answered in 471 tokens but never printed the word `Iterator` |
| flask-uncommitted | `gita diff HEAD` reported **15 changes** for one added function |

### Root causes

1. **Entity ids were strict on input.** Storage needs exact identifiers; input does
   not. Agents type `fetch`, not `svc.py::fetch`.
2. **Rust impl blocks were named after the type.** `impl Iterator for Walk` became
   `Walk#3`. The trait vanished — and the ground truth for that task was `Iterator`.
   The `#N` suffix was a symptom of throwing information away, not a naming quirk.
3. **My harness corrupted the task.** `write_text(read_text() + append)` converts a
   whole file LF→CRLF on Windows. git saw every line change. gita reported that
   faithfully; the +25% was mine.

### Mistake made and corrected within the session

Wired resolution as the *first* step. That turned `app.py::Store` — a valid
container query — into `app.py::Store::get`, because `Store` is a substring of
`Store::get`. Caught by an existing test.

**Rule learned:** resolution is a fallback. Try the literal argument first, always.
A convenience that overrides a correct input is not a convenience.

### State

295 tests. `4c3506f`. Iteration 4 queued to confirm the fixes move the numbers.

### Expected effect

- gin-history: should stop falling back to `git log -L`
- ripgrep-walker: recall should return to 100% (`Iterator` now printed)
- flask-uncommitted: diff shrinks to one function instead of a whole file

If any of these does not move, the fix was wrong and gets reverted.
