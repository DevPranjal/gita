# S4: Evaluating indexed Gita on AACR-Bench and ReviewBench

**Status:** Proposed protocol and harness migration specification, not results.
**Date:** 2026-10-14.
**Product under consideration:** indexed Gita at `b64d43f`, legacy disabled.
**Companions:** [Index qualification](02-index-qualification.md),
[product contracts](03-indexed-product.md), [public-repository measurements](../../../verify/README.md).

## 1. Purpose, claims, and decision

Determine whether persistent entity evidence helps a fixed agent review code
correctly and efficiently, and identify what to change in the next Gita version.
This is not a repeat of the legacy diff-compression experiment.

The unit under evaluation is **agent + model + evidence tools + index preparation
policy**, not Gita as a standalone defect detector. Index correctness and resource
qualification are prerequisites; review agreement cannot prove an index correct.

Questions:

1. Does indexed entity/state/source navigation improve useful review findings?
2. Does persistent history add value beyond indexed snapshot navigation?
3. At comparable review quality, does the agent consume less actual model context
   or elapsed time, after accounting for index preparation?
4. Where do missing lineage, ambiguity, coverage, source access, query UX, or
   model capability limit the result?

No absolute recall, winning leaderboard placement, or token saving is predicted.
Section 10 specifies **proposed engineering success thresholds**, to freeze before
confirmation; they are not expected observations or claims already supported.

## 2. Source pins and benchmark semantics

Definitions inspected for this spec:

| Source | Pin | Use |
| --- | --- | --- |
| AACR-Bench | `f8367981425b15a42d473853b94dfe3731b6b24c` | Dataset and upstream evaluation definitions. |
| Historical Gita AACR dataset | `68a569759289a83654a59d06db2a72910edf0a4a` | Reproduction only; never silently mix with current data. |
| ReviewBench | `c17b05be9c90b85a1fc012ce39e455e2d09a7255` | Corpus, agent contract, matcher/scorer, labels and rubric. |

Before running, hash downloaded dataset/scorer/prompt files and resolve licenses.
An upstream `main` update creates a different protocol; do not automatically
upgrade an in-progress run. Dataset membership and label changes require a
diff/audit, not merely an updated URL.

### AACR-Bench

At the inspected pin, direct JSON counts are **196 positive task rows / 1,506
comments / 50 repositories** and **155 negative task rows / 639 comments /
49 repositories**. **151 exact PR/base/head tuples overlap the two files.**
The negative file contains rejected review comments, not a collection of proven
bug-free PRs. Do not count 351 independent PRs or treat every negative-file PR
as a no-finding task. Preserve label origin and deduplicate by exact task tuple.
Human-audit the negative-label meaning before using it as an FP challenge.

Keep two named scoring products:

- **AACR official-compatible:** pinned upstream converter, matching/line tolerance,
  scoring and configuration, with all departures listed.
- **Gita research agreement:** existing blinded semantic matching plus explicit
  same-side location tolerance and maximum one-to-one matching, independently
  tested. Report its own name; it is not an official AACR score.

AACR agreement precision measures reference agreement, not actual defect
precision. An unmatched candidate can be a novel valid issue. A matched rejected
comment is a useful false-allegation diagnostic, but not proof that every
unmatched candidate is correct. Never infer execution success from comment scores.

### ReviewBench

The full manifest contains **219 tasks / 187 repositories**. The 25-task public
starter subset is part of the full set, **not a secret or untouched test split**.
Pin exact membership and labels.

Use the official matcher/scorer rather than substituting AACR one-to-one logic:
ReviewBench uses many-to-many raw correspondences, deduplication/precision claims,
known TP/FP labels, and independent classification of unmatched findings.

| Metric | Interpretation |
| --- | --- |
| Grounded recall | Known golden TPs covered / known golden TPs. Primary cross-arm quality metric. |
| Grounded precision | Matched TP candidates / matched candidates; excludes unmatched findings. Not an all-output noise measure. |
| Augmented precision | Matched TP candidates plus classified unmatched TPs / all candidates. Key noise guardrail. |
| Augmented recall | Known coverage plus novel TPs / known TPs plus novel TPs. Agent-dependent denominator; diagnostic, not headline cross-arm ranking. |
| Novel TP count, candidate count | Discovery and volume diagnostics; deduplication matters. |

Preserve official macro/micro, null denominators, severity/category slices,
candidate ordering, and duplicated-candidate behavior. Do not pool these metrics
with AACR into an undocumented common score.

## 3. Current harness audit: what to reuse and what must change

Findings are from source inspection, not claims that every old experiment was
invalid. Each risk below needs a test before confirmation.
The existing `tests/test_paired_eval.py` suite passed 50 tests with explicit
legacy opt-in during this audit, without model calls. That is a historical
regression baseline, not evidence that indexed preparation, new scoring adapters,
or the scientific validity gates below are implemented.

