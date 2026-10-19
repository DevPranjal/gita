# Indexed review adoption: investigation and experiment log

This is a development log, not a claim that a treatment must win.
Keep all attempts, costs, negative results and protocol changes visible.
Do not change preserved review/judgment artifacts or leak reference labels into
reviewer-facing inputs. The 25-task starter is development data, not held-out
confirmation.

## 2026-10-15: E0, diagnose before another paid run

**Objective:** explain actual PR-level navigation, index usefulness/coverage,
tool discoverability and source correctness; improve meaningful indexed evidence
use without dummy calls or removing legitimate raw fallback.

**Preserved baseline:** reviewer protocol
`ddc9607478cb72f1f17a8d2a4a8f00f8776903c6fe287a3f94206d37e70711e9`,
21 valid pairs. See [all paired results](reviewbench-auto-indexed-quality.md).

### Confirmed preparation defect relative to the intended experiment

The owned endpoint-pilot preparation explicitly set `history_ready=False`,
built separate `rb-base` and `rb-head` snapshots, and recorded no identity
continuity across their contexts. Consequently the broker's `history` and
`events` operations were deliberately unavailable. This was an experiment
preparation choice, not evidence that the product history engine failed or that
the PRs did not need history.

**Evidence:** frozen scopes and the retained endpoint-pilot preparation script;
[history/event gates](../../src/gita/eval/reviewbench/evidence.py).

**Implication:** do not rerun this preparation and call it a history-aware
index-first comparison. Independently assess which tasks admit a connected
first-parent history and build/verify that history before model invocation.

### Confirmed API/discovery gaps

- H adds `index_status/entities/id/state/source/history/events` to all common
  Git tools. It has no indexed endpoint-diff/review-start tool.
- `events` is explicitly not an endpoint diff and rejects separate contexts.
- Discovery is arm-wide, not readiness-specific: unavailable history/event
  tools are still exposed.
- Generic prompts say every tool is optional; index-first workflow guidance
  appears in `index_status`'s tool description, not a task-start evidence bundle.
- `source` requires a complete previously issued nested reference object;
  the agent must perform and correctly copy a multi-step lookup.
- Model responses cap serialized JSON at 8,000 characters, while complete source
  spans over 6,000 bytes are refused. Rich identity/provenance metadata can
  reduce practical entity-page size further. Usability must be measured, not
  inferred from correctness-gate success.

**Evidence:** [tool schemas and descriptions](../../src/gita/eval/reviewbench/evidence.py),
[review prompt](../../src/gita/eval/reviewbench/campaign.py).

**Correctness status:** previous preflight independently verified 9,009
occurrences, but that does not establish adequate changed-code coverage,
useful source payloads, connected history or effective agent UX.

### Work in progress

1. Independent trace analysis: each of the 21 PR pairs, observable tool calls and
   final outputs only; no private model reasoning.
2. Parent investigation: first-parent eligibility, language/changed-file
   coverage, output sizes, source round-trip friction and legacy harness prior art.
3. Select small, measurable interventions from that evidence; record hypotheses,
   tests and pilot results here before scaling.

### Gates before paid scale-up

- A complete relevant indexed source round trip works on representative changed
  functions; outputs and omission/coverage fields are independently checked.
- History tools are genuinely usable for advertised tasks, or unavailable
  capabilities are truthfully omitted.
- Agent workflow has an explicit evidence entry point, without arbitrary
  minimum call counts.
- Both arms retain comparable task objectives and permitted source/history;
  label/scorer data remain isolated from reviewers.
- Pilot measures successful entity/source/history use, quality, tokens, credits,
  failures and source coverage together. Adoption alone is not success.
- Final 25-task execution uses a new frozen protocol and consistent judging,
  retains every attempted slot, and is not selected from repeated winning runs.

No new paid reviewer calls have been authorized by a readiness gate at E0.
The original native-spend bounds remain the starting accounting ledger; record
any new campaign ceilings explicitly before launching models.

## E0a: measured changed-file payload/coverage probes

[Offline probe data](adoption-index-probes.json), all 25 original tasks:

- 21 tasks admit the declared first-parent chain; **zero** had indexed history
  enabled. Four tasks require a different lineage policy/fallback.
