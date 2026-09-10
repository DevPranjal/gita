# gita — first principles

**gita is the layer that lets software agents understand changes and their history, without repeatedly reconstructing that understanding from files. Git remains the source of truth.**

What this product is for, and why. Written 2026-09-04; revised 2026-09-11.
Measurements, design goals and commercial hypotheses are distinct: evidence for
one is not proof of the others.

The tactical list of "features to reach parity with git" lives in
[ROADMAP.md](ROADMAP.md). This document is the layer underneath it: the model
everything should be built on. Where the two disagree, this one wins.

---

## 0. The thesis

Git's genius was never the CLI. It was the **object model** — blob, tree,
commit. Content-addressed, deterministic, cheap to verify. Everything valuable
that followed (GitHub, CI, code review, bisect) was built on that model, not on
the porcelain. The porcelain is famously bad and it did not matter.

gita began with commands on git's object model. A narrow tool can be valuable;
more Git coverage or more commands do not, by themselves, make a product
sellable. Breadth matters only when it completes a workflow customers care
about. Our opportunity is to reduce repeated investigation and mistakes when
changing software, not to match Git command for command.

**The proposed foundation is an entity-level model built on top of Git.**

| git | gita |
| --- | --- |
| blob — bytes | **entity** — a named thing with durable identity |
| tree — a directory | **version** — an entity at one point in history, four hashes |
| commit — a snapshot | **event** — a typed transition between two versions |
| *(none)* | **edge** — a relation between entities, with supporting evidence |
| *(none)* | **note** — a durable fact anchored to an entity |

Every feature in this document is one of five operations over that model. That
is the test for whether something belongs in gita at all.

> Git preserves repository content and history as the source of truth.
> gita derives reusable structural context, with traceable evidence and explicit
> uncertainty.

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
- a long history in a busy repo — impractical to scan for every question

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

### P2: Evidence before assertion

*Our promise is traceable evidence and explicit uncertainty, not infallibility.*

Determinism means a computation is repeatable, not that its conclusion is
correct. A repeatable matcher can repeatedly connect the wrong functions.
Parsing and hashing provide structural evidence; they do not prove logical
identity, behavioral equivalence or program correctness.

Every answer should expose its source, scope and limitations. Distinguish direct
observations from inferred relationships, preserve the matching evidence, and
report ambiguity rather than silently selecting an identity. For example,
`signature_hash` inequality establishes that the normalized signature
representation differs; it does not by itself establish that a caller breaks.

**Generates:** inspectable change classification, evidence-backed review,
conflict triage, and checks with explicitly bounded guarantees.
**Forbids:** presenting heuristic matches as verified identity, hiding missing
evidence, or treating a structural comparison as proof that a change is safe.
The boundary between suggestions and verified facts matters more than the
technology used to produce them; product scope choices are discussed in §5.

### P3 — History is a database, not a log

*Git makes you scan. Every question costs a walk (§1.2).*

The entity index is not an optimisation. It is the thing that makes the other
principles expressible. A fact you cannot look up in constant time is a fact you
do not really have.

**Generates:** the index, the query layer, ownership and API timelines.
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

## 3. The core capabilities

These are capabilities, not a calendar-based roadmap. Shared foundations make
them easier to build, but each capability must earn its place through a useful
workflow and measurable evidence. Validate actual dependencies rather than
assuming every earlier capability must be complete before exploring the next.

### Identity and the index

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
the foundation needs rethinking.

### Structured queries and merge analysis

**`gita query`** — structured, not natural language. "Signature changes to
exported entities since v2.0." "Every entity this branch touches that `main` also
touched." Queries should return the indexed evidence quickly and state the
history, language support and identity assumptions they cover.

**`gita merge`** — structural merge analysis, building on ROADMAP §5.4 and the
identity model. Present the base and both branches, distinguish same-entity from
different-entity edits, expose matching evidence for renames, and flag equal
normalized content as a candidate for cosmetic review. These observations help
triage a conflict; they are not verdicts that the result is safe.

**Hash equality does not prove a merge is safe.** It compares the representation
being hashed, not program behavior. Different functions can interact even when
their edits do not overlap, and normalization may omit information relevant to
tools or execution. Structural evidence must not authorize an automatic
resolution on its own. Start with read-only analysis; any future resolution
mechanism needs its own bounded correctness contract, validation and approval.

**Kill criterion:** if fewer than 30% of git's conflicts across 100 real merges
can be usefully classified with independently checked evidence, the
classification is not earning its complexity. Report false classifications
separately; classification coverage is not a merge-safety success rate.

### The graph: edges and impact

Caller and test edges, with **honest resolution kinds**: `exact`, `imported`,
`heuristic`, `unresolvable` — and `unresolvable` stays a first-class answer, per
P2. A graph that lies is worse than no graph.

