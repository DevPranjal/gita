# gita — working plan

> **git rebuilt for a world of agent coders**

Everything below must serve that. No feature exists to move a metric; each one
exists because an agent cannot do its job without it.

## Where we are (start of autonomous session)

Iteration 3, 60 runs, real credits:

| metric | git | gita |
| --- | ---: | ---: |
| credits / task | 42.11 | **38.60** (-8.3%) |
| turns | 3.87 | **3.30** |
| tool output | 45,997 | **3,421** (-93%) |
| wall clock | 160.9s | **72.2s** (-55%) |
| recall | 97% | **98%** |

Branch `feat/one-shot-context`. 283 tests. Not merged: three tasks regress.

## Cost model (learned the hard way, twice)

Credits per 1M tokens, Claude Opus 5: input **500**, output **2500**,
cache read **50**, cache write **625**.

- Cache reads are **54% of the baseline bill**. Volume beats unit price.
- Output is **5x** input. Making the model *write less* is worth more than
  making it *read less*.
- Never quote a token count as cost. Three readings of one run gave -15%,
  +3.8% and -8.3%. Only `eval/pricing.py` is authoritative.

## Priorities

### P0 — the three regressions (blocking merge)

| task | credits | symptom |
| --- | ---: | --- |
| flask-uncommitted | +25% | 5.3 turns vs 4.0; working-tree flow still wrong |
| gin-history | +25% | 4.3 turns vs 3.3; `gita history` walks commits serially |
| ripgrep-walker | +18% | recall **78%** — the only quality regression |

### P1 — maturity, in the sense that a platform team would demand

- Errors that tell an agent what to do next, never a bare traceback.
- Performance: `gita history` is O(commits) with a full parse each.
- Determinism: identical input must give byte-identical output, or prompt
  caching breaks for every downstream turn.
- Honest limits stated in `--help` and SKILL.md, not buried.

### P2 — only if evidence demands it

Blast radius (WS-2) is the largest missing capability, but it is only worth
building if the evaluation shows agents failing for want of it.

## Loop

1. Diagnose with data from `evals/runs/*/` — never from intuition.
2. Write the fix with a test that fails first.
3. Re-run the affected task in isolation before a full sweep.
4. Full 60-run sweep, record in `evals/RESULTS.md`, commit.
5. Update this file and `scratch/JOURNAL.md`.

## Rules for myself

- A change without a measured reason is not a change worth making.
- If a fix does not move the number, revert it and say so.
- Record failures in the journal as prominently as successes.

---

# Roadmap plan (2026-08-23)

Four workstreams remain. This is how each gets decided, built, and validated --
and what would make us abandon it.

## The constraint that shapes all of it

The harness resolves cost to roughly +/-13 points on 18 tasks. Anything whose
value shows up only as a cost saving under that threshold **cannot be justified
by our evaluation**, however good it feels.

That is not a reason to stop measuring. It is a reason to prefer work whose
value lands in the signals that *are* steady:

| signal | stability | what it can justify |
| --- | --- | --- |
| entity recall | high | correctness work, new fact types |
| turns per task | high | anything that removes a follow-up call |
| tool output tokens | very high | compression, roll-up, filtering |
| wall clock | deterministic | indexing, caching, plumbing |
| credits | low | only large effects, or many more tasks |

**Rule: a workstream must name which row it will move before it is started.**
If the honest answer is "credits, by a bit", it is not ready.

## Sequencing, and why

    WS-5 memory  ->  WS-2 blast radius  ->  WS-7 documents  ->  WS-3 narration
      enabler         the flagship          breadth            presentation

Not in README order. The reasoning:

- **WS-2 is the flagship** -- "what should I re-test" is the question gita
  currently refuses, and refusing it is the biggest gap between what an agent
  wants and what we give. It is also the only one that would change what gita
  *is* rather than how well it does what it already does.
- **WS-2 needs WS-5.** A caller graph over a range of commits means resolving
  every entity in every touched file, repeatedly. We already learned that
  asking is more expensive than parsing; a graph makes that worse. Build the
  index first or WS-2 arrives correct and unusably slow.
