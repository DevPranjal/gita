# gita — roadmap to being worth installing

Written 2026-09-03 on `main`. The question this answers: **what do we build, in
what order, so that gita is something you would miss if it were removed?**

The short diagnosis: gita is excellent at one thing that turns out to be a small
fraction of what anyone does with git. That is why it does not sell.

---

## 1. The diagnosis, measured

Our own telemetry has recorded **1,467 real git invocations** during normal work.
Classified by workflow:

| workflow | share | does gita help? |
| --- | ---: | --- |
| read a file at some revision (`ls-tree`, `show rev:path`, `cat-file`) | **39%** | no |
| mutate or navigate (`add`, `commit`, `checkout`, `push`, `branch`) | 21% | no, and it should not |
| **read a change** (`diff`, `show`, `log`, `blame`) | **17%** | **yes — this is all it does** |
| check state (`status`, `rev-parse`) | 11% | partly |
| **compare branches** (`rev-list --count`, `merge-base`, `A...B`) | **7%** | no |
| other | 5% | — |

**gita addresses 17% of observed git usage.** Not because the other 83% is
uninteresting, but because we only ever built one verb.

A tool that covers one command in six is a utility, not infrastructure. Nobody
buys infrastructure that they have to remember to reach for.

### Two defects found while measuring this

1. **gita cannot parse `A..B` or `A...B`.** The CLI never splits range syntax, and
   a single positional argument means "that revision versus the working tree".
   So `gita diff main...feature` hands the whole string to git as one rev, git
   resolves it to *something*, and gita compares that against your working tree.

   Verified on this repo, clean but for two files:

   | invocation | result |
   | --- | --- |
   | `gita diff HEAD~5 HEAD` | 13 files, 109 changes, 358 noise filtered — correct |
   | `gita diff HEAD~5...HEAD` | 15 files, 493 changes, **no noise filtered** — wrong |
   | `gita diff totally-not-a-ref` | clean error |

   Note the failure mode: it does not error, it answers. The two extra files are
   the dirty working tree leaking in, and noise filtering is silently off. **This
   is the syntax that defines a pull request**, and pull-request review is the
   market we identified. A wrong answer on the critical commercial path.
2. **The observed branch-comparison traffic is pull-request work.**
   `rev-list --count origin/main..branch`, `merge-base origin/main branch`,
   `diff --name-only origin/main...branch`, `ls-tree branch path`. An agent
   answering "what does this branch do, and is it safe to merge". gita has
   nothing for any of it.

---

## 2. The rule for what we accept

Everything below is judged against four tests. A feature has to pass all four.

1. **Does it reuse the entity engine?** If it needs new infrastructure, it is a
   different product and should be sequenced as one.
2. **Is it deterministic?** gita's whole differentiator is that it never supplies
   a fact it did not compute. Anything probabilistic — embeddings, model-written
   resolutions, natural-language queries — destroys the one property nobody else
   has.
3. **Does it serve observed demand?** We now have the telemetry to check rather
   than guess.
4. **What kills it?** Every item needs a number that, if not reached, means we
   delete the code.

---

## 3. What I would reject from the advice, and why

The advice is good and most of it is accepted below. These parts are not, and the
reasons matter more than the verdicts.

**Vector-embedded commits and semantic search — reject.**
`gita query "where did we alter token validation"` requires embeddings, which
means a model, which means gita can be confidently wrong. Today gita's guarantee
is that every fact is computed: parsed, hashed, matched. That guarantee is the
product. The moment it ships a search that guesses, every other output becomes
something a reviewer has to double-check, and the reason to prefer it over grep
evaporates. If we ever want this, it belongs in a separate binary with a separate
promise.

**Model-driven conflict resolution — reject; conflict *classification* — accept.**
"Semantic code synthesis via local code-models" writes code into a merge. We can
prove a conflict is cosmetic by hashing, and we should auto-resolve exactly that
class and nothing else. Everything we cannot prove, we present and refuse. Same
discipline as `signature_changed` being computed rather than inferred.

**Shadow branches and speculative concurrency — defer, and probably never.**
Spinning up fifty branches and auto-promoting the winner is an orchestration
product. It reuses none of the entity engine, it is a very large build, and the
hard parts (running the test suites, judging the winner) are not version control.
Someone should build it; it is not us.

**Telemetry embedded in commit objects — defer.**
"Reject the push if memory rose 40%" is a CI policy engine wearing a VCS costume.
It shares no code with what we have. The *hook* surface (§5.6) gets us the useful
half of this at a fraction of the cost.

**Rollback-as-inverted-migration — defer.**
Genuinely valuable and genuinely hard: it needs to understand what a schema
change *means*, which is beyond a syntax tree. Revisit only if the caller graph
(§5.5) lands and proves solid.

---

## 4. What makes this sellable, stated plainly

Two things, and neither is "more commands":

- **Cover a workflow end to end, not a verb.** "Review this pull request" is a
  workflow: what changed, what breaks, what conflicts, what to re-test. Today we
  do step one of four.
- **Be reached for without being remembered.** Hooks and `status` mean gita is in
  the path of work the agent was doing anyway. Every feature below that an agent
  has to *choose* is worth less than one it *encounters*.

---

## 5. The build order

Sequenced by (observed demand x how much of the existing engine it reuses), with
the cheap-and-obvious first because momentum matters and because each one makes
the next easier to justify.

### Tier 1 — finish the verb we already have (days, not weeks)

**5.1 Range support — `A..B` and `A...B`.** *Bug, not feature.* Split range syntax
in the CLI before it reaches `diff_revisions`, resolve `A...B` through
`merge-base(A,B)..B`, and reject a range where a single revision is required
rather than falling through to the working-tree comparison. Add the "did not
error, answered wrongly" case to the test suite — that failure shape is the one
we care most about, and we did not have a test for it.
*Kill: none. This is a defect.*

