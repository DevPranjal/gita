# Foundation Guide

Status: F0-F3 are implemented in 1.0.0. Independent F4 verification is unavailable.

## Why This Layer Exists

Git stores blobs, trees and commits. It does not assign a durable identity to a
function that changes its name or file. Repeatedly reconstructing that history
from line diffs is costly and easy to get wrong. gita derives reusable structural
evidence while leaving Git as the authority for source bytes and parentage.

## The Data Model

| Record | Meaning |
| --- | --- |
| Version | Immutable extracted representation with exact source digest, own content, signature, body and subtree evidence. Location-independent. |
| Definition | A version's parse-local name, containment, discriminator and exact byte/line bounds. |
| Occurrence | A definition at an exact commit, raw path, blob, object type and mode. |
| Lineage | A policy-qualified chain of accepted continuations. `eid1_` is rooted in sufficient genesis evidence; `oeid1_` records observed/uncertain provenance. |
| Link | Accepted, candidate, ambiguous or rejected evidence on an exact parent edge. Similarity is not accepted identity. |
| Event | Independent flags on a lineage transition, such as rename, move, body/signature edit, descendant-only change or gap. |
| Scope | A named first-parent history with explicit anchor and interpretation. |
| Generation | One immutable published scope view, pinned to a tip. Readers and cursors remain attached to it. |
| File state | Half-open revision intervals and complete bindings, including unchanged entities whose line positions shift. |
| Coverage | Unsupported entries, parse gaps, missing objects and omitted candidate work, not a guessed empty answer. |

Canonical, domain-separated identifiers use BLAKE2b-256. OIDs retain their Git
object format, and raw paths are stored as BLOBs rather than lossy decoded text.
Hash equality is checked against full evidence before it can support matching.
Repeatability alone does not prove semantic correctness.

## Build and Update

1. Resolve and pin a committed tip. Disable replacements and lazy fetch; reject
   active graft interpretation. Read length-framed objects and NUL-framed paths.
2. On an initial build, walk actual first parents to a root, bounded, shallow or
   missing-parent anchor. Preserve all ordered parent OIDs, including merge parents.
3. Inventory the anchor. Prefetch a bounded window of files into reusable isolated
   parse workers, then persist exact definitions and versions in Git order on one
   writer thread. Unsupported or failed parsing stays unknown.
4. Compare complete before/after populations, including entities in unchanged
   files. Freeze candidate graphs before assigning accepted links.
5. Write bindings, intervals, frontier, events, departures and coverage in one
   `BEGIN IMMEDIATE` transaction. Validate relational invariants before publication.
6. On an update, walk back only to the prior published tip, copy the persisted
   checkpoint and process the suffix. A same-tip request performs no parsing.
   Second-parent-only reachability is not valid continuation.

Policy or boundary changes and divergent histories require explicit rebuild.
Parse and match policies have separate fingerprints; operational worker count,
page size, deadline and output budgets do not define identity. Compatible cached evidence
can be reused without rechecking every old object's availability; source checking
is reported separately and `source()` verifies on demand.

## Strict Matching

- Unchanged files carry their established bindings forward.
- Unique same-locator definitions may continue under an already established
  enclosing identity, subject to duplicate and copy/swap guards.
- Repeated-name siblings, including overload declarations, can continue under
   the same established parent when their exact own content is globally unique
   on both sides; containers require exact complete subtrees. Hash-indexed lookups
   verify full payload equality, complete compatible populations and copy/swap
   guards. This same-location rule can distinguish overload signatures despite
   stub bodies. It never uses ordinal position or pairs leftovers by elimination.
   Links record the sibling counts and matching scope. Policy changes require an
   explicit rebuild; old published generations remain unchanged.
- Relocations require distinctive exact own/body evidence for leaves, full
  subtree evidence for containers, or a unique full-blob move for modules.
  Tiny stubs, retained copies and competing destinations cannot pass as unique.
- Remaining suggestions use three-token shingles and rational Jaccard scores.
  They remain candidate/ambiguous, even if one has the highest score.

A supported parse gap blocks unsupported uniqueness claims. A removed entity
reintroduced later receives a new identity. An observed anchor does not invent a
birth, but a later fully observed deletion can still be proven structurally.
Merge processing compares the first parent; it does not import side-parent lineage.