| Area and evidence | Current behavior / risk | Required disposition |
| --- | --- | --- |
| [dataset freeze](../../../src/gita/eval/paired/dataset.py) | Pins legacy data, positive rows only; first hash per language becomes pilot. | Reuse digest/dedup concepts. Version benchmark-specific task/label manifests; repository-disjoint development partition. |
| [dataset prepare](../../../src/gita/eval/paired/dataset.py) | Fetches named commits at depth 1; resolves merge base via GitHub API; writes `scope.json` in corpus worktree. | Replace for indexed evaluation: provision exact local history offline before timing; move metadata outside inspected tree. Endpoint-only availability is not history readiness. |
| [evidence tools](../../../src/gita/eval/paired/evidence.py) | Treatment tools are `gita_diff/show/expand`, currently legacy-gated; common tools only expose two endpoint revisions. | New indexed adapter with fixed allowed generations/refs and history parity for baseline. Keep legacy harness separate; never just flip the feature flag. |
| [runner](../../../src/gita/eval/paired/runner.py) | Fixed GPT model, 40-call prompt, Copilot-specific flags, treatment opt-in, cached results reused by path. | Separate model/runtime config; validate protocol digest on every reuse, not just run-root metadata. No forced use of Gita. |
| [tool audit](../../../src/gita/eval/paired/runner.py) | Audits schemas from final usage checkpoint and start/server counts. | Keep strict audit idea; verify every inventory transition and correlate call IDs, args/result hashes, completion/failure and service logs. Counts alone are insufficient. |
| [usage parser](../../../src/gita/eval/paired/common.py) | Single-authoritative-total checks useful; Copilot-specific model and pricing estimate, cl100k diagnostic elsewhere. | Backend-specific usage adapters; actual model tokenizer/accounting, no Gemma cost inferred from Copilot credits or tool-output tokens. |
| [judge](../../../src/gita/eval/paired/judge.py) | Blinded candidate mapping, same-file pairs, semantic equivalence without source, one-to-one matching, five-line localized tolerance. | Reuse for explicitly named research agreement only; preserve uncertainty and line/side audits. ReviewBench needs its own official scorer path. |
| [reconciliation](../../../src/gita/eval/paired/reconcile.py) | Separates usage validity from format/content; content-invalid produces empty findings in derived artifact. | Preserve raw evidence; explicitly failed responses must not become indistinguishable successful zero findings. Report failure separately. |
| [report](../../../src/gita/eval/paired/report.py) | Repository bootstrap useful; cost comparisons condition on valid pairs, protocol key omits some provenance, pair ID lacks repeat dimension. | Add attempt/repeat/model/arm keys, all-attempt costs and task denominators, strict full protocol identity. Keep conditional estimates separately labeled. |
| [preflight](../../../src/gita/eval/paired/preflight.py) | Merge-base resolution is called availability. | Add object/member/grammar/index/source/adapter/schema readiness checks before any model call. |
| [export checks](../../../src/gita/eval/paired/export.py) | Existing sanitization and evidence projection. | Reuse with adversarial secret/path/leak tests and licensing review; never publish credentials or user configuration. |