- **WS-7 is breadth, not depth.** It widens the set of repositories where gita
  is useful. Worth doing, but it does not unlock anything else.
- **WS-3 is last on purpose.** It touches no facts, so it can only improve
  prose. Doing it earlier would mean tuning presentation before the substance
  is settled.

---

## WS-5 Memory -- a persistent entity index

**The question it answers:** none, directly. It is the enabler.

**Why now:** `gita history` walks commits and parses files. Pruning and batching
took it from 12s to 2.9s, but the shape is still O(commits touched). A graph
query over history would multiply that.

**What we build.** We already have the right primitive: `TreeStore` keys parsed
trees by content hash. That is an in-memory cache of exactly the thing an index
would persist. The evolution is to move it behind a store interface with two
implementations -- process-local and on-disk -- keyed the same way.

    entity index:  (blob oid, path) -> parsed entity tree
    commit index:  sha -> [(path, blob oid)]  for touched paths only

Both are **derived state, and content-addressed**, which means the hard part of
caching is already solved: an entry cannot be stale, because different content
hashes differently. Invalidation is eviction, not correctness.

Storage: SQLite in `.git/gita/index.db`. Inside `.git` because it is derived
from the repository and should die with a clone deletion; SQLite because
concurrent readers are free and we already depend on nothing heavier.

**How we validate.** Not with an agent evaluation -- this is deterministic work
and an agent evaluation could not resolve it.

1. **Equivalence.** Every answer identical with the index cold, warm, and
   deleted mid-query. The existing `prune=False` / `batched=False` pattern
   extends here: `index=False` must produce byte-identical output.
2. **Speed, as a curve not a number.** `gita history <name> --limit N` for N in
   20/100/500 on five repositories. Cold and warm. The claim to beat: warm
   lookups sublinear in commits walked.
3. **Bounded footprint.** Index size as a fraction of `.git`. If it exceeds
   ~5% we are storing the wrong thing.
4. **Concurrency.** Two gita processes on one repository, one writing. No
   corruption, no lock timeouts on the read path.

**Kill criteria.** If warm history is not at least 5x faster than cold on a
500-commit walk, the index is not paying for its complexity -- delete it and
keep the in-memory store.

---

## WS-2 Blast radius -- real caller edges

**The question it answers:** *"I changed this. What can break, and what should I
re-test?"* Today gita refuses this, and the refusal is correct: `unreferenced`
is name matching, which finds dead code and nothing else.

**Signal it must move:** entity recall on a new task category, and turns. Not
credits.

**Spike first, and design the spike to fail cheaply.** Before building a
resolver, answer one question on real repositories:

> Given a changed entity, can static analysis name the set of tests that
> exercise it, with precision and recall we can state?

Ground truth is available mechanically and that is what makes this workstream
tractable: **run the test suite with coverage, per test**. Coverage tells us
exactly which tests touch which lines, and therefore which entities. That is a
real answer key, not a hand-curated guess.

    for each of ~50 changed entities across the corpus:
        truth   = tests whose coverage includes the entity's lines
        gita    = tests our resolver names
        measure precision, recall

**Kill criteria for the spike:** if recall is below 0.8 or precision below 0.5
on Python and Go, stop. A blast radius that misses a fifth of the affected
tests is worse than refusing, because an agent will trust it.

**What we build, if the spike passes.** The entity model becomes a graph. Today
an entity has a parent and children -- a tree within one file. Blast radius adds
edges *between* files:

    Entity                      unchanged
    Reference(from, to, kind)   new: call, import, subclass, instantiation
    EntityGraph                 new: edges + reverse index, built per revision

Two properties the design must hold to stay honest:

- **Edges carry a resolution kind.** `exact` (same file, unambiguous),
  `imported` (resolved through an import statement), `heuristic` (name match).
  These are not equal evidence and must not be presented as if they were.
