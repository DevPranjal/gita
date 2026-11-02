# E10 novel-classification audit

**Conclusion: not reliable enough to certify the gate or justify 25-task scaling
from the current evidence.** Of eight existing unmatched TP classifications,
six are supported, one is contradicted by control flow, and one remains uncertain.
No existing label, decision, or score was changed.

## First clear bad classification

**N06: PROTRIDER, H local candidate 0 / original global candidate 5.**
The classifier credits retraining and overwriting an existing invalid checkpoint.
The save exists, but the stated failure path does not reach it:

1. Loading failure logs a warning and returns no model
   (`src/protrider/pipeline.py:89-91`).
2. Fallback initialization obtains a latent dimension and creates a fresh model
   (`src/protrider/pipeline.py:450-491`).
3. The still-existing checkpoint plus the populated dimension makes the later
   loaded-model flag true. Training is disabled
   (`src/protrider/pipeline.py:508-512`).
4. Saving is nested inside that disabled training branch
   (`src/protrider/pipeline.py:530-549`).

**Verdict: disagree; high confidence.** This is a reachability error in the
classifier's supporting proof, not merely wording disagreement. Other defective
load-failure behavior does not establish the particular destructive-overwrite
claim and was not substituted to rescue its existing TP. Unstated concurrent
external file deletion is outside the scenario.

## Scope and provenance

Eight unmatched TP decisions were inspected: **B=3, H=5**, across five tasks.
Original `classification-input.json` candidates and empty-match correspondence
entries established the blind/global indices; combined `score-inputs.json`
established arm-local indices. No reference messages were needed.

- Original-three protocol:
  `83fec8fd0ff5c0b021cae7b197faeb79f1ed18bd26345c6464d73fd8815e30dd`
- Transfer-two protocol:
  `b74eb2180e6736b2eace0b81d7634d4ca1aca3dce0aaca09cbf1fdbe24837e08`
- Final input IDs:
  `adjudications/e10-new2/score-inputs.json`,
  `adjudications/e10-new2/input.json`,
  `adjudications/e10-new2/classifications.json`.

The [JSON companion](adoption-e10-novel-audit.json) contains SHA256 **file-content**
hashes of these three final artifacts, exact source HEAD commits, score JSON
pointers, original global indices, per-decision source ranges, and qualifications.
All published artifact IDs are relative; no absolute locations are included.

## Decision results

All existing labels below remain **TP**. Verdicts concern truth and actionability,
not an alternative score. Pre-existing defects are eligible; introduction by the
PR was not required.

| ID | Task / arm / local candidate | Verdict | Confidence | Brief independent source proof |
|---|---|---|---|---|
| N01 | CrabTrap / B / 1 | agree | high | `useAuditLog.ts:68-84` resumes from an old entries snapshot after awaiting a page; SSE independently mutates the store at `52-61`. `auditStore.ts:33-60` replaces rather than atomically merges that state. A concurrent live row outside the fetched page can disappear from the view. |
| N02 | PROTRIDER / B / 3 | agree | high | `pipeline.py:32-37` omits feature identity; `69-87` accepts shape-compatible weights; `441-452` automatically reuses the checkpoint. Inference assigns current dataset labels at `576-592`. Same-shaped reordered features can silently receive wrong feature-specific weights. |
| N03 | LANdalf / B / 1 | agree | medium | `DeviceMonitoringService.cs:79-95` shares one scoped service across all parallel checks; status changes persist at `110-143`. `AppDbService.cs:7-14,30-35,50-51` holds and saves one context; `Program.cs:40-46` confirms registration. No serialization isolates concurrent continuations. |
| N04 | node-postgres / H / 0 | uncertain | medium | `query.js:122-133` supplies callback errors; `client.js:721-734` forwards them without explicit error publication. `diagnostics.js:8-18` delegates tracing to a built-in. The decisive built-in event contract was not independently inspected under the no-runtime/no-network constraint. |
| N05 | PROTRIDER / H / 1 | agree | high | `pipeline.py:65-80` obtains model presence behavior from saved metadata, but `495-502` obtains loss behavior from current config. `_inference` passes output directly to the loss at `576-584`; `model.py:151-159,208-226` establishes incompatible leading-dimension/indexing behavior. |
| N06 | PROTRIDER / H / 0 | disagree | high | The existing-file guard at `pipeline.py:508-512` disables the training branch containing save at `538-540`, contradicting the credited failed-load overwrite consequence. |
| N07 | chronicle / H / 0 | agree | high | `media.rs:55-68` permits an in-root parent even after an existing destination resolves outside the root. `79-89,150-152` then write or change permissions through that destination. The containment contract is violated. |
| N08 | LANdalf / H / 1 | agree | medium | Same actual shared-context and persistence path as N03. Validity does not depend on being newly introduced. This is a second arm's decision on one mechanism, not independent corroboration from a separate defect. |

Paths abbreviated in the table resolve to full repository-relative paths in the
JSON companion. There are **seven distinct mechanisms**, not eight independent
bugs.

### Explicit uncertainties and qualifications

- N01 establishes lost displayed state under a particular interleaving, not
  permanent database deletion.
- N02 requires changed feature meaning/order with compatible shapes and model
  architecture; numerical degradation was not measured.
- N03/N08 require overlapping continuations and changed statuses. The configured
  SQLite provider can complete individual operations synchronously, so failure
  is not guaranteed on every cycle. Concurrent access remains unisolated.
- N04's alleged test timeout and exact built-in error-event behavior were not
  verified. It is neither independently confirmed TP nor established FP here.
- N05's implementation uses a stacked tensor, not necessarily a Python tuple.
  The candidate's container wording is imprecise, but its configuration/output
  incompatibility remains actionable.
- N07 requires an existing symlink, suitable permissions, and a caller using that
  destination. Remote attacker reachability and the existing high severity were
  not independently established.

## What this means for the benchmark

The current novel-TP evidence contains at least one concrete classification
failure and one unresolved claim. A lower measured cost or higher augmented
precision/recall cannot establish a genuine quality gain when some credited
novel TPs have not survived source-level validation. Nor does this audit prove
the candidate is worse: **no metric was recomputed**, and the reported grounded
recall change was not independently recalculated.

Do not use these eight classifications as sufficient gate certification.
The bounded audit identifies a specific proof-quality gap: citing an operation
without following its controlling branch. It does not authorize choosing
favorable labels, changing the score, or tuning reviewers against references.

## Limits and unchanged state

This was a narrow static audit of the supplied unmatched decisions, not a broad
implementation review. The final LANdalf supplement was checked against actual
HEAD service and registration source. Published classifier evidence was already
visible, so the audit was not blinded and does not measure stochastic stability.
Reference completeness, semantic novelty against all references, severity
calibration, and aggregate deduplication were not independently audited.

No hidden reasoning, runtime corpus code, applications, tests, network requests,
paid model calls, or review reruns were used. No existing decisions, labels,
scores, source, tests, prompts, or pilot settings were changed. Only this report
and its JSON companion were written.
