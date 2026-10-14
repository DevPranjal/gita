# Index-first Gita: specification set

**Status:** Draft for review, not implementation authorization.

**Date:** 2026-10-14

**Implementation update:** Only the first transitional flag increment is
implemented: strict `GITA_ENABLE_LEGACY=1`, indexed history by default, lazy
indexed import boundaries, and legacy API/CLI/MCP/browser/evaluation gates.
This does not complete any milestone or whole specification; legacy still ships.
S2 now has a bounded first engineering increment: deterministic authored Python
histories, independent AST/Git/lineage gates, five separately measured scenarios,
persisted failed attempts, owned-process sampling/cancellation, recovery and
serial/parallel/resume equivalence tests, plus a profiled bounded token-cache
optimization. See [verification tooling](../../../verify/README.md#offline-s2-qualification).
This is not full S2 qualification or completion of a milestone. S3 query parity,
independent **product** verification and endpoint comparison remain unimplemented.
Storage schema and matching rules are unchanged; the builder artifact fingerprint
changes and therefore requires explicit rebuilding of older interpretations.

**Decision:** Keep the persistent indexed product; retire query-time entity
reconstruction and the separate structural-review engine.

**Baseline:** Source/tests at `public` commit `8858639`, also shipped in
[`DevPranjal/gita` commit `3bfca49`](https://github.com/DevPranjal/gita/commit/3bfca49a749fe2346aedef2c3ea189e5aaadf246).
These documents do not claim either repository has already migrated.

## Specifications

| Spec | Question answered | Deliverable |
| --- | --- | --- |
| [S1: Safe retirement](01-safe-retirement.md) | How do we remove the old product without damaging indexed behavior, data, or shared parser rules? | Dependency separation, deliberate breaking-change policy, migration gates, package/API retirement, rollout and rollback. |
| [S2: Index qualification](02-index-qualification.md) | How do we establish correctness and reliability, measure performance, and optimize without weakening identity? | Fixture/oracle design, benchmark manifest and results contracts, failure campaigns, instrumentation, baseline gates and optimization sequence. |
| [S3: Indexed product contracts](03-indexed-product.md) | What should the unified product expose, and what is still missing? | Product workflows, CLI/Python/MCP parity, entity queries, lifecycle UX, verification, browser boundaries, and optional genuine endpoint comparison. |
| [S4: Indexed review evaluation](04-review-evaluation.md) | How should the harness evaluate indexed Gita on AACR-Bench and ReviewBench without confusing review quality, proxy savings, and index correctness? | Harness audit, indexed/baseline/history arms, scorer conformance, leakage tests, statistical decisions and proposed success targets. |

The [keep/retire report](../../INDEXED-PRODUCT-REPORT.md) records the preceding
analysis. These specs turn it into testable product and engineering requirements.

## How to interpret requirements

- **MUST** is a release gate. **SHOULD** is recommended; an exception requires a
  documented rationale. **MAY** is optional.
- **Current** means observed in the cited baseline, not a new guarantee.
- **Proposed** means implementation is required. Command examples in S3 describe
  the target interface, not commands to run against 1.0.0.
- Numerical budgets and regression allowances in S2 are **proposed engineering
  policy**, not measured performance claims. Initial correctness-gated smoke
  observations are recorded in [the verification guide](../../../verify/README.md);
  they do not complete the specified statistical or real-repository campaigns.
- Every epic has an ID scoped to its spec. Dependencies are explicit; document
  order is not a dependency.
- No spec authorizes rewriting Git history, changing repository visibility,
  deleting user files, running paid models, downloading a corpus, or publishing.

## Shared release contract

1. Tree-sitter extraction and lineage matching run during explicit index work.
2. Reads use published indexed evidence; missing evidence never triggers a
   hidden build, reparse, similarity match, or network fetch.
3. Git owns source bytes and ancestry. Gita owns derived entity evidence.
4. Identity is qualified by interpretation context; names and paths are selectors.
5. Events and endpoint comparisons are different operations.
6. Partial coverage, uncertainty, observed boundaries, and missing source remain
   visible and machine-readable.
7. One indexed application/query contract serves CLI, Python, MCP, and any UI.
8. Correctness and durability gates precede throughput optimization.

## Cross-spec delivery sequence

| Milestone | Required work | Exit |
| --- | --- | --- |
| M0: Evidence baseline | S1-E1, S2-E1 | Inventory, reference fixtures and baseline manifests frozen. |
| M1: Decoupled foundation | S1-E2, S3-E1 | Index imports no legacy review pipeline; policy changes require explicit compatible handling/rebuild. |
| M2: Usable indexed-only core | S3-E2/E3, S1-E3 | Unified queries and CLI/MCP parity pass; retired commands cannot reach old code. |
| M3: Qualified candidate | S2-E2/E3/E4, S1-E4, S3-E4 availability gate | Correctness, failure, migration, package, and benchmark gates pass; available verification levels declared honestly. |
| M4: Release candidate | S1-E5, S3-E5 | Public docs/tools/UI consistent; verifier availability explicit; results reproducible. |
| Later capability | S3-E6, selected S2-E5 optimizations | True endpoint comparison and performance improvements land independently behind their own gates. |

S2-E1 can begin before API refactoring. S2-E2/E3 can qualify current internal
primitives before S3 adapters exist. S3-E4's independent verifier can develop
alongside API parity. Do not wait for a dashboard or endpoint diff to prove the
core identity store correct.

## Release recommendation

Ship indexing, identity resolution, history/events, state, source, file entity
listings, event patches, and lifecycle controls first. CLI/Python plus a tested
optional-install MCP extra are the required interfaces; an indexed browser is
secondary.

Use a breaking release with explicit migration notes. Do not retain the old
engine for compatibility, and do not describe old diff-compression results as
validation of the new indexed product.

Required release scope is S1-E1 through E5, S2-E1 through E4, and S3-E1/E2/E3/E5
with the truthful-availability portion of S3-E4. Completion of independent V0-V3
verification, S3-E6 endpoint comparison, and S2-E5 optimizations are separately
gated follow-ups. The optional browser may be omitted only if its entry points,
assets and documentation are removed consistently from that release; it cannot
remain advertised but broken. MCP installation remains optional for end users,
but correctness of its advertised tools is a release gate.

## Review decisions

Defaults are selected so implementation can proceed after review:

| Decision | Default |
| --- | --- |
| Old diff/show/expand behavior | Removed in the breaking release; temporary diagnostic tombstones only. |
| Missing index | Refuse with exact build guidance; never build on read. |
| Public `diff` in the first release | Omit unless S3-E6 passes; never alias range events to diff. |
| Existing incompatible store | Preserve and refuse; explicit rebuild where schema compatibility permits. |
| Initial performance claims | No universal SLA; publish named hardware/corpus results. |
| Verification | Keep explicit unavailability until independent scope-specific verification passes. |
| Automatic background indexing | Deferred; explicit build/update is the first-release contract. |
| Research tooling | Separate from runtime imports and product qualification. |
