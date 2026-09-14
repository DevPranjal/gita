# Entity-Tracking Foundation

**Start with [FOUNDATION.md](FOUNDATION.md).** This is the current detailed
implementation specification, drafted 2026-09-14. The broader product direction
and end-to-end workflow live in
[scratch/FIRST-PRINCIPLES.md](../../scratch/FIRST-PRINCIPLES.md).

## What We Are Building

A local, rebuildable entity-tracking foundation over committed Git history:

- Immutable entity versions, separate from their revision-specific occurrences.
- Exact source evidence, including byte spans when line positions shift.
- Accepted lineage links with recorded policy/evidence; candidates and ambiguity
  never silently become identity.
- Change events on exact parent edges, with branch-isolated historical state.
- Explicit indexing, incremental publication and opt-in indexed queries.

The first visible goal is one distinctive function across birth, body edit,
rename, move and body edit: one accepted lineage, five material events, correct
source at each commit, and the same result after restart. Negative cases are
equally binding: duplicate bodies must not fuse, parse gaps must stay unknown,
and another branch's edits must not contaminate the answer.

## Current Status

| Work | Status |
| --- | --- |
| [WS-1 enumeration](WS-1-enumeration.md) | Implemented previously; existing behavior remains covered by the full regression suite. This is Git plumbing, not entity identity. |
| [Foundation contract](FOUNDATION.md) | F0 contracts implemented and tested. Persistent-index behavior and measured release gates are not complete. |
| F0 | Complete as a contract checkpoint: 208 pure-contract checks and 13 fixture/assertion checks pass; five-event CLI acceptance is deliberately red. |
| F1 | Complete as the storage/evidence prerequisite checkpoint: schema-v2 relations, generation publication and GC, bounded offline Git access, byte-accurate parsing, isolated worker and parse-policy separation implemented and tested. Full-builder and release gates remain later work. |
| F2 | Started: independent event classification implemented. Scope traversal, strict lineage matching and automatic state construction are not implemented. |
| F3-F4 below | Not started. `gita index`, `gita id` and indexed history are still proposed commands. |

Latest full regression: **1,122 passed, 1 deliberately failed**, no skips, through
Unit Test MCP in 385.6s. The failure is the five-event CLI acceptance, separately
confirmed by exact test selection. This is not a release-ready persistent index.

Run the integrated checkpoints from the repository root:

```powershell
.venv\Scripts\python.exe verify\check.py --stage f1
.venv\Scripts\python.exe verify\check.py --stage f2-events
```

The F1 command passed five checks using a disposable real Git repository:
committed bytes despite a dirty worktree, publication/reopen, successor rollback,
published-generation GC refusal and explicit staging GC. The F2 command passed
four flag checks using explicitly supplied accepted endpoints; it does not infer
lineage. See [the verification guide](../../verify/README.md) and the
[delivery checkpoint](FOUNDATION.md#f1-completion-and-f2-start-2026-09-14).

**Compatibility:** schema-v1 evidence caches are refused and preserved, not
migrated or deleted. New stores use schema v2. The corrected parse fingerprint
also invalidates prior parse evidence. There is no public rebuild CLI yet.

The earlier focused evidence and worker checkpoints remain available:

```powershell
.venv\Scripts\python.exe verify\check.py --stage f1-evidence
```

All five checks passed: independent Python AST byte spans/digests, cache reopen,
location versus literal changes, persisted syntax-error coverage, and rollback
plus read-only guards. It retains fixtures in a unique temporary directory and
does not index or modify this repository. See [the verification guide](../../verify/README.md)
for limitations and [the F1 checkpoint](FOUNDATION.md#f1-evidence-checkpoint-2026-09-14)
for implementation boundaries.

The worker checkpoint uses one reused spawn worker with a five-second default
deadline, crash/cancellation cleanup and no timeout-dependent evidence IDs:

```powershell
.venv\Scripts\python.exe verify\check.py --stage f1-worker
```

All four manual checks passed. The 42 worker tests also exercise blocked I/O,
termination failure, late responses and transaction rollback; five additional
harness cases pass. OS process startup/reaping are not hard real-time guarantees,
and native memory limits remain unmeasured. See the
[worker checkpoint](FOUNDATION.md#f1-worker-checkpoint-2026-09-14).

## Implementation Order

| Phase | Deliverable | Depends on | Done when |
| --- | --- | --- | --- |
| **F0: Contracts and red acceptance** | Typed records, canonical IDs, independent vectors, distinctive five-commit fixture | Existing extraction and fixtures | Record contracts agreed; focused codec tests green; end-to-end acceptance deliberately red |
| **F1: Store and evidence** | Executable SQLite schema, safe transactions, byte-accurate extraction and Git access | F0 | Constraint/recovery tests and source-byte tests pass; store/evidence owners can work independently |
| **F2: Strict lineage builder** | Scoped checkpoint, conservative matching, file-state intervals, events and coverage | F1 | Five-event acceptance and ambiguity/line-shift/branch tests pass |
| **F3: Queries and incremental update** | Explicit build/id/history surface, persisted-frontier resume, immutable query generations | F2 | Incremental equals full logical export; reads are exact, bounded and compatible with legacy defaults |
| **F4: Verification** | Independent invariant checks, manual workflow, accuracy and performance evidence | F3; labels can start in F0 | Each gate has a pass/fail/unverified result; missing evidence is not a pass |

Detailed owners, file boundaries and ITEM-001 through ITEM-016 are in
[Foundation section 14](FOUNDATION.md#14-implementation-plan). These phases are
implementation checkpoints, not a calendar commitment or a claim that the full
product workflow is delivered by the index alone.

## Test-First Contract

Write one discriminating failing test before the behavior it requires. Run it,
make the smallest owning implementation change, rerun it, then check affected
legacy behavior. Existing green regression guards must not be reported as new
red-green evidence. Reuse existing test helpers and real disposable Git repos.

The foundation specification contains 34 proposed test cases, 25 acceptance
criteria, nine verification invariants and eight manual acceptance scenarios.
Every delivery handoff must include red/green evidence and a reproducible manual
check. Implemented commands must be visibly distinguished from proposed ones.

## Superseded Documents

Retain these for historical rationale and useful test ideas, not as current
implementation contracts:

| Document | Replaced assumption |
| --- | --- |
| [Earlier index design](../../scratch/SPEC-ENTITY-INDEX.md) | Events double as state; globally ordered commits; identity derived without sufficient uncertainty handling |
| [WS-2 store](WS-2-store.md) | Old schema, PID locks, timestamp-based checks, destructive error recovery |
| [WS-3 builder](WS-3-builder.md) | Persist the existing matcher's decisions verbatim; positional/fuzzy results become identity |
| [WS-4 queries](WS-4-query.md) | Transparent fallback to legacy history and global latest-event state |
| [WS-5 verification](WS-5-verification.md) | Physical-file determinism and insufficiently independent identity answer keys |

For conflicts, [FOUNDATION.md](FOUNDATION.md) governs the foundation. No old
checkbox or syntax-valid SQL sample is evidence that this revised model works.
The first-principles document remains authoritative for product direction.