- 21 had two snapshot publications, four had no published usable index.
- On representative changed files, a native result with 43 occurrences became
  only three model-facing records; another 229-occurrence file also returned
  three. Continuations were truthful, but this creates expensive pagination.
- The first record can be the synthetic `<module>` span. One observed module
  was 14,475 bytes: directly passing it to old `source` would exceed the
  6,000-byte exact-source cap.
- Entity records contain occurrence IDs, spans and blob/generation provenance,
  **not an EID or source text**. Resolving history is another lookup; these are
  not drop-in replacements for opening a function.
- The sampled Java task returned zero entities for its changed `.java` files.
  A successful snapshot publication is not proof of language/changed-code
  coverage. Language support and scope availability must be reported separately.

These are usability/preparation failures, not evidence of incorrect source bytes.

## E1: one-call indexed source at a review location

**Hypothesis:** an agent already has changed file/line anchors from a PR. A
single indexed call returning the containing entity's source is a better
substitute for `read_file` than requiring file enumeration, full JSON reference
copying and a separate `source` call.

**Change:** prototype H-only `entity_context(path,line,commit?,cursor?)`.
It selects the smallest indexed containing entity (preferring real definitions
over synthetic module roots), independently verifies Git-backed occurrences,
returns actual source plus compact identity/span metadata, and pages large
entities instead of refusing solely because the complete function is large.
It does not parse/reconstruct entities on read or build missing indexes.

**Validation:** exact-byte test initially exposed the test's LF/trailing-newline
assumption on Windows, not a source mismatch. Corrected the oracle to compare
the actual indexed byte span against Git. Both new tests pass: exact named
source and baseline isolation/no hidden build. Existing evidence tests: 25
passed before correcting that one oracle assertion.

**Not yet claimed:** adoption, cost improvement, quality improvement or complete
product CLI/MCP integration. The prototype is first being qualified in the
controlled indexed-review broker; avoid treating this as an already shipped
public product API.

**Regression qualification:** all 122 offline ReviewBench tests now pass,
including official scorer conformance and the two new source-tool cases.
An [actual frozen-PR location](adoption-source-roundtrip.json),
`customRulesEngine.ts:262`, returned `CustomRulesEngine.validatePattern` in one
indexed call: 1,872 source characters / 2,938 serialized response characters,
complete source, no error and no opaque reference copying.

## E0b: legacy onboarding was materially different

[Legacy runner](../../src/gita/eval/runner.py) installs an H-only `AGENTS.md`
whose reviewing section explicitly says **"Use gita instead of git diff to read
changes"**. It advertises one command delivering summary, changed files,
changed functions and relevant code. The natural-language task prompt itself
is shared, but repository instructions are not. Therefore it is incorrect to
attribute the old uptake solely to spontaneous discovery or model preference.

The indexed ReviewBench runner disables custom repository instructions and
offers optional low-level tools, with no replacement for that task-start
experience. Thus both **onboarding** and **a genuinely review-shaped entry
point** disappeared at once. Merely exposing seven indexed primitives is not
equivalent to the legacy integration.

Do not restore legacy extraction or its unsupported compression promises.
Design a truthful indexed entry point and document the evidence workflow,
while keeping the baseline's objective and available raw evidence comparable.

## E0c: PR-level trace analysis completed

[Per-PR analysis](adoption-pr-analysis.md) covers all 21 pairs with artifact
digests. Actual source retrieval was overwhelmingly raw file access, not entity
retrieval: 46/117 file reads were clipped, while only 1.7% of delivered lines
repeated. This points more strongly to bounded complete context and actionable
entry points than to a generic repeated-reading/cache claim. No completed
indexed-to-source workflow occurred. History is a conditional aid, not something
every static PR review demonstrably needs.

## E2: connected history and small workflow pilot

[Connected-history preparation](adoption-connected-history-pilot.json):
positions 0 and 6 passed source/preflight/history validation over 18 and 16
commits; eight multi-event named-entity histories checked per ready repository.
Coverage remains partial. Position 3 built 17 commits in 262.370 seconds but
did not complete verification before its 300-second supervisor limit; it is
not silently counted ready and remains in the eventual fixed 25-task cohort.