Additional upstream audit risk: the inspected AACR
[`evaluation/judge.py`](https://github.com/alibaba/aacr-bench/blob/f8367981425b15a42d473853b94dfe3731b6b24c/evaluation/judge.py)
selects mock lexical matching when the judge key is absent and catches judge
exceptions as nonmatches. A scientific run MUST fail closed on mock mode,
missing judge configuration, malformed outputs and judge-call failures. Preserve
the official behavior for reproducibility but fail the campaign gate, rather
than publish lexical/mock results as semantic evaluation. Record this deviation.

Provisioning must load required parsers and verify installed native inventory
before blocking network or starting measurement. The indexing PR's fresh-cache
CI exposed `download()` returning successfully without materializing grammars;
developer caches masked the failure. Add a fresh-cache readiness test and a
read-only indexed dry run. A successful download request or merge-base API call
is not a ready experiment.

The S2 review also exposed gates that accepted self-consistent wrong source
digests or consistently wrong query answers. Those were fixed with adversarial
tests; apply the same lesson here: repeatability and JSON validity are necessary,
not evidence of correct reviewer scope or scoring.

## 4. Experiments and arms

### 4.1 Main controlled comparison

Freeze one model/runtime/configuration per experiment. Run three arms:

| Arm | Evidence |
| --- | --- |
| B: conventional baseline | Raw PR diff, exact file reads, literal search, file list, bounded Git commit log/history and historical source. |
| S: indexed snapshot ablation | All B tools plus indexed file entities, ID/state/source at allowed endpoints. No indexed history/events. |
| H: indexed history | All B tools plus S and entity history/range events over the same declared allowed commit graph. |

B is a competitive, documented read-only conventional workflow, not intentionally
crippled to favor Gita. S/H retain common raw tools for unsupported files and
context; do not prohibit raw diff just to improve adoption metrics.

Run B vs H as primary; S explains navigation versus historical reuse. Freeze
schemas/instructions/budgets per arm; extra schema-token overhead belongs in
measured model inputs. Treatment names are blinded to judges, not hidden from
the model if they naturally explain tools. Do not instruct H to invoke Gita once.

**S's endpoint-only indexed contract:** disable historical name lookup, reject
non-endpoint `at` and occurrence-source requests, and withhold `last_known_occurrence`
when it belongs to an intermediate commit. Broker responses may contain only
the requested endpoint occurrences and a state/ambiguity result qualified by
that restriction. They must not invent absence when an intermediate reference
is withheld. Reject cursors that expand this restriction. Build two endpoint
snapshot scopes if necessary: cross-scope EIDs cannot establish continuity.
Any shared-generation cross-endpoint EID relationship intentionally retained in
S must be declared as endpoint identity evidence, not history-free extraction.
Mutation tests probe deleted identities, historical aliases, old source refs and
pagination. All arms retain B's raw history tools; the ablation isolates the
additional indexed history interface, not the availability of historical facts.

Separate studies:

- **Matched-budget review:** same context ceiling, token/turn/call/time budgets
  and task prompt. Report quality and actual consumption.
- **Amortized reuse:** sequential PRs/investigations in a pinned repository with
  incremental indexes. No learned answers/agent state carried across PRs.
- **History-essential tasks:** independent reviewed tasks with known rename/move/
  regression history and executable outcomes when possible. Neither review
  benchmark alone establishes benefits of long-lived identity or bug repair.

The reuse sequence must be frozen and genuinely first-parent appendable. Arbitrary
PR heads usually are not: a published PR tip may not be an ancestor of the next
tip. Either select an appendable sequence explicitly or predeclare separate
scopes/rebuilds for divergent heads. Charge every rebuild, extra scope's storage,
and provisioning in the sequence. Report how many transitions resumed versus
rebuilt; never calculate break-even from only successful append transitions.

An unrestricted-shell production baseline MAY be a separately labeled external
validity check. It is not directly interchangeable with the restricted controlled
baseline and requires its own artifact/safety isolation.

### 4.2 Models and offline claims

For the Gemma paper, use a specific Gemma checkpoint, tokenizer, quantization,
context limit, inference server revision, decoding settings, hardware and seeds.
Verify official model availability/terms before selection. No hard-coded
assumption that a particular checkpoint fits consumer RAM.

First qualification uses one model to attribute evidence-tool effects; model-size
or cloud/local comparisons are separate strata, not pooled causal estimates.
Record temperature/top-p, actual supported seed behavior and stop conditions.

ReviewBench hosted runner has no GPUs and expects self-hosted network inference
for open models; it cannot establish offline hardware performance. Run locally
with network blocked after provisioning for that claim. Judge calls may happen
later on a separate machine; disclose them and do not call the entire scientific
pipeline offline. Official leaderboard entry is separate from a local research run.

## 5. History scope, PR semantics, and leakage prevention

### 5.1 Freeze task facts

Each task records benchmark pin/file digest, repo/mirror URL, supplied base,
effective merge base, review head, title/body policy, ordered PR diff semantics,
allowed commits, index anchor/generation/context, scope readiness and labels
digest in a grader-only manifest.

**Eligibility is independent of Gita readiness.** Freeze benchmark/source
availability, reviewable pinned endpoints and the common evidence contract before
arm assignment. Missing source pins affect all arms and are reported. Index build
failure, parser gaps, candidate limits, unsupported files or non-first-parent
range membership MUST NOT remove an otherwise eligible task. Default S/H policy
is to continue with common B tools and a visible indexed-unavailable response;
index failure and its cost remain counted. A no-fallback deployment variant is
separate and counts failed treatment delivery, not an exclusion.

Use Git three-dot PR semantics where required: resolve merge base offline, then
compare merge-base to head. Verify this matches the supplied/pinned benchmark
patch. An adjusted base is recorded and audited, not silently rewritten.

### 5.2 First-parent limitation

Current indexing requires common first-parent membership for range events. A
merge base reachable only through another parent cannot be forced into one index
by relabeling a commit or synthesizing a merge.

- If anchor/base/head lie on the selected first-parent chain, build a bounded
  scope ending at review head; expose `(base, head]` events plus earlier allowed
  history under an explicit anchor policy.
- If they do not, keep the task in the overall evaluation: common raw diff
  remains correct; separate endpoint scopes MAY supply snapshot navigation.
  Do not compare cross-context EIDs or claim indexed events cover this PR.
- Missing history, parse gaps, unsupported languages and candidate-limit omissions
  are eligibility/coverage strata, not automatic task exclusions.

For the controlled H-history cohort, predeclare **100 first-parent commits before
the effective review base**, plus the PR chain, as the initial bounded history
policy. If fewer exist, record actual root/shallow boundary. An additional full-
history/reuse campaign is optional and separately costed. B gets access to the
same reachable history; otherwise the experiment confounds information with tools.

### 5.3 Future information

Never expose labels, review discussions, author follow-up fixes, downstream tags,
current upstream branches, future commits/objects, or benchmark IDs that enable
lookup. Restrict by Git reachability and a manifest allowlist, not wall-clock
timestamps. ReviewBench reference discovery can use later fixes; the reviewer
must not see those. Bare/mirror clones may contain future objects even when HEAD
is old: the broker must enforce allowed object access, including source references.

Artifact directories, preparation metadata and judge labels live outside the
inspected Git tree and outside the reviewer filesystem. Search/list/read never
see hidden output/config directories. Treat repository text as untrusted data.
No reviewer network, arbitrary shell, build scripts, hooks, or corpus execution
in the controlled experiment. Executable repair tasks need a separate sandbox.

ReviewBench's frozen clones have minimized/shallow history and no upstream API;
record their real bounds. Never add upstream history to one arm and still call it
the official hosted setting. A locally enriched-history study has a new label.

## 6. Harness architecture and strict contracts

Recommended new versioned indexed experiment module under `gita.eval`, separate
from the retained legacy `paired` protocol. Extract stable utilities after tests;
do not create one generic scoring abstraction that erases benchmark semantics.

```text
benchmark converter -> frozen task/label manifests
                                |
offline preparation -> readiness + scope/index receipts
                                |
paired/repeated scheduler -> isolated evidence broker + model backend
                                |
raw traces/usage/findings -> schema/location audit
                                |
benchmark-specific blinded scorer -> reproducible analysis -> sanitized export
```

### Reviewer broker

Tools bind to fixed repo/scope/generation, valid member revisions and source
occurrences; clients cannot override repo or build/GC/rebuild/verify the index.
Use current Python/indexed MCP operations, not legacy diff/show/expand. Expose
range events through an explicit adapter to `events_in` if no public dedicated
tool exists. This is range history, never an indexed endpoint diff.

Every response includes provenance, coverage, explicit omissions and cursor.
Source references are validated against task/generation/allowed commits. Over-
budget source returns an error, not truncated success. Keep raw-evidence
continuation behavior consistent in all arms.

### Attempt identity and provenance

Required immutable key:
`dataset_digest/task_tuple/model_config/arm/repeat/attempt/protocol_digest`.
Protocol digest covers product and harness source/archive hashes, parsers,
matching policy, manifest/labels, tool schemas, budgets, prompt, model/backend/
tokenizer/quantization, judge/scorer, environment and timing policy.

Resume never accepts a `result.json` just because it exists. Verify checksums,
identity, terminal receipt and protocol. Retries get new attempt IDs; never erase
failed spend or substitute the cheapest attempt. Code/threshold changes force a
new protocol/run root.

### Terminal states and accounting

Separate preparation/index failures, reviewer failure, schema/location invalid,
measurement invalid, judge pending/failed, and valid empty findings.

- A crash is not an empty review.
- A judge timeout is not a nonmatch.
- Unknown usage is `null` with reason, not zero.
- A partial index is not a complete source oracle.
- A terminal zero-findings review is valid only with completed model/evidence
  execution and valid schema/scope.

Persist start/end receipts, call IDs, inventory snapshots, stdout/stderr,
model-native usage, exact findings, source locations, judge inputs/outputs,
all requested retries, and attribution digests. Never store keys in command logs.

## 7. Benchmark-specific outputs and scorer validation

### AACR adapter

Retain original PR/path/side/line coordinates and label class. Validate ranges
against actual base/head files; diff offsets are not file coordinates. Rename/
deleted-line findings need explicit old path/left side. Do not map them to head
lines without a defined lossless transformation.

Freeze separate official and research-agreement metric outputs. The research
score uses maximum one-to-one same-issue matches, five-line tolerance only if
predeclared, and a semantic-only diagnostic without locality. Report zero-
reference denominators explicitly; never average null as zero implicitly.
Include overlap of generated findings with rejected annotations as a diagnostic,
not a universal false-positive estimator.

### ReviewBench adapter

Emit official JSON with `pr`, registered `agent`, `producer`, repository-relative
POSIX `file`, and head-revision `start_line/end_line/message`. Validate head/PR
identity and actual line bounds. Pure deleted-line findings that cannot be mapped
to a valid relevant head location are retained in research artifacts but marked
unexportable; report their count. Never silently drop or invent coordinates.

Use the pinned official scorer and judge settings. Preserve every raw matching
edge and official dedup/order rules. Recompute micro/macro from counts; save
null denominators. Record source snippets available to matcher/classifier.
Only portal-accepted runs are official leaderboard results.

Before calling the scorer, require exactly one valid terminal classification
for every resolved unmatched candidate. The pinned scorer's normalization can
substitute FP/unclassified when a classification is missing; that fallback MUST
NOT become a scientific result. Reject incomplete work as judge-pending/failed.
Include the official
[`match-types.ts` resolver](https://github.com/review-bench/ReviewBench/blob/c17b05be9c90b85a1fc012ce39e455e2d09a7255/scripts/lib/match-types.ts)
between matcher and scorer in pinned conformance tests: it determines precision
claims and golden coverage. Do not validate only final ratio formulas.

### Required synthetic scorer cases

Perfect match; same location/different issue; same issue/different side; rename;
left/deleted-only evidence; duplicate candidates; one candidate covering multiple
goldens; two candidates matching one golden; matched known FP; novel TP/FP;
no candidates; no golden TP; malformed/missing/duplicate judge rows; error/
uncertain verdict; candidate order permutations; out-of-range locations.

Run official fixture outputs as conformance tests, not only our expected formulas.
Any permutation sensitivity inherited from official scoring is documented and
the canonical candidate order frozen. Mock backend tests MUST stamp synthetic
results and be rejected by scientific reporting.

## 8. Evaluation ladder and harness-review gates

| Stage | Work | Permission to advance |
| --- | --- | --- |
| E0 offline audit | Unit/property/adversarial tests, pinned real task preparation without models, trace replay, label/coordinate/scorer review. | 100% required contract tests pass; zero forbidden tool/ref/label accesses. |
| E1 instrument pilot | Approximately 20 tasks per benchmark, repository/coverage diversity, B/S/H, two repeats; development only. | Manual audit of scope/usage/output/grades; protocol defects fixed before confirmation. |
| E2 feasibility freeze | Estimate variance/cost, finalize targets/power and protocol; freeze development vs confirmation by repository. | Signed manifest and decision rules; operator approves spend and runtime. |
| E3 confirmation | Every predeclared eligible confirmation task with the exact frozen repeat count; no tuning during run. | Report complete denominators, missingness, uncertainty and all-attempt resources. |
| E4 diagnosis | Blinded error buckets, supported vs unsupported/history-ready slices, ablations and next-version priorities. | New changes tested on new/fresh evaluation protocol; no post-hoc rebranding of confirmation. |

AACR's historical 196-task corpus has already been used in development/previous
experiments: disclose this. Repository-disjoint partitions help current tuning,
but do not turn previously exposed data into genuinely unseen evidence. ReviewBench
starter repositories belong in development; remove all same-repository tasks
from confirmation, not just the 25 exact tasks. Check cross-benchmark repo/task
overlap and report a separately untouched/less-exposed slice if available.

Pair arm order with recorded randomized/counterbalanced schedules per task/repeat.
Do not run paired latency measurements concurrently. If an interruption is
investigated by replaying all arms, use a separately reported diagnostic cohort;
retain the original interrupted attempts and never replace primary outcomes.
Model seed support is logged; equal seeds do not eliminate stochastic uncertainty.

**Repeat/retry policy:** initial default is three repeats per arm/task. If the
approved budget cannot support this, freeze a smaller uniform count in E2 before
confirmation. Primary outcome uses the first scheduled attempt for each repeat;
no replacement by a successful retry. Permit at most one diagnostic retry for a
logged transport/service failure, never for an unsatisfactory valid review.
Report retry-assisted delivery separately and include every attempt's cost.
Interrupted campaigns resume unstarted repeats; started interrupted repeats
remain failures/unknown measurements with cause and bounds. A uniform replay
after an environment correction is a new protocol/cohort, not a repair that
erases the first cohort. Stop on a scope/label leak, corrupted source, spend
ceiling, or predeclared resource deadline; save all receipts and no success claim
from a selectively completed cohort.

### Manual review before expensive runs

Two reviewers SHOULD inspect at least 20 pilot task bundles: original patch,
scope/first-parent graph, representative source refs, tool inventory/calls,
output locations, native usage and judge result. Adjudicate all disagreements.
At least one reviewer must not have implemented the harness.

Judge validation: independently label at least 100 stratified candidate decisions
as an initial screen, using separate matching and unmatched TP/FP classification
audits balanced across arms/verdicts. Target >=90% binary agreement as a screening
threshold only. Report confusion matrices, false-match/false-nonmatch rates,
arm-specific errors and uncertainty; use sampling weights when inferring
population error from stratified samples. A 90% aggregate agreement rate cannot
resolve a 1-2pp efficacy margin. Freeze a 100-decision screen followed, if needed,
by two additional 100-decision stratified batches, selected by a recorded random
seed without access to the direction of the treatment effect. Assess robustness
only at those checkpoints and stop at 300 decisions unless all decisions in the
finite population have already been audited. Predeclare the error-bound and
adjudication rules in E2; reaching the ceiling without a robust decision means
inconclusive. Unresolved label uncertainty requires sensitivity
bounds or an inconclusive conclusion, not forced matches. Lower screening
agreement blocks automatic claims pending rubric correction and a new audit.

Mutation tests must deliberately make the harness wrong: wrong revision, poisoned
label path, always-empty indexed query, tampered digest, dropped failure, duplicated
usage total, swapped arm, omitted repeat, stale cached attempt, mock judge and
judge exception. **Every mutation must fail the relevant gate.**

## 9. What to measure and how to analyze it

### Primary measures

- AACR: macro localized reference recall, plus PR-level detection and micro
  agreement metrics. ReviewBench: official macro grounded recall plus augmented
  precision; report official micro and severity/category results too.
- Native model input/output/reasoning/cache tokens, requests and elapsed reviewer
  time. Diagnostic serialized tool-output counts use a named tokenizer and must
  not be called billed/model input tokens.
- Index provisioning/build/update seconds, CPU, sampled process-tree RSS, DB/WAL
  and coverage. Report unobserved TEMP or other resource counters explicitly.
- Eligible tasks, valid outputs, tool errors, failures by arm/stage, source/query
  coverage, ambiguity, truncation, required fallback, and unsupported paths.

### Exact estimands and denominators

- Freeze separate AACR positive-reference and rejected-comment label sets after
  joining duplicate task tuples. Only positive annotations enter recall.
  Negative-only tuples enter rejection/operational diagnostics, not positive
  macro recall. Report their counts and overlap.
- Primary delivered recall is the equal-task mean over a fixed set of eligible
  positive/reference-TP-bearing tasks, averaging the frozen repeats within each
  task first. Failed delivery contributes zero; unresolved judge work produces
  bounds. The denominator cannot change by arm or because a task emits nothing.
- Preserve official ReviewBench macro/micro outputs, including official null
  precision omission, as separate descriptive metrics with contributing counts.
  They are not automatically the paired non-inferiority estimator.
- The section 10 augmented-precision gate uses **pooled all-output precision**:
  sum matched-TP plus novel-TP counts across fixed tasks/repeats divided by sum
  emitted candidates, separately per arm. The paired delta is recomputed by
  whole-repository resampling. Empty reviews contribute zero candidates, not
  perfect precision; if an arm emits none, this gate is undefined and cannot pass.
  Recall/delivery gates prevent silence from becoming success.
- Independently audited FP comments/PR uses the fixed task/repeat denominator,
  including valid empty reviews as zero FP. Report invalid outputs/failures next
  to it, not as evidence of low noise. Estimate unaudited population rates with
  declared sample weights and uncertainty; otherwise label the rate sample-only.

### Cost boundary

Report preparation separately and end to end:

`cold_total = object/grammar provisioning + index build + agent review`

`local_preprovisioned_total = index build/update + review`

`warm_reuse_total(n) = initial_build + sum(updates + reviews for n tasks)`

Use seconds/CPU/storage and native model tokens, not a sum of incomparable units.
Keep judge cost separate from deployment inference but include all evaluation
spend in the scientific budget. Cloud credits are provider-specific observations,
not a consumer-hardware metric.

Compute break-even only when observed per-review savings exceed update overhead:
`n * (baseline_review - indexed_review - mean_update) >= initial_build`.
Publish the sampled sequence and uncertainty; never amortize over hypothetical
thousands of uses to rescue a slow cold experience.

### Failures and statistical analysis

Primary operational quality denominator includes every scheduled task after
benchmark/source eligibility freeze, regardless of index readiness. A reviewer/output failure contributes zero
delivered recall and a failure count; undefined precision remains undefined, with
failure rate shown. Do not treat infrastructure/judge uncertainty as proven model
error: report worst/best missing-outcome bounds and valid-pair sensitivity.
Unavailable pins are reported before randomization by cause; never silently
replace them with easier tasks.

Cost totals include every attempt/retry, even failed/ungraded ones. Show matched-
valid-pair efficacy separately; no headline conditional-only savings. Separate
model tokens from index CPU overhead.

Resource means divide by the fixed number of scheduled task/repeat slots per
arm, never the number of executed attempts. Report first-attempt usage and
retry-inclusive usage as separate series; charge each diagnostic retry to its
original slot. Use retry-inclusive native input tokens and elapsed deployment
work in the section 10 cost gates, while quality remains first-attempt delivery.
Each series includes failures' actual observed spend; missing telemetry is
unavailable with bounds, not zero. A cost gate cannot pass with unresolved
material accounting gaps. Diagnostic retries therefore cannot dilute a mean by
increasing its denominator.

Bootstrap whole repositories (10,000 draws, recorded seed); keep task repetitions
together within each sampled repo. Average repeats within task before macro
estimates; repeats are not additional independent PRs. Publish paired CIs for
deltas/ratios. Pin one primary comparison per benchmark; use Holm adjustment or
declare secondary exploratory comparisons for additional models/arms/claims.
Do not pool the two benchmarks into a single significance test.

Pilot-based power: use cluster resampling to estimate the available task count's
ability to distinguish the frozen margin/benefit. If insufficient, narrow claims
or collect more tasks; do not loosen margins after seeing confirmation. Small
language/severity strata are descriptive, not significance claims.

## 10. Proposed numbers defining a successful evaluation

These are product decision gates, **not forecasts**. Freeze them after E1 using
only development data; publish any revision and rationale before E3.

| Dimension | Proposed success gate |
| --- | --- |
| Harness trust | 100% required offline/mutation tests pass; 0 forbidden ref/label/network accesses; 100% published scores replay from frozen findings/judgments. |
| Index evidence | 0 sampled exact-source mismatches or proven false accepted links; all gaps preserved. Any verified corruption blocks efficacy claims. This does not prove universal correctness. |
| Operational delivery | >=98% schema/location-valid first-attempt terminal reviews per arm on source-eligible tasks, regardless of index readiness; treatment failure rate no more than 1 percentage point above B. These are point-estimate acceptance gates; confidence-backed reliability is separate below. |
| Useful baseline | Baseline localized/grounded recall >=10% and delivered known TP matches on >=20 confirmation PRs per benchmark; otherwise floor warning and no non-inferiority-only success claim. This is a minimum interpretability floor, not good product quality. |
| Recall preservation | Lower 95% bound of H-B recall exceeds `-m`, where `m=min(2 percentage points, 10% of development B recall)` frozen in E2. Apply per benchmark; do not use the old 5pp margin at a 10% baseline. |
| Noise guardrail | ReviewBench pooled all-output augmented precision H-B lower 95% bound >-3pp, with denominator defined in section 9; point estimate of independently audited FP comments/PR no more than +0.10. AACR novel claims require manual audit before true-precision language. |
| Safety-sensitive findings | No confirmed high-severity failure mechanism newly introduced by false index evidence. High-severity recall point regression >2pp triggers investigation; sparse evidence remains inconclusive, not proof of safety. |
| Efficiency win route | With quality/operational gates met: >=15% lower native mean input tokens, with 95% lower bound of reduction >0, and warm preprovisioned end-to-end time point estimate <=B. Report cold cost and measured break-even. |
| Quality win route | Alternatively, at identical resource ceilings: >=3pp recall point gain, lower 95% bound >0, no noise/operational regression; ratios of native mean input tokens and warm preprovisioned mean end-to-end seconds each <=1.10. Output/reasoning tokens and memory reported separately. |
| Index usability diagnostic | On named <=10k-definition repositories, proposed p95 warm id/state/first history page <=250 ms per API call, fresh session-open <=2 s, identical-tip no-op <=2 s. Named hardware and at least 100 observations/five sessions required; not a universal SLA. |

Choose efficiency or quality route **before confirmation**, or correct for testing
both. Passing one benchmark alone supports a benchmark-specific statement, not
general success. A confidence interval crossing a required margin is
**inconclusive**, not pass. Strongly adverse failure/noise evidence is a failure
even if token savings meet the threshold.

Reliability uncertainty must remain boundary-aware: zero observed failures does
not mean a zero-width true failure interval. Report one-sided 95% exact binomial
upper bounds at the repository-cluster level for the event "any first-attempt
failure in this repository," plus the observed task-level rate. For task-level
reliability assurance with dependent PRs, require a prespecified cluster-aware
model/sensitivity analysis; do not reinterpret the cluster bound as a task
bound. Bootstrap paired rate differences where nondegenerate; boundary or sparse
cases require conservative sensitivity/bounds and may remain inconclusive.
Passing the point gates alone permits "met observed delivery target," not
"demonstrated >=98% reliability with 95% confidence."

Do not invent a required 80% recall or repeat old 23.1% credit savings as an
expected result. Absolute rates depend on model, task distribution, labels and
scorer. The useful deliverable is a measured frontier: quality, resources,
coverage and failure probability together.

## 11. Turning outcomes into next-version fixes

| Observed failure | Evidence to inspect | Likely next work, not automatic conclusion |
| --- | --- | --- |
| Recall falls only in H vs S | Retrieved history, cursor/anchor coverage, agent distraction | History ranking/UX or bounded context; do not loosen identity. |
| S and H fail on same supported entities | Resolution ambiguity, reference mapping, source completeness | Query discoverability, schema/instruction improvements, missing surfaces. |
| High fallback/unsupported concentration | Actual changed-file language coverage | Prioritize byte adapters with frozen correctness fixtures. |
| Copy/rename wrong lineage | Accepted decision evidence/population guards | Correctness-first policy bug fix and explicit rebuild. |
| Many candidate-limit gaps | Candidate counts, profile/top contributors | More selective candidate indexing/bounds design; never suppress coverage. |
| Correct evidence but poor findings | Agent reasoning/quantization/context limits | Agent/model/post-training experiment rather than attributing everything to Gita. |
| Warm wins, cold loses | Build/update/reuse break-even measurements | Incremental/inventory/storage costs; scope defaults and honest UX. |
| Fewer output tokens but same native input | Tool schemas/repeated queries/model cache | Better protocol or no efficiency claim; don't headline proxy reduction. |
| Precision falls through novel allegations | Human-audited unmatched findings and source | Agent review calibration, judge rubric audit, not forced empty outputs. |
| Language/severity loss hidden by average | Per-task strata and matched budgets | Targeted regression fixtures before next general run. |

Every next-version hypothesis gets a reproducible failing case, an assigned
layer (index/query/harness/agent/judge), and a new evaluation protocol. Do not tune
against confirmation labels then report the same tasks as fresh validation.

### Evidence already available before model evaluation

The [pinned public-repository indexing report](../../benchmarks/public-index-20261014.md)
is not review-quality evidence, but it supplies readiness priorities:

- ripgrep's bounded Rust history hits `only synthetic roots have module kind`;
  retain this as an index-preparation failure until a separate correctness fix.
  Snapshot success is not evidence the historical task is ready.
- Flask/Express histories retain candidate-limit and cached-source coverage
  records. Audit their effect on the evaluated PR, do not treat `partial` as
  guaranteed complete context or remove the tasks.
- The initial Flask decorator-span oracle mistake demonstrates why benchmark
  gate failures require source inspection, a corrected versioned oracle, retained
  failed attempts and new measurements rather than post-hoc relabeling.

## 12. Implementation epics and acceptance

| Epic | Deliverables | Dependencies | Exit |
| --- | --- | --- | --- |
| S4-E1: Freeze/audit | Pins/label meaning, corpus overlap, per-repo partitions, current harness audit, recorded protocol and spend policy. | None | Dataset counts/coordinates inspected; scoring products named; independent reviewer approves. |
| S4-E2: Indexed preparation/broker | Offline allowed-graph stores, first-parent fallback policy, index receipts, strict indexed tools and common baseline history. | S4-E1, S2 correctness | Mutation tests reject scope leaks; legacy disabled throughout; no model calls. |
| S4-E3: Execution/accounting | Configurable model backend, attempt IDs/resume, native usage adapters, call correlation, failure/all-attempt ledgers. | S4-E1 | Fake-backend adversarial traces pass/fail correctly; unsupported metrics null, not zero. |
| S4-E4: Scorer conformance | AACR official/research split, ReviewBench official adapter, head/left coordinate rules, blinded judging, replay and human audit. | S4-E1 | All section 7 cases match pinned semantics; mock/error fail closed. |
| S4-E5: Pilot/freeze | B/S/H pilot, manual source/usage audit, judge validation, power and final thresholds. | S4-E2/E3/E4; operator runtime/spend approval | Signed E2 protocol; no claims from development pilot. |
| S4-E6: Confirmation/report | Paired repeats, repository-cluster analysis, cold/warm accounting, diagnostics and sanitized reproducible release. | S4-E5 | Evidence-to-claim map, missingness bounds, failure rates and next-version cases published. |

No paid run is authorized by this spec. E5/E6 require a model/hardware choice,
explicit compute/cost budget, permitted provider/data handling and stop conditions.
The local public-repository index measurements in this PR do not satisfy E5/E6.

## 13. Reviewer checklist and claim-evidence map

- **Contribution:** Does H beat S or is the gain only snapshot navigation?
  Answer: the three-arm design separates these; no history-reuse claim without
  H-vs-S evidence or independent history-essential tasks.
- **Clarity:** Are events confused with PR endpoint diff?
  Answer: common raw diff defines the PR; events remain `(base,head]` transitions.
- **Experimental strength:** Are retries, preparation and cache effects hidden?
  Answer: all-attempt ledgers, staged timing and interruption strata required.
- **Evaluation completeness:** Are unmatched findings automatically false?
  Answer: no; benchmark-specific labels, novel classification and human audit.
- **Method soundness:** Can the grader or future code leak into reviewer tools?
  Answer: separate label process/files, allowed graph/object broker and mutation
  tests are mandatory before a live pilot.

| Proposed claim | Required evidence | Current status |
| --- | --- | --- |
| Indexed Gita lowers review cost at preserved quality | Frozen confirmation B/H, margins, native usage CIs, cold/warm costs and guardrails | Needs new evaluation. |
| Persistent identity/history is useful | H/S ablation and independently reviewed history-essential cases | Needs new evaluation. |
| Works offline on consumer hardware | Local Gemma run with specified hardware/model, blocked network, memory/time and outcome records | Needs new evaluation. |
| Index evidence is reliable | S2 oracles/fault/equivalence tests plus real-repo source checks and disclosed gaps | Partially tested; not complete F4. |
| Generalizes across repositories/languages | Frozen multi-repo benchmarks, coverage/stratum reports and uncertainty | Needs new indexed review evaluation. |

## References

- [AACR-Bench pinned overview](https://github.com/alibaba/aacr-bench/blob/f8367981425b15a42d473853b94dfe3731b6b24c/README.md),
  [metrics](https://github.com/alibaba/aacr-bench/blob/f8367981425b15a42d473853b94dfe3731b6b24c/docs/metrics.md),
  [converter](https://github.com/alibaba/aacr-bench/blob/f8367981425b15a42d473853b94dfe3731b6b24c/evaluation/converters/aacr_bench.py).
- [ReviewBench pinned methodology](https://github.com/review-bench/ReviewBench/blob/c17b05be9c90b85a1fc012ce39e455e2d09a7255/docs/METHODOLOGY.md),
  [agent contract](https://github.com/review-bench/ReviewBench/blob/c17b05be9c90b85a1fc012ce39e455e2d09a7255/AGENT_CONTRACT.md),
  [matcher](https://github.com/review-bench/ReviewBench/blob/c17b05be9c90b85a1fc012ce39e455e2d09a7255/scripts/eval/matcher.ts),
  [scorer](https://github.com/review-bench/ReviewBench/blob/c17b05be9c90b85a1fc012ce39e455e2d09a7255/scripts/eval/scorer.ts).
