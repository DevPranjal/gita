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

### Session 5 - the control run, and what it cost me to skip it

Thirteen iterations in, I ran the sweep that should have been run second: the
same code twice. Iteration 12 and 13 differ only in scoring code, and the
headline moved **10 points**. One task moved **82**.

I had been diagnosing per-task regressions at n=3 and telling causal stories
about them. Some of those stories were true -- but I could not have known which
from the numbers alone, and I did not check.

**Rule added, and it outranks the others:** a number is evidence only after you
know what it does when nothing changes. Measure the noise floor before trusting
a delta above it.

The saving grace is that the loop had a second discipline running alongside:
every fix was reproduced by hand first -- a failing unit test for the TOML value
bug, a deterministic command-line repro for `gita history <name> <rev>`, a direct
before/after token measurement for the test roll-up. Those hold up. The cost
attributions attached to them do not.

Published numbers now pool the two identical sweeps: 110 runs, six repetitions
per task per arm, cache-misses excluded. **-15.4% credits, -23.1% turns, -92.4%
tool output**, 8 of 10 tasks cheaper. Turns and tool output are stable across
every sweep; cost is not, and the README and the site now say so.

---

## Session 6 (2026-08-22) - the data structures, not the surface

Direction: fewer features, stronger primitives, git-like maturity. Every change
below was chosen from a measurement and defended by one.

### What the profiler said, in order

| finding | measured | action |
| --- | --- | --- |
| 98 git subprocesses per history query, 67ms of spawn each | 6.53s of 12.08s | plumbing: `cat-file --batch`, one walk, path pruning |
| 43% of parses were of identical content | 4.2MB parsed per query | content-addressed `TreeStore` |
| one `git grep` per added entity | 0.93s of 2.59s | one call, count in Python |
| `entity_nodes` rebuilt per node | 603,193 calls | field, not property |
| entity subtree walked three times | 1.47s self time | one walk yields own + body |
| slot numbers treated as identities | 25 false changes / 2,858 | resolve sibling groups by content |

    gita diff (got, 13 files)   11.20s -> 1.82s   6.1x
    gita history (gin)          12.08s -> 2.75s   4.4x
    git subprocesses per query  98 -> 2

### Method notes worth keeping

**Equivalence, not hope.** Every fast path kept its slow path alive behind a
flag so tests can prove they agree: `series(batched=False)`,
`entity_history(prune=False)`. The hashing refactor was checked by fingerprinting
1,433 entities from five real repositories under both implementations --
identical digest, `9617c160d769225a1c910594d98bebce`.

**A loose metric flatters.** The renumbering fix first measured 44 false alarms;
most were new test callbacks whose content coincided with an existing one, which
are real additions. Restricting to the same name group gave 25, and 14 after the
fix. Reporting the loose number would have claimed roughly double the win.

**A step that changes nothing gets removed.** A second matching pass on the
normalised hash was written, measured, moved neither the metric nor a test, and
was deleted.

**Python's `splitlines()` treats \x1e as a line boundary.** A record separator
cannot be found with it. Cost twenty minutes.

### Stacked next

Experiments, cheapest first:

1. **Startup is now the floor.** 0.45s of every invocation is interpreter and
   imports, against 1.8s of work for the largest task. Measure what a lazy
   import of tiktoken and the language pack buys on the small tasks, where it is
   proportionally worst.
2. **Does any of this reach the agent?** Wall clock fell 4-6x but the eval
   measures credits and turns. Run a sweep at five repetitions rather than three
   -- the control run at three repetitions moved 10 points on its own, so three
   cannot resolve what this is worth.
3. **`expand` was used 4 times in 1,209 invocations.** Either it is redundant
   with one-shot answers, or the output never tells an agent it exists. Read the
   sessions where it was used before deciding which.
4. **14 renumbering false alarms remain.** All involve content that also
   changed. Determine whether sibling groups need similarity matching or whether
   this is the honest floor.

Data decisions pending:

- The `#N` scheme is stable under insertion of *differently* named siblings
  (measured, hypothesis disproved) and unstable only within a name group. That
  bounds how much a content-addressed id scheme could buy -- probably not enough
  to justify ids that change when a body is edited.
- `history` now walks 20 commits *that touched the entity*, which is git's
  meaning of `-n`, not the old "look at the last 20 commits". Better answers,
  more work per query. Worth confirming against agent behaviour, not intuition.

---

## Iteration 14 -- a declared correction to the corpus, and where the tokens really went

Two findings, one of which changes what I thought the tool's problem was.

### The benchmark was penalising correct answers

`flask-context-copy` scored 50% recall on *both* arms, all three repetitions.
A task that neither arm can pass is either very hard or wrongly specified, and
a symmetric failure is the signature of the second.

The prompt asks "which test covers it?". The required strings are
`ctx.py` and `test_greenlet_context_copying`. But that commit *deletes*
`TestGreenletContextCopying` along with its two methods and *adds*
`test_copy_context_thread`. The test that covers the new behaviour is the added
one; the required string names the removed one.

Both arms answered `test_copy_context_thread`, in `tests/test_reqctx.py`, and
both went further and noted that it replaces the removed greenlet class. The
answers were right and the answer key was wrong.