**Development pilot:** positions 0 and 6, chosen for verified readiness and
concrete source-navigation opportunities, not for favorable quality labels.
This selection is not confirmation evidence. B receives raw diff/source
workflow guidance. H receives explicit indexed source/history workflow guidance
with raw fallback; no minimum call count and no instruction to invent temporal
questions. This tests agent-plus-product onboarding, not pure tool availability.

Pilot protocol must declare four scheduled review sessions, Copilot `auto`,
reviewer ceiling 80 credits / judge ceiling 60, prospective native 30-credit
reservations, no automatic retries, and consistent targeted judging from the
start. The worst-case new allocation 140 plus the previous all-native upper
bound 589.474791 remains below the original 950 total authorization.
Unused reservations are not expenditure; actual pilot spending will determine
remaining ceilings before the final 25-task run.

**Readiness validation:** 125 offline evaluator tests pass. Small pilot/task
limits and budgets are frozen explicitly; the ordinary 25-task membership gate
is unchanged. All reviewer/judge ledger consumers use the declared frozen caps.

### E2 result: adoption/quality signal, but cost gate fails

[Complete two-task pilot summary](adoption-pilot1-summary.json). All four
reviews and both judgments valid; reviewer spend 6.167295 / judge 10.441809.

| Metric | B | H |
| --- | ---: | ---: |
| Credits | 1.088083 | 5.079211 |
| Input tokens including cache | 259,143 | 246,490 |
| Macro grounded precision | 50.0% | 83.3% |
| Macro grounded recall | 10.1% | 21.2% |

Tiny development sample, not a win: H routed once to Claude/once GPT; B routed
twice to GPT. On the same-routed node-postgres pair, H used 210,877 input tokens
versus B's 112,751, and cost 0.674926 versus 0.530928.

H used five `entity_context` calls, an indexed `events` call and index status
on node-postgres, but no indexed tools on the first PR (whose raw patch already
contained useful source). Thus meaningful source uptake rose to 1/2 sessions,
not universal. History was actually available and events succeeded.

Two concrete UX failures explain why usage alone is not success:

1. Smallest-entity selection returned a **19-character filter callback** at one
   relevant line, requiring raw reads for its enclosing removal method.
2. A raw-diff continuation copied by the model failed HMAC validation. Existing
   continuation strings ranged from 189 to over 1,000 characters.

## E3: review entry map, enclosing source and short continuation handles

- Add `change_context`: Git changed-line anchors mapped to existing indexed
  spans, with compact locations/names and explicit gaps. It is **not** a semantic
  endpoint diff, does not extract entities from a patch, and does not imply
  identity equivalence across separate contexts.
- Enclosing source selection supplies the widest non-module containing entity
  within a 4,000-byte source budget, with existing pagination for larger bodies.
  This is bounded enclosing context, not a claim to infer the semantically
  correct scope in every case.
- Session-owned 28-character HMAC-derived cursor handles replace long serialized
  cursor payloads. Scope/query binding and tamper rejection remain enforced;
  underlying native cursor data never needs to be copied by the model.
- H onboarding now starts with the indexed change map; raw patch verification
  and raw fallback remain available. No call quota is added.

Actual node-postgres checks: the 19-character callback now yields its enclosing
`Pool._remove` (540 characters); another tiny verification callback now yields
`Pool._acquireClient` (842 characters). The indexed map starts with named changed
locations and has 28-character continuations.

Do not scale to 25 on E2's quality-only improvement. Test E3 contemporaneously
against B, retain the negative E2 cost result, and report routing explicitly.
Offline full-set PR-range index preparation is proceeding separately, bounded
per repository and without models or excluding failed tasks.

### E3 result: less context, not better joint outcomes

[All E3 outcomes](adoption-pilot2-summary.json), no failures or cursor errors:
B/H credits 1.039816 / 8.813205; input tokens 225,156 / 86,971. Macro
grounded precision 50% / 50%; recall 14.65% / 10.10%. H retrieved indexed
entity source on one PR but skipped indexed tools on the other. Do not call
this a quality/cost success or select only its lower-token result.

**Routing evidence, not speculation:** native `auto_v2` records selected GPT
for both B sessions and Claude for both H sessions. This is a large credit
confound even though H used much less context. Category scores were similar;
the records do not establish *why* those candidate sets differed. Keep `auto`
as requested and report realized routes rather than silently switching models.