## Read-Only Queries

`open_index` opens an existing store read-only and immediately pins a snapshot.
It never traverses history to manufacture missing index results or rebuilds on read.
The session exposes `resolve`, `state_at`, `history`, `events_in`, `entities_in`
and `source`. Name resolution prioritizes typed ID prefixes, exact path-qualified
locators, then literal names. More than one match remains ambiguity.

```python
from gita.index.query import open_index
from gita.vcs.git import CommittedRepo

with CommittedRepo("/path/to/repository") as repository:
    with open_index(repository, scope="review") as index:
        page = index.resolve("YourFunction")
        if page["status"] == "unique":
            match = page["results"][0]
            state = index.state_at(match["eid"])
            history = index.history(match["eid"], order="oldest")
            if state["state"] == "present":
                source_bytes = index.source(state["occurrence"])
```

State distinguishes **present**, **removed**, **absent** and **unknown**. Being
outside a selected scope is an error, not absence. Source retrieval validates the
returned occurrence against generation membership and active state, then reads
the original committed blob and byte slice, never current worktree text.

Pagination cursors encode generation, context, selector, revision, ordering and
position. They are validated but are not authentication tokens. A cursor never
silently follows a newly published generation. Keep it until consumed or restart
the query if its generation has been explicitly collected.

## Storage and Failures

SQLite schema v2 contains the evidence, interpretation, commit, scope, generation,
membership, lineage, binding, interval, occurrence, link, event, departure and
coverage relations. WAL readers keep their old snapshot while a writer publishes
a successor. Exceptions and cancellation roll back the whole build invocation.
Published data is immutable; explicit GC protects active publications and retained
references. Schema objects and integrity are checked without automatic repair.

The main and scratch SQLite page caches each have a 64 MiB target. New stores
include an optional covering index for parse metadata; existing schema-v2 stores
without it still open unchanged. Worker count bounds the prefetch window, not
total process memory. No durability, collision, source-size or publication
checks are disabled for throughput.

Publication reserves conservative space for dirty pages and WAL growth, which may
refuse earlier than the final physical size would require. OS process startup and
cleanup are not hard real-time guarantees. Native process-tree memory and large-
repository performance still need F4 measurement.

| Indexed CLI exit | Meaning |
| --- | --- |
| 0 | Successful action; inspect coverage/status because successful can be partial. |
| 1 | Missing, not-live or ambiguous identity. |
| 2 | Invalid options, cursor, revision or scope selection. |
| 3 | Missing/incompatible/collected index generation, or F4 verifier unavailable. |
| 4 | Resource, I/O, permission, source availability or cleanup failure. |
| 5 | Corruption or invariant/contract failure. |
| 6 | Budget too small for a valid serialized response. |

## Ownership Map

| Files | Responsibility |
| --- | --- |
| [model.py](../src/gita/index/model.py) | Immutable contracts and canonical encoding. |
| [evidence.py](../src/gita/index/evidence.py), [worker.py](../src/gita/index/worker.py) | Exact extraction, fingerprints and process isolation. |
| [git.py](../src/gita/vcs/git.py) | Committed object access and legacy Git plumbing. |
| [store.py](../src/gita/index/store.py), [schema.py](../src/gita/index/schema.py) | Transactions, immutable generations, indexed relations and GC. |
| [builder.py](../src/gita/index/builder.py), [matching.py](../src/gita/index/matching.py) | Initial/incremental traversal, strict policy and event classification. |
| [query.py](../src/gita/index/query.py) | Pinned, bounded, read-only queries and exact source references. |
| [indexed.py](../src/gita/cli/indexed.py) | Indexed command validation, JSON envelopes, cleanup and token budgets. |
| [check.py](../verify/check.py) | Reproducible local manual checkpoints, including fresh-process CLI reads. |

## What Comes Next

F4 must independently verify stored/source invariants, evaluate labeled real
refactors and measure latency, memory, storage growth and end-to-end agent quality.
`gita index --verify` currently reports that the verifier is unavailable.
The foundation enables the larger review/trace/compare/handoff experience; it
does not yet implement a caller graph, anchored agent notes, automated conflict
resolution or an on-device model integration. Indexed history, identity, state,
source and file-entity queries are available through MCP; index management is
explicit and read-only queries never build on demand.