Correction, to be applied after iteration 15 so the running sweep is scored
against the file it started with:

    must_mention: [copy_current_request_context, test_copy_context_thread]

`ctx.py` is dropped because the prompt asks for the function, not the file.
Both replacement strings appear in the raw `git diff` (4 and 1 occurrences) and
in gita's answer (12 and 5), so neither arm is advantaged.

Changing an answer key after seeing results is goalpost-moving, and the only
reason it is not here is that the failure is exactly symmetric: the two arms
produced the same identification, so no correction to this task can favour
either one. It raises both absolute recall figures and cannot move the paired
comparison. I am recording the reasoning rather than the edit, so the claim can
be checked later.

### 87% of the gita arm's tool output was not gita

The headline said 23.5% fewer credits. The telemetry said something more useful.
Across the 54 gita runs, gita's own calls cost 76,067 tokens and falling back to
raw git cost 492,105 -- so the saving was won *despite* the fallbacks, and the
thing worth optimising was never the size of gita's answers.

The largest single fallback: `git diff -- uv.lock`, 214,918 tokens, on both
arms, twice. gita had answered that changeset well. It had also used 2,111 of
its 6,000-token budget, withheld fourteen of thirty-four changes because of
`MAX_DETAILED`, and reported `truncated: False`.

That last part is the defect. Only the budget set the truncation flag; a cap
that was not the budget dropped entities in silence. The agent could see the
answer had stopped early, did not believe the claim of completeness, and
recovered with the tool it trusts. An agent that cannot trust the boundary of an
answer will go and re-read the file, and then the careful summary above it was
spent for nothing.

So the lesson generalises past this bug: **a tool that summarises must be
trusted about what it left out, or the summary is worthless.** Every limit that
is not the budget has to declare itself.

Fixed, with the notice bought out of detail rather than appended -- v1.0.0
shipped the appended version and `--budget 120` emitted 211 tokens. The notice
also stops offering `--budget` where the raw diff is the cap, since raising it
there cannot help.

Measured and rejected in the same sitting: raising `MAX_DETAILED` from 20 to 60
adds about 14% more tokens per answer and moves the truncation rate not at all
(49% either way), because roll-up dominates the cap. It buys tokens rather than
completeness, so it stays at 20.

### Pre-registered for iteration 15

Written before the run, so the result can falsify it:

1. Fallback tokens in the gita arm fall well below 492,105; the `uv.lock` and
   `context.go` whole-file reads disappear.
2. `flask-dependency-update` (-36%) and `gin-copy-fix` (-27%) move toward zero
   or positive.
3. The headline improves on 23.5% and the interval stays clear of zero.
4. Recall does not fall: this adds information and withholds none.

If fallback tokens do not move, the notice is not being read, and the change
should be reverted rather than explained.

---

## The capability cohort -- first result (2026-08-25)

Five tasks over ranges whose raw diff does not fit in a context window (80k to
340k tokens, 52 to 98 files), asking questions whose answers are verified present
in the raw diff. Two repetitions, twenty runs.

| | normal cohort (18 tasks) | capability cohort (5 tasks) |
| --- | ---: | ---: |
| git recall | 99.5% | **77%** |
| gita recall | 99.7% | **93%** |
| gap | +0.2pt | **+16.7pt** |

**The instrument does what it was built to do.** On ordinary commits the two arms
are indistinguishable on quality and the only question is price. On changes too
large to read, a gap opens. git returned nothing usable twice -- `gin-range-
interface` and `ripgrep-range-triage`, recall 0 -- and gita never did.

Bootstrapped over tasks the gap is **[+0.0%, +36.7%]**: not separated from zero
at five tasks, but in no resample did git come out ahead. Directional, not yet
measured. The remedy is more tasks, not more repetitions, for the usual reason.

### The finding that complicates the story

gita used **fewer turns** (6.7 against 8.0) and **fewer prompt tokens** (827k
against 1,036k) and still cost **more credits**: 114.9 against 92.6.

The mechanism is cache writes. Median cache creation was 128,424 tokens for gita
against 43,407 for git, and a cache write is priced 12.5x a read. The baseline
arm reads one enormous diff, pays for it once, and re-reads it from cache on
every later turn. The gita arm makes several different calls, each returning
novel text that has to be written to cache before it can be read back.

So on large changes gita currently buys better answers in fewer turns with more
money. That is a real trade and it should be stated as one rather than hidden
behind the recall number. It also names its own fix: fewer, larger gita calls
would cache like the baseline does.

### A correction, declared

`flask-range-tests` scored 50% on both arms. The key demanded
`test_default_static_max_age`; the range changes only its *signature*, when type
annotations were added. `test_static_file` carries the real body change, and both
arms named it.

The ground truth had been derived by taking identifiers that appear on both the
`+` and `-` sides of the raw diff -- which cannot tell a cosmetic change from a
behavioural one. That is precisely the weakness gita exists to fix, reproduced by
hand in the answer key. Corrected to `test_static_file`; re-scoring the stored
answers moves git 50% to 100% and gita 50% to 100%, so it cannot favour either.

### Next

Ten more capability tasks. Five resolves +/-18 points on recall; the gap is 17.