The same broad ReDoS concern also received different golden-match assignments
between pilots. Judgments and official resolver output are retained unchanged;
do not tune reviewer prompts to golden labels or choose a favorable rerun.
Small-sample quality and automatic semantic matching remain uncertain.

## E4: a genuine entity-source diff and review-specific exposure

The missing diff operation is not restored legacy extraction. New
`entity_diff(token,path?)` resolves a persistent EID in a connected generation,
requires independently verified present states at both PR endpoints, and diffs
their exact stored/Git-backed source. Ambiguity, unqualified endpoints and
disconnected contexts refuse with raw fallback guidance. It is one entity's
source diff, not a complete semantic PR comparison.

[Actual round trip](adoption-entity-diff-roundtrip.json): `validatePattern`
resolved to the same EID at base lines207-245 and head247-299; 1,621 diff
characters / 2,732 response characters, complete and independently verified.

The review profile now exposes six common raw tools plus six relevant indexed
operations, rather than every low-level primitive. Unavailable history/entity
diff are omitted for disconnected contexts. Exact task-specific schemas are
frozen, and server discovery, dispatch, native permissions and receipt audit
use that same profile. Both arms' guidance requires relevant full-function
verification without unnecessary rereads. The conflicting "all tools optional"
sentence is removed only for declared workflow experiments.

**Validation:** 127 offline tests pass, including exact persistent-EID source
diff, raw fallback retention, capability-specific discovery and unchanged
default prompts. This remains a broker prototype, not completed public CLI/MCP
productization. A final win is not assumed; full25 inference remains gated.

### E4 canary: stopped on a harness replay-guard regression

One H review completed valid source/output checks at 3.582495 credits, using
the indexed change map and raw verification. Before B could start, cached
scope verification incorrectly hashed the new `indexed_workflow` control flag
as though it belonged to the immutable repository scope. The source/index data
were not shown corrupt; this was a harness integration error.

The stopped ledger remains untouched, and the one-sided attempt is not
included in a paired performance/quality comparison. Fix: validate arm/workflow
against their frozen controls separately, and remove only those explicit
control fields when reconstructing the source-scope digest. A regression test
checks valid profiles plus tampered arm, workflow and history scope.

## Full25 offline qualification: not yet a model-launch gate

All 25 preparations were attempted in 34.8 minutes:
17 verified connected PR-range histories; 3 verified endpoint-only scopes;
2 build timeouts (positions1,13); 3 strict source-verification failures
(positions7,14,18, "Python AST span missing").

Original endpoint fallbacks are recorded, but a failed strict source oracle
is **not** silently reclassified as a verified connected index. The final paid
25-task review run is blocked until those three cases are understood. Existing
failure receipts/source oracles are being inspected under a bounded follow-up,
without rerunning the whole preparation batch.

### E5: independent oracle calibration, not an index-content fix

Read-only Git/SQLite diagnostics found all three named definitions in
**complete** files at the correct start offset. Their indexed ends include
trailing comments that CPython AST excludes:

- position7, `LayoutMode`: AST `[80,465)`, indexed `[80,487)`;
- position14, `rewrite_property`: `[250,2036)` versus `[250,2092)`;
- position18, `BacktestConfig`: `[1102,1299)` versus `[1102,1350)`.

The independent verifier previously required exact CPython end equality, so
these were false missing-definition failures. Corrected only the oracle:
allow a longer end solely when Python's independent tokenizer proves the
suffix is comments/whitespace, not executable code. Wrong starts, shortened
bodies, out-of-range ends and partial comments still fail. Assertion diagnostics
now report file/name/expected/indexed spans instead of only a generic failure.

27 oracle/public-benchmark tests pass. Only verification/preflight/history checks
on the three existing published generations are being rerun; no rebuild or
old failure-receipt replacement. Index/parser/matcher fingerprints are unchanged.

### Corrected profile canary status

After the replay fix, both reviewers completed and the real prior receipt
replayed successfully. H used `change_context` plus raw verification, but still
did not request per-entity source/diff on this PR. Credits B/H 0.425992 /
3.967533; input118,123 /25,291; GPT versus Claude routing again dominates the
price comparison. Automatic matching/classification is unstable on this
single broad regex finding across runs; retain each judgment and do not choose
the most favorable classification. No all-metrics success gate is claimed.

