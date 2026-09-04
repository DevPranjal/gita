# gita — first principles

What this product is for the next five years, and why. Written 2026-09-04 on
`main`, with the measurements that justify it.

The tactical list of "features to reach parity with git" lives in
[ROADMAP.md](ROADMAP.md). This document is the layer underneath it: the model
everything should be built on. Where the two disagree, this one wins.

---

## 0. The thesis

Git's genius was never the CLI. It was the **object model** — blob, tree,
commit. Content-addressed, deterministic, cheap to verify. Everything valuable
that followed (GitHub, CI, code review, bisect) was built on that model, not on
the porcelain. The porcelain is famously bad and it did not matter.

For two years gita has been building *commands on git's object model*. That is
why it is a utility and not infrastructure, and it is why it does not sell.

**The transformational move is to ship gita's own object model.**

| git | gita |
| --- | --- |
| blob — bytes | **entity** — a named thing with durable identity |
| tree — a directory | **version** — an entity at one point in history, four hashes |
| commit — a snapshot | **event** — a typed transition between two versions |
| *(none)* | **edge** — a proven relation between entities |
| *(none)* | **note** — a durable fact anchored to an entity |

Every feature in this document is one of five operations over that model. That
is the test for whether something belongs in gita at all.

> Git stores files and reconstructs meaning on demand, by guessing.
> gita computes meaning once and stores it.

---

## 1. What we measured before writing this

Three probes, run today. Scripts kept in `scratch/probe_*.py`.

### 1.1 gita is currently *worse than git* at its own headline property

The entity docstring claims `body_hash` gives "identity under rename". I built a
repo with known ground truth: one function, born in `auth.py`, body edited,
renamed, moved to `security.py`, edited again. Five commits, all touching one
function.

| tool | events found |
| --- | --- |
| `git log --follow` | **5 of 5** |
| `git log -L :check_token:` | 5 of 5 |
| `gita history check_token` | **2 of 5** |

gita reports `added` at the move and loses everything before the rename. Its
history is **name-anchored**, so it breaks precisely at the moment you most need
it.

The engine is not the problem. Diffing each commit individually, gita correctly
reports `renamed  validate_token -> check_token` and
`moved  auth.py -> security.py`. **It computes the identity link four times in a
five-commit repo and discards it every time.**

That single fact is the whole argument for what follows. The primitive exists.
There is nowhere to put it.

### 1.2 History-scale questions are not interactive

Walking real Flask history costs **100–180 ms per commit**, because every
question re-parses every file from scratch.

- 150 commits — 28 seconds
- 1,000 commits — ~3 minutes
- a year of a busy repo — unusable

No product can be built on a substrate where every question costs a full walk.
This is not a performance nit; it is the reason the query layer does not exist.

### 1.3 An honest negative: churn concentration is weak

Over 150 Flask commits — 443 entities, 666 material changes — the top 5% of
entities carry **16%** of all change. That is 3x over-representation, not 10x.

So "risk hotspots from entity churn" is a real signal but a **thin** one, and I
am not going to build a product on it. Recorded here so nobody rediscovers it
optimistically in eighteen months.

Change-kind mix, for calibration: body 32%, added 23%, removed 22%, signature
18%, moved 4%, renamed 1%.

*(Method note: the first version of this measurement paired each commit with its
neighbour in log order, which is wrong across merges, and produced a headline of
"106 changes to one function" — a pure artifact. Cross-checking against git's
file-level count caught it. Corrected numbers above.)*

---

## 2. The principles

Five. Each is meant to be **generative** — it should produce features and, more
usefully, forbid them.

### P1 — Identity over location

*Git tracks where code lives. gita tracks what code is.*

A path is not an identity. A line number is not an identity. Neither survives the
refactor that agents perform constantly. Every durable capability descends from
assigning an entity a stable identity and preserving it across rename, move,
split and merge.

**Generates:** true history, entity blame, ownership, anchored memory, semantic
merge — all of which are *joins on the identity*.
**Forbids:** anything keyed on path or line, because it will break on contact
with a rename.
**Status:** computed per-commit today, then thrown away (§1.1).

### P2 — Computed, never inferred

*Every fact gita states must be derivable by parsing and hashing.*