**`gita impact <entity>`** — what breaks and what to re-test. This is the
question agents most want answered and the one gita refuses today. The output
must distinguish observed dependencies from possible impact and unresolved
edges. A confidently wrong blast radius can contribute to a production incident;
correctness and uncertainty matter here just as they do for identity and merge
analysis.

**Kill criterion:** spike against coverage-per-test as a mechanical answer key
*before* building. Recall below 0.8 or precision below 0.5 and we stop.

### Memory: the compounding layer

**`gita note`** — durable facts anchored to entity identity, surviving the
refactor. Constraints, rationale, review decisions, incident links. Surfaced
automatically whenever an agent touches the entity, and enforceable in CI as a
gate ("this entity is marked allocation-free; this change adds an allocation").

This is where a repository stops being a pile of files with history and starts
being an institution that remembers. It is also the strongest commercial
position in the document: switching cost rises every week it is used, and no
competitor can ship it retroactively.

**Kill criterion:** if fewer than 20% of notes are still attached to a living
entity after six months of real use, identity is not durable enough and the
foundation failed quietly.

### Coordination: many agents, one repository

The bottleneck when N agents work in parallel is not writing code, it is
integration. Line-based merge fails on formatting; human review does not scale to
machine output volume.

Entity-level **claims** (this agent is working on these entities), collision
prediction *before* the work is done rather than at merge time, and structural
review that a machine can inspect. This is especially speculative: it depends on
reliable entity addressing and evidence that coordination helps real parallel
workflows. It does not require treating every other capability as a prerequisite.

---

## 4. What could make this valuable

The current product is a better `git diff`. It saves roughly 15% of round trips
on changes it is good at — real, measured, and modest. That may be valuable in a
frequent or expensive workflow. The result does not establish willingness to
pay, and neither does adding more commands or broader Git coverage.

The capabilities above suggest three possible sources of customer value, not
three guaranteed commercial outcomes:

1. **A faster, inspectable answer.** Identity and indexed history can reduce
   repeated investigation. Measure the complete workflow, not command count or
   isolated query speed.
2. **Better-informed changes.** Merge and impact analysis can surface evidence
   that helps developers and agents make decisions. Test whether this reduces
   review effort or mistakes, without claiming to guarantee safety.
3. **Reusable context.** Memory and coordination can carry decisions between
   people and agents. Value depends on that context remaining relevant,
   trustworthy and used.

Choose features by the workflow they complete and the evidence that customers
benefit. A valuable narrow tool is preferable to broad coverage without a
compelling use. An entity-level model is a means to deliver that value, not
proof of a business by itself.

---

## 5. What we will not build, and why

Recorded so the arguments do not have to be had again.

**Embedding-backed commits and semantic search — outside the current scope.**
Git remains the source of truth; embeddings must not define identity or silently
establish factual relationships. A future search layer could suggest candidates
if it labels them as suggestions and links to inspectable evidence. This is a
scope decision, not a claim that deterministic alternatives cannot be wrong.

**Model-written merge resolutions — rejected.** Structural conflict analysis
is in; automatic resolution is outside the current scope. Neither a model's
suggestion nor a hash comparison establishes that merged code is correct.
Preview and evidence first, with unresolved questions made explicit.

**Natural-language query — outside the current scope.** `gita query` is
structured so agents can issue inspectable requests. A future natural-language
interface would need to expose the translated query and keep suggestions
separate from the evidence returned. It is not forbidden by P2, but it must
justify its additional complexity.

**Shadow branches and speculative concurrency — declined.** Running fifty
branches and promoting a winner is an orchestration product. It reuses none of
the object model and the hard parts are not version control.

**Telemetry embedded in commit objects — declined.** A CI policy engine in a VCS
costume. The hook surface gets the useful half far more cheaply.

**Rollback as inverted migration — deferred.** Requires understanding what a
schema change *means*, which is past what a syntax tree can support. Revisit only
if the graph's edges prove solid.

**Churn-based risk scoring — deprioritised on evidence.** Measured at 3x
concentration (§1.3), not the 10x the idea implies. Too thin to headline.

---

## 6. The immediate consequence

The next thing to build is not on the ROADMAP list.

It is **durable identity, persisted**, specifically the continuity missing in
§1.1. That controlled example shows the value of preserving detected rename and
move links; it does not establish that every inferred link is correct.

Validate identity accuracy and ambiguity handling before relying on those links
for downstream decisions. Build the smallest capability that improves a real
workflow; a new object model or a longer command list is not the outcome itself.

Two things stay true regardless of how far these capabilities develop. Each gets
measured against the naive version of itself, the same way the diff engine was —
that habit found four defective answer keys and a confound in our own control
arm, and it is worth more than any single feature. And every claim shrinks when
evidence arrives: 28.6% became 18.9% became 14.9%. Plan for the numbers above to
get smaller too.