### E5 qualification completed

The next blocker was a real Python-version mismatch in the independent oracle:
the CPython3.11 host could not parse a newer valid f-string. An explicitly
configured CPython3.13.14 **isolated stdlib AST subprocess** now handles syntax
the host rejects; it only parses bytes and never imports/executes corpus code.
Returned coordinates are validated and interpreter versions are recorded.
Invalid syntax still fails. 29 oracle/benchmark tests pass.

Read-only requalification finished with **19 connected histories, 4 verified
endpoint-only scopes, 2 explicit timeout fallbacks**. Both newer verification
projections and all old failures are retained; database/auxiliary hashes remain
unchanged. No product parser/matcher/index fingerprint changed.

## E6: the entry packet must contain evidence, not just a table of contents

The canaries show that an indexed name/location map alone often sends agents
straight back to raw patches. `change_context` now includes up to two verified
persistent-entity source-diff previews before the complete paginated map.
Non-test paths are prioritized for these limited previews; test paths remain
in the map. Scope and incompleteness are explicit; new/deleted/ambiguous
entities retain raw fallback. Integrity failures propagate, not preview skips.
Backend preview generation is not counted as extra agent tool requests.

Validation initially encountered the retained-store test's existing requirement
for a clean committed runtime: it reported a source-pin mismatch while the
prototype source was dirty, before checking its deliberately missing store.
Do not weaken that source gate or change its expected error; commit the
qualified source and rerun the test under its intended precondition.

## Delivery freeze: one fixed25 validation run

Delivery candidate `200c404`; source-packet/oracle/public-benchmark check:
**56 tests passed** under the required clean-source pin. All original 25
starter tasks are frozen, with 19 connected histories, 4 endpoint-only scopes
and 2 recorded timeout fallbacks. No failed task is removed.

Protocol:
`3ef35ec7035e92e4a5349455f56c58186b0d2bcf9b468c7b3fdeb0a51ed4c691`.
Reviewer `auto`, 50 primary sessions, no automatic retries; reviewer cap220 /
judge cap80, combined with prior all-native upper bound644.1287544 for a total
prospective bound944.1287544, below the original950 authorization.

The canary pair passed inventory, source, output, call and usage checks.
H requested `change_context` and actually received verified indexed source-diff
previews; B requested raw source. H input25,973 versus B143,061, but H cost
3.823263 versus B0.491216 with Claude versus GPT routing. No cost/quality win
is inferred from this canary. Both receipts were inspected before continuation.

The fixed25 run is now executing. This is a development validation of the
frozen engine-plus-workflow candidate, **not** a claim that small pilots
established a joint win, a held-out confirmation, or pure optional-tool
availability. No further reviewer prompt/tool changes during this run. Next:
consistent blinded judging, all-slot accounting, actual source/history/diff
use and realized-route reporting, then publish the complete result regardless
of direction. This replaces further open-ended micro-iterations.

## User-directed fixed-model transition

The user requested GPT-5.6 Sol / medium reasoning instead of `auto`. The auto
coordinator was stopped at a **settled-session boundary**, holding the ledger
lock so no new reservation could race with the stop. All26 started sessions
settled: 57.368546 credits, zero unknown usage. Original receipts and operator
stop records remain unchanged; do not pool this incomplete auto cohort with
the coming fixed-model run.

Reviewer settings now flow through protocol, commands, invocation receipts,
output validation and replay: `reviewer_model="gpt-5.6-sol"`,
`reviewer_reasoning="medium"`. Unexpected fallback/mixed realized models fail.
Default auto behavior and older receipts remain compatible.154 offline evaluator
tests passed. An actual zero-tool fixed-model qualification completed with
the correct model and flags:1.516700 credits, one request, valid JSON.

The user requested the **upstream benchmark judge**, not merely its arithmetic.
Pinned ReviewBench was restored separately; its SDK supports `github-copilot`.
The older registry needs an explicit Sol model entry. An isolated, credential-free
model configuration is prepared. Upstream matcher defaults to low reasoning;
only that setting was changed to medium (classifier already medium), retaining
the matcher/classifier/scorer logic. Upstream TypeScript typecheck passed.