This is the commercial differentiator, and it becomes more valuable, not less, as
everything around it becomes probabilistic. In a world where every other tool
might be confidently wrong, being the one component that cannot be is a position
worth defending.

The discipline is already in the codebase: `signature_changed` is not a guess, it
is `signature_hash` inequality. Extend that everywhere.

**Generates:** provable conflict classification, verifiable review, safety gates
you can put in CI without a human double-checking them.
**Forbids:** embedding search, model-written merge resolutions, natural-language
query, "probably related" anything. Discussed further in §5.

### P3 — History is a database, not a log

*Git makes you scan. Every question costs a walk (§1.2).*

The entity index is not an optimisation. It is the thing that makes the other
principles expressible. A fact you cannot look up in constant time is a fact you
do not really have.

**Generates:** the index, the query layer, ownership, API timelines, everything
in Year 2.
**Forbids:** features whose only implementation is "walk all of history at
runtime".

### P4 — The reader is a machine with a budget

*Context is finite and priced. Output is ranked and capped, always.*

This is the one principle gita already honours — `--budget`, noise filtering, the
never-larger-than-`git diff` invariant. Generalise it: **every** command, not
just `diff`, must rank by relevance and cap by budget, and must say what it
withheld. The truncation notice was worth more than the compression.

### P5 — Write for the next agent, not the last human

*Commit messages are prose aimed at a reader who will skim. Agents cannot skim
and should not have to parse English to recover a constraint.*

The most expensive thing lost in a repository is **why**. Not what changed —
that is recoverable — but why it must stay that way. Today that knowledge lives
in review comments that vanish, commit messages nobody queries, and code
comments that go stale and lie.

Anchored to an entity with a durable identity, "this must stay allocation-free"
survives the rename, the move, and the rewrite. That is a category of memory
neither git nor any code-review tool can express — git notes attach to *commits*,
which is immutable and the wrong granularity entirely.

**Generates:** the note object, the rationale ledger, constraint enforcement.
**This is the moat**, because it is the only part of gita whose value compounds
with use, and the only part a competitor cannot ship by writing better code.

---

## 3. The five-year arc

Each year is a layer, and each layer is only buildable on the one beneath. The
ordering is not a preference; it is a dependency graph.

### Year 1 — The substrate: identity and the index

**Ship:** a persistent, incremental, content-addressed entity index in
`.git/gita/`, holding every entity, every version, and every event, with
**durable identity maintained across renames and moves at write time** rather
than recomputed and discarded (§1.1).

`gita id <entity>` returns a stable identifier that outlives the function's name
and its file. Everything after this is a join on that identifier.

**Sold as:** speed. Users experience an index as "history questions are now
instant". The data model is the product; the speed is the packaging.

**Kill criterion:** warm queries under 100 ms on a 10,000-commit repository, and
identity that survives a rename-plus-move on a corpus of 100 real refactors with
recall above 0.9. Below that, the identity is not durable enough to build on and
the whole arc needs rethinking.

### Year 2 — The query layer and provable merge

**`gita query`** — structured, not natural language. "Signature changes to
exported entities since v2.0." "Every entity this branch touches that `main` also
touched." Deterministic, exact, fast because of Year 1.

**`gita merge`** — the provable-conflict work from ROADMAP §5.4, now cheap
because identity is precomputed. Classify every contested entity as *cosmetic*
(content hash equal), *disjoint* (different entities, same file), *renamed*
(matched by identity), or *contested* (present three ways, resolve nothing).
Auto-resolve only what is proven, refuse the rest, and never write code.

Git flags conflicts when context lines shift. We can prove that class away by
hash. That is a checkable advantage, not a claim — and it is the single most
painful unsolved problem in the workflow.

**Kill criterion:** if fewer than 30% of git's conflicts across 100 real merges
are provably cosmetic-or-disjoint, the classification is not earning its
complexity.

### Year 3 — The graph: edges and impact

Caller and test edges, with **honest resolution kinds**: `exact`, `imported`,
`heuristic`, `unresolvable` — and `unresolvable` stays a first-class answer, per
P2. A graph that lies is worse than no graph.

**`gita impact <entity>`** — what breaks and what to re-test. This is the
question agents most want answered and the one gita refuses today. It is also the
first feature where correctness genuinely matters more than coverage: a confident
wrong blast radius is a production incident.