- **Unresolvable is a first-class answer.** Dynamic dispatch, reflection,
  monkey-patching and DI containers defeat static resolution. The output says
  "3 callers found, 2 call sites unresolved" rather than implying completeness.

**Scope discipline.** Intra-repository only. Python and Go first -- both have
static, readable import systems. TypeScript third. Never cross-repository, never
runtime.

**How we validate.**
1. **The coverage answer key**, as above, run per language.
2. **New evaluation tasks** in a `retest` category: *"which tests should I run
   after this change?"* with ground truth from coverage. Both arms can attempt
   it; plain git can only guess, so this is where gita should separate.
3. **The refusal must survive.** A test that asserts gita still refuses when
   resolution confidence is low, rather than degrading to a guess.

---

## WS-7 Document semantics

**The question it answers:** *"Did this config or documentation change alter
behaviour, or only wording?"*

**Signal it must move:** recall on config-category tasks, and noise filtered.

**Where we are.** Markdown, YAML, JSON and TOML already parse into entities --
sections and keys. What we do not do is understand *values*. `timeout: 30` to
`timeout: 5` is a body change, indistinguishable from a comment reflow, and one
of those changes production behaviour.

**What we build.** Not natural-language understanding. Two concrete additions:

- **Typed value diffs for config.** Numbers, durations, booleans, URLs and
  version specifiers get compared as values rather than strings, so gita can say
  `timeout 30s -> 5s (6x lower)` instead of `body_changed`.
- **Reference edges from config to code.** A key that names an environment
  variable or a feature flag read somewhere in the repository is an edge, and
  the same graph WS-2 builds carries it.

**How we validate.** New config tasks with ground truth of the form "this change
alters behaviour" / "this change does not", drawn from real dependency and
settings commits. Recall must rise on the behaviour-altering ones **without**
the noise-filtered count falling -- if we start reporting reflows to catch value
changes, we have traded one error for another.

**Kill criteria.** If typed value diffing cannot beat the current structural
diff on a labelled set of 30 config commits, drop it. The existing hashes may
already be enough.

---

## WS-3 Local narration

**The question it answers:** none. It makes an existing answer easier to read.

**Signal it must move:** turns, if anything. Possibly nothing measurable.

**The rule that governs it.** The model never supplies a fact. It receives the
computed changeset and rewrites *one line* of prose. Facts, entity names, kinds
and counts come from the engine and are copied through verbatim.

**What we build.** A narration boundary with a hard contract:

    narrate(changeset, facts) -> str   # prose only
    verify(prose, facts) -> bool       # every fact still present, none added

The verifier is the actual deliverable. Anything the model emits that names an
entity not in the changeset, or a kind that does not match, is rejected and the
deterministic line is used instead.

**How we validate.**
1. **Fact preservation, adversarially.** Golden tests where the model is fed a
   changeset and the output is checked entity by entity. Any hallucinated name
   fails the build.
2. **Switched off must still be correct.** Every existing test passes with
   narration disabled, and the answer remains complete -- degraded prose, never
   missing facts.
3. **Only then, does it help?** An agent evaluation comparing narrated and
   plain summaries. Given the resolution limit, expect no measurable difference
   and be prepared to ship it off by default.

**Kill criteria.** If the verifier rejects more than 5% of narrations, the model
is not reliable enough at this size and the workstream waits for a better one.

---

## How the harness must grow alongside

Each workstream needs measurement that does not exist yet:

- **A `retest` task category** with coverage-derived ground truth (WS-2). This
  is the single most valuable addition, because the answer key is mechanical
  rather than hand-written.
- **A deterministic benchmark suite** separate from the agent evaluation, for
  work whose value is speed (WS-5). Agent evaluations cannot resolve wall clock.
- **Labelled config commits** for WS-7, split into behaviour-altering and not.
- **A fact-preservation harness** for WS-3, which is a property test rather than
  an evaluation.

And one thing that helps all four: **more tasks**. 18 resolves +/-13 points.
Roughly 40 would halve that. Every workstream is easier to justify against a
sharper instrument, and task-writing is cheap compared to being wrong about
whether a feature helped.