Authentication remains a launch gate: the configured Gemini key was rejected
by Google, so no Gemini inference ran. The user then selected Copilot-backed
judging. Pi has no Copilot login, and the existing GitHub CLI account's
first-party Copilot token exchange returned403. No alternate credential
extraction, fallback account, or authentication bypass is attempted. A supported
one-time Pi GitHub Copilot login is needed before the upstream judge smoke test.
Local Copilot CLI reviewer authentication works; that does not authenticate Pi.

No new fixed25 reviewers have been launched before the judge route is usable.
The Copilot provider registry reports zero prices, which is not proof of free
judging or a substitute for measured credit accounting.

### User clarified: reuse the previous Copilot judge

The user explicitly chose the already-working native Copilot matcher/classifier
plus pinned official arithmetic, rather than waiting for upstream Pi OAuth.
Reviewers **and judges** are now frozen to GPT-5.6 Sol / medium. No new login
or Gemini route is needed. Original custom prompts and consistent targeted
context remain; scores will be named research agreement, not upstream/leaderboard
scores. Model/reasoning are separately frozen for judges and passed through both
matching and classification.

The user was unavailable to approve a higher ceiling. Respect the original
950-credit cumulative allowance: use new reviewer160 / judge80 caps (240
maximum), with approximately703.014 already spent/reserved across previous
protocols and the fixed-model qualification. The fixed25 membership stays
unchanged; actual completion may be budget-limited, and no incomplete run will
be called complete. All old protocols and missing-usage bounds remain separate.

### Fixed Sol/medium first pair completed and judged

[Actual measurements](reviewbench-solmedium-first-pair.json). New protocol
`30ad9e3dcaccbe96756844afada9c3aecde1b3614ee95c71b632ea276f9f3e02`.
Both reviewers and the matcher/classifier realized GPT-5.6 Sol with medium
requested reasoning; no fallback or extra login.154 offline tests passed,
plus28 focused tests including fixed judge propagation.

| First-pair metric | B | H |
| --- | ---: | ---: |
| Credits | 24.126660 | 21.008640 |
| Input tokens including cache | 147,714 | 72,215 |
| Output tokens | 2,259 | 3,127 |
| Grounded precision | 0% | 0% |
| Grounded recall | 11.11% | 11.11% |
| Augmented precision | 0% | 33.33% |
| Augmented recall | 11.11% | 20% |

H used the indexed change packet, status and three entity-context calls.
Native judging cost16.997900 credits; total first review pair plus judging
**62.133200**. These are research agreement metrics, not official judging or
one-PR efficacy evidence. Official multiple-reference resolution can produce
nonzero recall and zero precision; do not replace its arithmetic.

This confirms fixed-model costs are materially higher than `auto`. At this
observed first-pair rate the remaining original authorization cannot fund the
full25 run. Requested a new total-run cap using the actual costs; the user was
unavailable. Do not assume the proposed1,500-credit ceiling was approved.
The remaining24 tasks have not started; preserve the scored first pair while
waiting for spending approval. This is a budget gate, not an authentication gate.

### Bounded continuation and native-cap overrun

The user directed continued autonomous work; no higher cap was approved.
Continued under existing160/80 caps, without changing the frozen protocol.
Two complete review pairs produced valid outputs, then native Copilot exported
**32.530160 credits despite its30-credit session cap**. The ledger stopped.

[Stopped projection](reviewbench-solmedium-stopped.json) preserves all four
raw receipts and46 unstarted sessions. Actual measured reviewer spending is
**106.038400**, not the original ledger's73.508240 plus an assumed30 upper bound.
All106.038400 are exported measured credits. The historical ledger is retained
unchanged; derived accounting explicitly corrects its overrun misclassification.
One pair is quality-scored (judge16.997900); the second is not scored.

New ledger handling now counts valid measured overspend, labels it `overrun`
instead of unknown, and writes a permanent stop before raising.33 focused
runtime/fixed-model tests pass. Missing usage still remains unknown. No further
paid calls, retry or old-ledger clearing followed this stop. The native limit
is demonstrably not a strict upper bound; future campaign budgeting must
allow a documented overrun reserve rather than promise a hard provider cap.

### Both already-paid fixed-model pairs are now quality-scored