**Kill criterion:** spike against coverage-per-test as a mechanical answer key
*before* building. Recall below 0.8 or precision below 0.5 and we stop.

### Year 4 — Memory: the compounding layer

**`gita note`** — durable facts anchored to entity identity, surviving the
refactor. Constraints, rationale, review decisions, incident links. Surfaced
automatically whenever an agent touches the entity, and enforceable in CI as a
gate ("this entity is marked allocation-free; this change adds an allocation").

This is where a repository stops being a pile of files with history and starts
being an institution that remembers. It is also the strongest commercial
position in the document: switching cost rises every week it is used, and no
competitor can ship it retroactively.

**Kill criterion:** if fewer than 20% of notes are still attached to a living
entity after six months of real use, identity is not durable enough and Year 1
failed quietly.

### Year 5 — Coordination: many agents, one repository

The bottleneck when N agents work in parallel is not writing code, it is
integration. Line-based merge fails on formatting; human review does not scale to
machine output volume.

Entity-level **claims** (this agent is working on these entities), collision
prediction *before* the work is done rather than at merge time, and structural
review that a machine can check. This is the only layer that is genuinely
speculative, and it is deliberately last — it is worthless without Years 1–4, and
by then we will know whether the premise holds.

---

## 4. Why this is sellable when the current thing is not

The current product is a better `git diff`. It saves roughly 15% of round trips
on changes it is good at — real, measured, and modest. Nobody buys 15% of one
command.

The arc above changes the unit of sale three times:

1. **Year 1–2 — a faster answer.** Competes on quality. Weak moat, necessary
   foundation, drives adoption. Free and open, because the substrate has to be
   everywhere for anything above it to matter.
2. **Year 3 — an answer nobody else has.** Impact analysis is a team-level
   purchase and a genuine capability gap.
3. **Year 4–5 — an asset the customer accumulates.** Memory is not a feature they
   rent, it is data they build. That is the difference between a tool with churn
   and infrastructure with retention.

The honest read: **only Year 4 is a real moat.** Years 1–3 are good engineering
that a well-funded competitor could replicate. They exist to earn the right to
build Year 4, and to make sure that when we do, the identity underneath it is
durable enough to hold.

---

## 5. What we will not build, and why

Recorded so the arguments do not have to be had again.

**Embedding-backed commits and semantic search — rejected.** Directly violates
P2. The moment gita ships a feature that can be confidently wrong, every other
output becomes something a reviewer must verify, and the reason to prefer it over
grep disappears. This is the single most tempting suggestion and the most
damaging.

**Model-written merge resolutions — rejected.** Conflict *classification* is
provable and is in. Conflict *resolution* means writing code into a merge on a
guess. Preview yes, resolve never.

**Natural-language query — rejected.** `gita query` is structured. The agent
calling it can already translate English; making gita do it adds a guess to a
deterministic system and buys nothing.

**Shadow branches and speculative concurrency — declined.** Running fifty
branches and promoting a winner is an orchestration product. It reuses none of
the object model and the hard parts are not version control.

**Telemetry embedded in commit objects — declined.** A CI policy engine in a VCS
costume. The hook surface gets the useful half far more cheaply.

**Rollback as inverted migration — deferred.** Requires understanding what a
schema change *means*, which is past what a syntax tree can support. Revisit only
if Year 3 edges prove solid.

**Churn-based risk scoring — deprioritised on evidence.** Measured at 3x
concentration (§1.3), not the 10x the idea implies. Too thin to headline.

---

## 6. The immediate consequence

The next thing to build is not on the ROADMAP list.

It is **durable identity, persisted** — Year 1, and specifically the part proven
missing in §1.1. Everything in this document is a join on an identifier that gita
computes correctly, four times per five commits, and then throws on the floor.

Until that identifier exists and survives, every feature above is unbuildable and
every feature below it is a command rather than a capability.

Two things stay true regardless of how far up this arc we get. Every layer gets
measured against the naive version of itself, the same way the diff engine was —
that habit found four defective answer keys and a confound in our own control
arm, and it is worth more than any single feature. And every claim shrinks when
evidence arrives: 28.6% became 18.9% became 14.9%. Plan for the numbers above to
get smaller too.