**5.2 `gita status`, risk-classified.** Not a file list — "2 files body-changed
(safe), 1 signature change (interface-breaking, review before continuing)". Runs
where an agent already looks, costs one call to the existing engine, and is the
cheapest way to be encountered rather than remembered.
*Kill: if it does not change what an agent does next, it is decoration.*

**5.3 `gita review <branch>`** — the whole pull-request answer in one call:
entities changed against the merge base, interface breaks first, noise filtered,
conflicts with the target flagged. This is 5.1 plus framing, and it is exactly
the artifact a review product would embed.
*Kill: if it is not materially better than `gita diff main...HEAD`, drop the
command and keep the range support.*

### Tier 2 — the branch-comparison gap (1–2 weeks)

**5.4 `gita merge-preview <branch>`.** The advisor is right that this is the
highest-leverage unsolved problem, and it maps onto the four hashes we already
compute. Classify every entity touched on both sides of the merge base:

| class | how we prove it | what we do |
| --- | --- | --- |
| **cosmetic** | `content_hash` equal, `raw_hash` differs | auto-resolvable, proven |
| **disjoint** | both sides changed, different entities in one file | not a real conflict |
| **renamed** | matched by stable id across the rename | not a delete+add |
| **contested** | both sides changed the same entity's body | present three-way, refuse to resolve |

Git flags a conflict when context lines shift; we can prove that class away by
hash. That is a real, checkable advantage over `diff3`, not a claim.

*Kill: measure against 100 real merges from the corpus repos. If we cannot prove
at least 30% of git's conflicts are cosmetic-or-disjoint, the classification is
not earning its complexity.*

**5.5 `gita conflicts`.** Same output shape as `gita diff`, scoped to unresolved
entities, interface-breaking first. Cheap once 5.4 exists, and it is the command
an agent runs repeatedly mid-rebase.

**5.6 `gita rebase --plan`.** Precompute the conflict for every commit in the
replay so an agent can reorder or squash before starting rather than discovering
mid-flight. Pure reuse of 5.4.

### Tier 3 — be in the path of work (days each, high adoption value)

**5.7 `gita check` for hooks.** Wire into pre-commit and pre-push: block on an
unreviewed interface break, warn on a stale `AGENTS.md`, enforce a budget. This
is the item most likely to make gita load-bearing, because it runs whether or not
anyone chose it — and it is the same shape as the governance pitch, at the git
operation rather than the pull request.
*Kill: if teams disable it within a week, it is a nuisance, not a guardrail.*

**5.8 `gita reset --preview` / destructive-operation guards.** An autonomous agent
force-pushes with no human to flinch. Show which entities a reset or a force-push
would discard, before it happens.

**5.9 `gita catch-up <since-ref>`.** A long-running agent that has been away gets
an entity-level digest of what landed upstream, instead of re-reading everything.
Same engine, different framing, and specific to agents rather than humans.

### Tier 4 — the external surface (days each, direct commercial use)

**5.10 `gita changelog A..B`.** Public API additions, removals and signature
changes computed structurally rather than parsed out of commit-message
conventions. More accurate than every conventional-commits tool, and it reuses
the engine exactly.

**5.11 `gita describe`** — the structural pull-request description, generated
locally before pushing. This is the same content a hosted review bot would emit,
runnable standalone first, and it is the natural free tier that leads to the paid
one.

**5.12 `gita blame <entity>`.** Entity-level rather than line-level: "this
function's last *structural* change was commit X" rather than a wall of line
attributions.

### Tier 5 — needs new infrastructure (weeks; do not start before Tier 2 lands)

**5.13 Persistent entity index (WS-5).** Content-addressed, in `.git/gita/`. Not
a feature — the thing that makes Tier 2 fast enough on a large repository.
*Kill: if a warm run is not 5x faster than cold on a 500-commit walk, delete it.*

**5.14 Caller edges (WS-2), and only then `gita refs` / `gita blast-radius`.**
"What calls this, and what should I re-test" is the question gita refuses today
and the one agents most want answered. It needs real resolution with honest
kinds — `exact`, `imported`, `heuristic`, `unresolvable` — and "unresolvable"
must stay a first-class answer.
*Kill: spike first against coverage-per-test as a mechanical answer key. Recall
below 0.8 or precision below 0.5 and we stop.*

**5.15 Rationale ledger via git notes.** `gita commit --rationale` attaching
structured *why* at commit time, surfaced later through blame and history. The
"why" is what is lost today and what an agent most needs when it picks up a repo
cold. Cheap to build; its value depends entirely on 5.12 and 5.14 existing to
surface it.

---

## 6. What I would do first, if only one thing

**5.1 and 5.3 — range support and `gita review <branch>`.**

Not because they are the most interesting, but because gita currently gives a
*wrong answer* to the single question our chosen market asks every minute of
every day. Everything else on this list is an improvement; that one is a defect
on the commercial path.

After that, **5.4 merge-preview**, because it is the only item here that no other
tool does structurally, it is provably correct rather than heuristic, and it
turns gita from "a nicer diff" into "the thing that stops agents breaking merges".

---

## 7. What this does not fix

Being honest about the ceiling: none of this changes the measured effect size.
gita saves ~15% of round trips on the changes it is good at, and that is a real
but modest number. Breadth makes gita *reachable* — it does not make it
*dramatic*.

The dramatic claim, if one exists, is still the one from the control experiment:
**cutting tokens is not the objective, and the obvious implementation makes
things worse.** Every feature above should be measured against the naive version
of itself, the same way the diff engine was. That habit is the asset, not any
single command.