Separate read-only source analysis bound all four valid measured receipts,
the unchanged stopped ledger and first-pair judgment. Only the second pair
received new Sol/medium matcher/classifier calls; the first pair was reused.
This did not resume reviewers or clear the spending stop. Additional judging
20.100900 credits; all fixed-run judging37.098800. Actual review plus judge
total **143.137200**.

| Two-pair macro quality | B | H |
| --- | ---: | ---: |
| Grounded precision | 0% | 0% |
| Grounded recall | 5.56% | 5.56% |
| Augmented precision | 50% | 16.67% |
| Augmented recall | 12.70% | 10% |

Micro augmented precision33.33% /25%, recall12.5% /12.5%. These are not a
quality win or formal non-inferiority. All23 unstarted pairs remain explicit.
Actual credits B56.656820 /H49.381580 and input421,944 /280,152 are conditional
two-pair observations only.

Updated the pre-existing overrun regression to assert measured `overrun`
instead of the old inaccurate `unknown` contract. **All157 evaluator tests
pass.** The broader task remains incomplete: a23-pair continuation requires
an explicitly increased total budget and revised native-cap overrun handling.
No approval is inferred from an unanswered budget form.

## E7: remove measured schema failures before buying more inference

Inspected the fixed-model tool traces, not reference answers. **Eleven source
searches were refused solely because the model used `path` instead of `pattern`**:
three in the first H session and eight in the second. The second also requested
an indexed changed-code packet although no index was available after its timeout.
Those refusals consumed calls/context and helped exhaust the evidence budget.

Changes:

- Accept an explicit documented `path` glob alias for `pattern`, with identical
  literal filtering, scope and cursor binding. Conflicting values still refuse.
  Both arms receive the same schema improvement; this is not a Gita-only baseline
  handicap.
- Workflow discovery on a task with no qualified index exposes only common raw
  tools plus readiness. The task prompt explicitly switches to raw fallback.
  No failed task is removed and no empty index is treated as complete coverage.

[Exact offline replay](adoption-path-alias-replay.json): all11 previously refused
queries now succeed and equal their canonical `pattern` queries against the
same frozen Git objects. No model calls or corpus execution.29 focused evidence
tests pass. This proves fewer interface failures, **not** improved review quality
or inference credits. Original reviewer receipts remain unchanged.

One final small development check may use the remaining original allowance:
prior all-native conservative bound846.1512005, new reviewer60/judge40 ceilings,
leaving3.8487995 credits of reserve below950. Native per-session limits can
overshoot, so any measured overrun remains a stop, never an automatic retry.
The check uses an already-seen development PR and cannot establish held-out
quality or justify a25-task claim. Do not spend past authorization or select
only favorable prior results.

### E7 result: actual defect fixed, requested efficacy target not achieved

[Complete fixed-model pilot](adoption-e7-solmedium-pilot.json). Both arms and
both judge stages used GPT-5.6 Sol / medium. All reviewer outputs and judgments
completed without tool errors; no alternate route or retry.

| Metric | B | H |
| --- | ---: | ---: |
| Review credits | 19.694500 | 26.897860 |
| Input tokens including cache | 137,411 | 144,702 |
| Grounded precision | 0% | 0% |
| Grounded recall | 11.11% | 11.11% |
| Augmented precision | 50% | 0% |
| Augmented recall | 20% | 11.11% |

Meaningful indexed-source usage increased: H made seven `entity_context` calls,
plus the indexed source-preview packet. But it also read nine raw source
windows and eight patch pages. Index use became **additional** evidence rather
than consistently replacing raw exploration. There were no schema errors, but
H cost36.6% more and consumed5.3% more input. This falsifies the hypothesis that
fixing interface refusals alone delivers a joint quality/efficiency win.

Actual E7 cost63.494260 including judging; conservative all-native cumulative
upper909.6454605 leaves40.3545395 under the original950 ceiling. A fresh paired
review requires two30-credit prospective reservations plus judging allowance,
so another paid paired experiment is not authorized under the remainder.
No unavailable-user budget proposal is treated as approval.

**Delivery status:** code and all seven iteration outcomes are preserved on
the PR; all159 evaluator tests pass. The fixed-model study remains two primary
pairs plus a separately reported one-PR development iteration, not25 completed
tasks. No cost/quality win, official-score equivalence or held-out confirmation
is claimed. All old raw artifacts and negative outcomes remain intact.

