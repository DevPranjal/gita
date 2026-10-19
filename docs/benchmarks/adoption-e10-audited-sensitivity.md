# E10 AUDITED-SENSITIVITY — not new official scores

This is an explicitly labelled, arithmetic-only counterfactual using the
[existing novel audit](adoption-e10-novel-audit.md). **Original automated all-five
results remain unchanged.** No classification file, score file, correspondence,
or gate decision was replaced.

The pinned official resolver and scorer were executed directly on in-memory
copies of the existing five task rows per arm. The unmodified full aggregate
exactly matched existing `quality.json` for both arms. Scorer and resolver hashes
match the repository's pinned identities. The [JSON companion](adoption-e10-audited-sensitivity.json)
records those hashes, input SHA256, exact keys, full-precision ratios, and limits.

## All-five macro precision/recall

Percentages below are rounded for presentation; JSON retains scorer precision.

| Scenario | Arm | Grounded P | Grounded R | Augmented P | Augmented R | Novel TP count |
|---|---|---:|---:|---:|---:|---:|
| Original auto all-five, unchanged | B | 73.333333% | 10.124459% | 75.000000% | 14.430863% | 3 |
| Original auto all-five, unchanged | H | 80.000000% | 9.172078% | 90.000000% | 16.391163% | 5 |
| Known FP flip; uncertain optimistic | B | 73.333333% | 10.124459% | 75.000000% | 14.430863% | 3 |
| Known FP flip; uncertain optimistic | H | 80.000000% | 9.172078% | 83.333333% | 15.600649% | 4 |
| Known FP flip; uncertain pessimistic | B | 73.333333% | 10.124459% | 75.000000% | 14.430863% | 3 |
| Known FP flip; uncertain pessimistic | H | 80.000000% | 9.172078% | 76.666667% | 14.237013% | 3 |

After the known flip, the uncertain row spans H augmented P **76.666667–83.333333%**
and augmented R **14.237013–15.600649%**. B is fixed. In the pessimistic endpoint,
H augmented recall is below B; in the optimistic endpoint, it is above B.
These are conditional endpoints for this one unresolved row, **not statistical
confidence intervals or guarantees against other judge errors**. No gate is approved.

## Exact changes and existing citations

### Known bad credited consequence: N06

- Task: `0083e3b9d289cf65d718cf844d29bac7df57f1cd603e32f48f2be5136caba41f`
- Arm **H**, local candidate **0**, original global candidate **5**.
- Existing score pointer: `/H/2/classifications/1`.
- Both sensitivity endpoints change only this row's `tp_fp` from TP to FP,
  then additionally vary N04 as specified below. This is not a persistent relabel.
- Existing audit: `adoption-e10-novel-audit.json#/decisions/5`.

Publication-ready guard explanation, from the existing audit only:
loading failure returns no model and logs a warning
(`src/protrider/pipeline.py:89-91`); fallback repopulates the latent dimension
(`450-491`); the still-existing file makes the loaded-model flag true and disables
training (`508-512`); saving is nested in that disabled branch (`538-549`).
Thus the credited normal failure-path overwrite does not follow.

### Uncertain runtime semantics: N04

- Task: `0b0800ed5659ae4f24efd65819c3ecd550f8a1f61f054b894ed5fee8fec83871`
- Arm **H**, local candidate **0**, original global candidate **2**.
- Existing score pointer: `/H/0/classifications/0`.
- Optimistic: leave TP. Pessimistic: change to FP in memory only.
- Existing audit: `adoption-e10-novel-audit.json#/decisions/3`.
- Existing source citations: `packages/pg/lib/query.js:122-133`,
  `packages/pg/lib/client.js:721-734`,
  `packages/pg/lib/diagnostics.js:8-18`.

**Runtime verification handoff; verdict remains uncertain.** The relevant built-in
module is `diagnostics_channel`, API
`TracingChannel.traceCallback(fn, position, context, thisArg, ...args)`.
The repository creates `dc.tracingChannel('pg:query')` and calls:

```text
queryChannel.traceCallback(
  (tracedCb) => { query.callback = tracedCb; enqueue(); },
  0, context, null, enrichedCb
)
```

The enrichment callback forwards `err` through `return origCb(err, res)` without
throwing. The original candidate claims ordinary error-first callback failures
therefore do not publish on the tracing `error` channel, causing a failed-query
error-channel test to time out and subscribers to miss the failure.

The repository's relevant runtime constraint is feature-based:
`typeof dc.tracingChannel === 'function'`. Module access uses
`process.getBuiltInModule` when available, otherwise `require`.
**The existing audit did not establish an exact deployed Node version/patch.**
Do not confuse this with the separate scorer tooling requirement of Node 22.19+.
The remaining verification question is whether that pinned runtime publishes the
tracing error event for a non-throwing callback's first Error argument or only
for thrown exceptions. No fetch, runtime probe, or verdict change was performed.

## Denominators and reproducibility

Pinned scorer commit: `c17b05be9c90b85a1fc012ce39e455e2d09a7255`.
Execution used its `resolveForScoring`, `scorePR`, and `aggregate` implementations,
not an approximation of their formulas.

For each task:

- Grounded P = matched TP candidates / matched candidates.
- Grounded R = uniquely covered reference TPs / reference TPs.
- Augmented P = (matched TP candidates + unmatched TPs) / all candidates.
- Augmented R = (uniquely covered reference TPs + unmatched TPs) /
  (reference TPs + unmatched TPs).

A zero denominator yields null. Macro metrics average non-null per-task ratios;
**all four metrics have five non-null contributors per arm in every scenario**.
They are not pooled micro ratios. Both arms retain 11 candidates and 64 reference
TPs across the five tasks. Only unmatched TP/FP status varies; correspondence,
reference labels/coverage, severity/category, task membership, and B are fixed.

Consequently grounded P/R cannot move under these flips. Augmented R changes
both numerator and denominator when a novel TP becomes FP. Novel TP counts are
counts across tasks, not percentages.

No additional corpus analysis, models, network calls, runtime verification,
reviewer reruns, or source changes were performed. All original audit
uncertainties remain applicable.
