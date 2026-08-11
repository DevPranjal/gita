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

### Session 2 — iterations 5 and 6

**Result is stable and reproducible.** Two independent 60-run sweeps:

| | it5 | it6 |
| --- | ---: | ---: |
| credits vs git | -16.7% | **-16.8%** |
| turns | 3.67 -> 3.00 | 3.73 -> 3.00 |
| recall | 100% / 100% | 100% / 100% |
| adoption | 100% | 100% |

### Pattern across every cycle so far

Every cost regression turned out to be a **correctness or robustness defect wearing a
cost disguise**. Not one was a tuning problem.

| symptom | actual defect |
| --- | --- |
| +149% on one task | `UnicodeEncodeError` on a piped Windows shell |
| +29% on history | reported *when* but not *what* |
| +18%, recall 78% | Rust impl blocks named after the type, losing the trait |
| +25% on uncommitted | harness rewrote whole files via LF->CRLF |
| gita never invoked | AGENTS.md did not mention the bare form |

This is worth stating in any pitch: the evaluation is not measuring compression, it is
finding bugs. The cost number is a *detector*.

### Failure this cycle: untracked support made the target task worse

Added untracked files to working-tree diffs (a genuine correctness fix -- a file an agent
had just written was invisible). `flask-uncommitted` went **+26% -> +41%**, turns 4.0 -> 6.0.

Cause: the harness writes its own `AGENTS.md` into the repo. Once gita could see untracked
files, it correctly reported `AGENTS.md` as a change, and the agent spent turns reviewing
the harness's scaffolding as if it were the user's work.

gita was right. The harness was wrong. `AGENTS.md` is now added to `.git/info/exclude`
so it stays discoverable without being part of the diff.

**Rule reinforced:** when a fix makes a number worse, find out why before reverting. The
fix was correct; the environment was lying.

### Session 3 - iteration 8: a fix that worked, and a task that fell apart

`flask-uncommitted` was **fixed** by the reference reporting: +19% -> -2%, and the
`git status` + `git diff -U15` follow-up calls disappeared. The diagnosis held.

But the headline went **-19.5% -> -9.5%**, because `got-new-option` went
**-38% -> +111%** at 7.7 turns. One task, four separate defects underneath it.

#### 1. Bulk test churn crowded out the answer

The commit adds one option and **159 test cases** whose names all restate it. The
summary listed 20 of them, each a truncated 100-character `test('allowAbsoluteUrls
false rejects whitespace-prefixed scheme-relative URL from retryWithMergedOpt...`,
and the actual API change sat below them. The agent read files by hand instead.

Test files now collapse to one line each. The summary now names `allowAbsoluteUrls`
directly, and the answer fell 5,273 -> 3,969 tokens (git diff: 16,713).

Guarded: roll-up only applies when tests are bulk **and** something else changed. A
test-only commit is about its tests, and hiding them would hide the answer.

#### 2. The dead-code check raised 159 false alarms

Every added test was reported as "unreferenced". A test is invoked by its runner,
never by name. And because only 25 lookups are affordable, **all 25 were spent on
tests before reaching a single source change** -- the feature could not have worked
on any repo with tests. Same failure mode as `ask()`: confidently wrong.

#### 3. My telemetry had been lying for three iterations

`got-new-option` reported 10,546 output tokens. The real figure was 5,273 -- exactly
half. `render.write` retries after a failed encode, and the measuring tee counted the
*attempt* as well as the retry. Only this task carries non-ASCII text, so only this
task was inflated, in iterations 6, 7 and 8.

Reported credits come from the model's own logs, so the headline numbers stand. But I
published a tool-token figure that was 2x wrong and did not notice for three cycles.

**Rule added:** an instrument that can only be wrong in one direction, on one input,
is the hardest kind to catch. Measure the measurement against ground truth
occasionally -- `count_tokens(actual stdout)` versus what telemetry claims.

#### 4. We were corrupting source text

The cp1252 fallback turned an arrow inside a quoted documentation line into `?`.
Small, but it is the one thing gita promises never to do: degrade to worse prose,
never wrong facts. `?` in a quoted line is a wrong fact. We now ask the stream for
UTF-8 first.

#### Performance, because 10 seconds is a correctness problem too

`entity_diff` re-read and re-parsed the same file for every entity -- `options.ts`
parsed ~15 times in one call. 11.2s -> 6.2s. An agent that waits ten seconds starts
working around the tool, which is exactly what the turn count showed.

#### State

335 tests. `9ef8033`. Iteration 9 queued.

**Expected:** `got-new-option` returns to negative; `flask-uncommitted` stays fixed;
headline returns to at least -19.5%. If `got-new-option` does not move, the test
roll-up was not the cause and gets reverted.

### Session 4 - iteration 9, and the bug the quality number found

Headline **-18.1%**, turns **3.33 -> 2.67** (best yet). `got-new-option`
**+111% -> -30%** at 2.0 turns: the test-churn diagnosis was right.

But recall fell to 98.3%, the first time gita has scored below git. One
`flask-dependency-update` repetition named `pyproject.toml` and not `uv.lock`.

Chasing it found that **a TOML or YAML value change was classified cosmetic and
filtered out as noise**. tree-sitter gives a TOML string two quote children with
the value in the gap between them, owning no node of its own; `_own_tokens`
collected leaves only, so it saw `"` and `"` and dropped `3.0`. A dependency
version bump -- the entire point of the task -- hashed as unchanged.

gita said *no material changes* for a version bump. That is the failure mode the
whole design exists to prevent: not a worse answer, a wrong one. It survived
nine iterations inside a task scoring -41%, because a cost win was sitting on
top of a correctness hole.

**Rule added:** a recall miss outranks a cost regression. Five bugs so far were
found by cost going the wrong way; this one was found by a quality number moving
1.7 points while cost looked healthy. Watch the quality column first.

**Also this cycle**, without waiting for a number to justify it:

- The same edit across sibling files now collapses to one line. Four
  `examples/*/pyproject.toml` isort blocks produced 25 mentions of
  `pyproject.toml` against 2 of `uv.lock`. Same defect as the test churn,
  different shape -- worth fixing once the pattern was visible twice.
- Six error paths audited and fixed. The worst: `resolve` was structurally
  broken because `git rev-parse` **echoes an unknown argument on stdout while
  failing**, so every guard built on its truthiness passed. Errors now state
  what happened and what to run next, in one line, with git's advice text
  stripped.

Per-task noise is real: the git baseline moved 7% between sweeps. Four clean
sweeps at -16.7 / -16.8 / -19.5 / -18.1 are the honest band; single-task deltas
are not worth chasing unless they are large or repeat.

**State:** 360 tests. `c41b55c`. Iteration 10 running.