**Next measurable intervention, not yet executed:** an integrated source
retrieval contract must replace redundant raw/structured navigation rather
than add another optional tool surface. It should expose enough code for
caller/guard checks, track source coverage/omissions and measure repeated
retrieval at identical evidence scope. Qualify this independently of golden
labels, then use a newly authorized fixed-model paired run with a frozen,
consistent judge and explicit native-cap-overrun reserve. Do not force minimum
history calls or train the prompt to this repeatedly seen regex PR.

## 2026-10-19: E8, integrated source reuse (funding constraint lifted)

The user explicitly lifted the earlier funding constraint but requested that
long evaluations remain gated by small verified improvements. New work uses
bounded protocols, not an unlimited search for winning runs.

**Concrete trace evidence:** E7 asked for `handlers.ts:340`. The smallest
indexed containing occurrence was a 508-line module. Pagination returned the
**beginning of the file**, not the requested location. A later method source
request therefore became more work rather than useful enclosing context.
Other calls retrieved exact functions, then repeated their bodies with raw
`read_file`. Eleven observed schema errors were fixed in E7; the remaining
issue is evidence-delivery composition, not another prompt slogan.

**Intervention:** opt-in integrated source delivery. Large containing
occurrences start with an explicitly partial exact line window around the
requested location, not the beginning of the module. All source displays have
absolute line numbers. An immutable commit/path/blob keyed delivery ledger
will permit repeated source requests to reference already-delivered lines
while returning new lines; full replay remains available. Only fully delivered,
independently verified source may become reusable evidence.

**Initial no-model reproduction:** the real `handlers.ts:340` request now
returns exact lines320-370, 1,722 characters, with `whole_entity=false` and
explicitly omitted outside context. No hidden extraction/build or semantic
equivalence claim.

**Pilot population before judging:** positions6 (node-postgres),8 (CrabTrap),
17 (PROTRIDER): three supported connected-history tasks with distinct languages
and layouts. Do not use the repeatedly evaluated regex task as the new quality
gate. Their prior results remain development information; this is not held-out
confirmation. Both arms fixed Sol/medium, same judge, one repeat, no automatic
retries. Scale only if cost/context improve without a measured recall or
precision regression and delivery/source correctness gates pass.

### E8 offline qualification

- Shared source-delivery utility is independent of parsing/matching/index build
  policy. It keys exact commit/path/blob/line and is memory-bounded; forgetting
  a receipt resends source rather than claiming it known.
- Both integrated source and raw reads explicitly identify prior call receipts
  for repeated complete lines. `force_full=true` always permits a full reread.
  Truncated, lossy or continued-page outputs do not register complete delivery.
  Exact Git lines are independently checked before reuse; unread or future
  source cannot enter the ledger.
- Compact responses retain commit, blob where applicable, generation/context
  and source kind, without repeating the same task/base/head envelope on every
  source response.
- The original source-query sequence replayed offline against frozen objects:
  serialized source-response characters38,797 ->33,887 (**12.66% less**), with
  101 explicitly reused source lines. This is a transport counterfactual, not
  a credit/quality result; line numbering and reference overhead are included.
- **196 tests pass**, including source reuse, revision isolation, forced replay,
  incomplete-output exclusion, protocol binding and existing scorer conformance.

**Audit correction:** an early engineering probe inherited the old E7 tool-log
path and appended one engineering response. Before further replay, restored
the exact original prefix matching the original receipt's SHA256; retained
the full before-correction log, appended suffix and a correction receipt
separately. No reviewer findings/usage changed. Subsequent probes explicitly
remove tool-log paths and hash-check old logs unchanged. This correction is not
hidden or treated as another benchmark attempt.

**Soft native cap:** the provider's30-credit limit previously exported32.53.
New pilot freezes a45-credit reservation around the still30-credit native cap,
counts actual spend, and permanently stops if the explicit45 reserve is
exceeded. This is budget headroom, not an assertion the provider enforces a hard
cap. Defaults and historical ledger semantics remain30. Pilot caps:
reviewer300 / judge150; sequential calls, no automatic retries.
