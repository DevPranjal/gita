# Indexed review adoption: investigation and experiment log

This is a development log, not a claim that a treatment must win.
Keep all attempts, costs, negative results and protocol changes visible.
Do not change preserved review/judgment artifacts or leak reference labels into
reviewer-facing inputs. The 25-task starter is development data, not held-out
confirmation.

## Current status: joint quality/operational gates remain open

Engineering changes are tested; **E37's fixed25-task development study is
complete:50/50 valid reviews and25/25 judged pairs**. Gita uses9.62% fewer
review credits and16.76% fewer input tokens, with higher point-estimate recall
and pooled precision. Quality noninferiority is not established: the paired
intervals cross the frozen recall/precision margins, and warm elapsed is1.11%
higher. Do not turn the point estimates into a passed joint quality gate.
All six E34/E35 pairs were retained and the remaining19 executed once each.
A pre-model Rust query correctness repair has an explicit
annex and exact replay equivalence for all retained native evidence. All19
continuation source preparations qualified, including one recorded timeout/raw
fallback. Reviewer and judge remain GPT-5.6 Sol/medium on isolated CLI1.0.97.
E34's reused-case pilot is promising, but E35's fresh transfer costs19.03% more
despite higher recall and equal augmented precision. Lower cost has not
transferred in E35 alone; the new19 continuation saves10.18% credits, with only
a0.52pp macro reference-recall increase. E36's same-file guidance variant was
rejected for lower recall. The full25 remains development evidence, not
held-out/leaderboard confirmation. E30 repairs Swift/PHP and qualifies all five formerly failing
source requests. E31 adds the remaining eight plain-language/configuration
adapters and tests actual files rather than declaring registration a win.
Macro-heavy C, Astro and other real syntax/format gaps remain explicit.
These are product improvements, not yet a new quality/cost win.
Preserve all previous policies, source
supplements and judging spend. Product correctness and useful tool uptake
are not evidence that the requested joint benchmark target has been achieved.

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

### E8 paired result: scale gate failed

[All three PR outcomes](adoption-e8-dev3-results.json):
B/H credits81.844480 /76.737260 (**6.24% lower**);
input500,039 /492,185 (**1.57% lower**).
Macro precision91.67% /88.89%, recall14.99% /13.40%.
All six review sessions and three judgments completed; review158.581740 +
judge33.401600 credits. Only three source lines were actually reused in this
pilot: do not attribute the whole credit difference to the delivery ledger.

CrabTrap had the same measured quality with lower H consumption; node-postgres
had equal quality but higher H cost/context; PROTRIDER had one fewer matched
TP in H and two continuation argument-binding errors. The aggregate is not a
joint win; do not select only the favorable repository or start25.

## E9: eliminate avoidable pagination fragmentation and query-copy failures

The prior safe-output implementation halved a6,000-character candidate if it
slightly exceeded its serialized6,000-character envelope, usually returning
only3,000 characters. Replace halving with a bounded binary search for the
largest valid page under the **same** serialized/SDK limits. No larger output
or hidden truncation.

Add common `continue_evidence(cursor)` to both arms: it dispatches only a
previously issued continuation with its original scoped arguments. Unissued,
other-session or unavailable-tool continuations refuse. This prevents the
model having to reconstruct a source path/range while copying a cursor.
Tool-call budgets, output limits and provenance remain unchanged.

[Exact full-patch replay](adoption-e9-pagination-replay.json): node-postgres
6->4 pages, CrabTrap7->4, PROTRIDER68->37, with identical complete patch
content. That is a transport improvement for **both arms**, not agent quality
or a Gita-only credit claim. The huge PROTRIDER patch still exceeds practical
full-review budgets: it must not be declared completely inspected.

39 evidence/paging regressions pass. Future protocols explicitly bind the
standalone source-delivery helper hash as well as review-package sources.
The next small comparison keeps all three predefined development tasks,
fixedSol/medium and the same quality scorer; no golden-text prompt changes.

### E9 result and independent judgment check

[Complete E9 pilot](adoption-e9-dev3-results.json): B/H credits78.924260 /
80.428220, input430,093 /459,457. Macro grounded precision88.89% /91.67%,
grounded recall11.32% /14.99%; augmented precision88.89% /91.67%,
augmented recall15.43% /14.99%. The continuation fix eliminated H's earlier
query-copy failures, but cost/context did not win. **Scale gate fails.**

[Independent E8 decision audit](adoption-e8-judge-audit.md): all eight inspected
accepted matches agreed on underlying issue/cause/impact. This deliberately
small accepted-TP sample cannot establish FP discrimination, missed-match
reliability or judge repeatability. Existing classifications/scores are not
changed, and no reviewer prompt is tuned to audited reference text.

## E10: replace the extra tool menu with one engine-first review interface

The previous arms had seven baseline tools versus thirteen H tools. Even with
correct source delivery, the H workflow invites a parallel sequence of map,
status, entity diff, entity source and raw patch calls.

**Explicit new treatment:** H has a single `gita_review` operation interface
for changes/source/diff/history/status and exact ordinary `raw_patch` fallback.
The separately exposed H `raw_diff` is replaced by that explicit fallback,
not removed from the permitted evidence. Common raw file/search/list/log/
history and safe continuations remain. The objective, source endpoints,
history scope and fallback patch bytes stay the same.

This is **engine-plus-interface integration**, not a pure availability or
identical-schema experiment. It must not be advertised otherwise. No minimum
indexed-call quota; raw fallback through `gita_review` counts as raw, **not**
indexed usage. The first changed-code packet includes readiness/coverage so
the agent need not make a separate status call just to learn the same facts.

Actual H discovery:13->7 tools, serialized schema5,859->3,612 characters
(38.35% smaller). All201 evaluator/source-delivery tests pass, including exact
raw-fallback equality, source reuse, control binding and false indexed-usage
prevention. Smaller schema is not proof of cheaper reviews or equal quality.

Report tooling is corrected to use declared task/session counts and fixed
models rather than hardcoded25/50/auto. Measured overrun counts once; missing
reservations use their actual frozen caps; unrelated old plumbing spend is
not silently added to every new study.

Next bounded comparison: same predeclared three development PRs, fixedSol/
medium for both reviewers and judge, reviewer300/judge150,45-credit reserve.
No full25 inference until the combined quality/cost gate is supported. Do not
choose a favorable task subset or hide the prior failed candidates.

### E10 result: promising efficiency/augmented quality, not a clean scale pass

[All three E10 outcomes](adoption-e10-dev3-results.json), all six reviews and
judgments completed. Credits B81.213080 /H72.202300 (**11.10% lower**);
input475,387 /412,045 (**13.32% lower**). H credits were lower on each task.
Macro augmented precision91.67% /100%, augmented recall14.53% /14.76%.
Grounded precision88.89% /100%, but grounded recall11.32% /9.73%.

Meaningful indexed source was retrieved on all three H tasks (nine source
operations), while thirteen raw-patch fallback requests are correctly counted
as raw, not indexed. No quota or artificial denial of ordinary patch evidence.
One changes-operation selector misuse and one nonexistent-file request remain
explicit. Novel TP judgments are automatic and not certified defect precision.
The E8 audit's accepted-edge sample does not settle these novel classifications.

Do not retroactively relabel the original no-regression gate as passed:
grounded recall declined. However, the cost reduction is consistent across
the small tasks and augmented metrics did not regress. Before any25-task run,
use a **small additional check of the unchanged candidate** on positions15
(chronicle) and20 (LANdalf), disjoint from the three optimization PRs. Selected
by source readiness/language/layout, not golden text or favorable prior scores.

This is development validation, not a pristine held-out benchmark. Freeze
the same prompts/tools/engine and fixedSolmedium judges. Report the two new
tasks separately and all five combined; never replace earlier negative runs.
Only a supported joint quality/cost outcome on the combined dataset permits
the long25 study. If evidence remains mixed, report that rather than spend on
an unqualified long run. Reviewer200/judge100 caps,45-credit reservation.

### Transfer check completed; all five outcomes retained

[New two-task and combined five-task results](adoption-e10-transfer5-results.json).
On the two additional tasks, grounded P/R are equal between arms, augmented P/R
are higher in H, credits52.844680 /38.552600, input436,849 /181,249.
Combined allfive: credits134.057760 /110.754900 (**17.38% lower**);
input912,236 /593,294 (**34.96% lower**).
Automatic macro augmented P75% /90%, R14.43% /16.39%; grounded P73.33% /80%,
R10.12% /9.17%. One classifier needed exact dependency source, then allfive
were scored; four already completed pair judgments were retained unchanged.

### Novel-finding audit changed the confidence, not the historical scores

[Eight novel-decision audit](adoption-e10-novel-audit.md): six agree, one
disagree, one uncertain. The PROTRIDER H claim credits failed-load retraining
and checkpoint overwrite despite an existing-file guard disabling the nested
training/save. This is a real classifier error. The node-postgres error-channel
claim requires decisive built-in runtime semantics.

[Audited sensitivity](adoption-e10-audited-sensitivity.md), exact pinned
arithmetic with original automatic scores unchanged: flipping the known H FP
gives H augmented P83.33% /R15.60%; also pessimistically treating the uncertain
case as FP gives P76.67% /R14.24%. B remains P75% /R14.43%.
Thus the optimistic apparent quality improvement is **not** a robust certified
gain. Do not hide this or change only H labels in the primary result.

## E11: generic control-flow discipline for both arms' quality judges

The classifier prompt now requires reachable triggering conditions through
enclosing guards/early returns/side-effect conditions, and verification of a
compound finding's central claimed consequence. Contradicted central impacts
are not credited just because a weaker generic concern is plausible. Missing
decisive caller/runtime evidence stays uncertainty. No benchmark-specific
function, path, expected TP/FP or golden wording is added to reviewer prompts.

An **authored, independently executed Python oracle qualification** checked a
guarded write incorrectly alleged reachable, and an unguarded empty-list access:
the fixedSolmedium judge returned FP/TP correctly with source-line proof,
cost2.064700. No corpus program was executed. This is a small policy regression,
not proof that the judge is generally reliable.

All eight novel candidates from **both arms** are now being reclassified under
the same generic policy, with original matching edges and reviewer outputs
unchanged. The policy-variant result will be separately named and preserve every
earlier result/uncertainty. No full25 launch before this verification completes.
The explicit funding configuration supports bounded larger runs after the
user lifted the old allowance; old default400/200 caps remain unchanged.

### E11 result: errors caught; optimistic quality claim withdrawn

[Separate policy-variant results](adoption-e11-guard-policy-results.json).
All eight novel candidates were reclassified for both arms. Six decisions
remained unchanged; the two questioned H decisions became FP. Two missing
contexts were supplied: exact store/handler implementation and the documented
Node tracing contract with an independently authored built-in-only probe.
The probe on Node22.19.0 showed an ordinary error-first callback publishes the
tracing error event; no benchmark tests/application were executed.

The revised judge is still custom research judging. Original reviews, matching
edges and all older scores remain intact. Requalification cost65.324900,
plus authored qualification2.064700.

| Audited-policy allfive macro | B | H |
| --- | ---: | ---: |
| Grounded precision |73.33%|80%|
| Grounded recall |10.12%|9.17%|
| Augmented precision |75%|76.67%|
| Augmented recall |14.43%|14.24%|

Crucially, **pooled** augmented precision81.82% /72.73% is worse in H.
Do not select the favorable macro denominator or call this quality preserved.
The cost/context signal remains, but the original quality gate is not passed.
The long25 run is therefore still not launched.

The follow-up trace audit found the PROTRIDER reviewer **received both the
existing-file guard and nested save in a raw-patch page**, then received the
guard again in indexed source. This is a reasoning/reconciliation failure,
not evidence that the source engine hid the guard. Avoid inventing an index
extraction fix for a statement the model had already seen.

## E12: source-only verification, equal treatment and complete cost accounting

Test a source-only proof pass on already generated reviews, **for both arms**.
It receives original findings and observable tool results, never benchmark
references, prior judge labels, arm names or credit totals. It can retain,
drop or withhold uncertain existing findings, but cannot add/edit findings.
Decisions cite original evidence calls. A guard/reachability check must
reconcile the actual condition with the claimed side effect; unverifiable
library behavior remains uncertainty, not proof of an API defect.

First qualification uses the already audited PROTRIDER pair, then the remaining
four tasks only if useful. This is a development diagnostic, not an independent
confirmation. No reviewers are rerun. Final quality derives from the unchanged
retained findings using pinned resolver/scorer; existing labels are not changed
to make retained output look better. Missing re-resolved classifications stop
scoring rather than default FP. Empty output has undefined precision, not a
free pass.

Verification inference and latency must be included in B/H agent costs.
No source/verifier menu changes or large25 inference while this check is pending.

### E12 implementation

`proof.py` builds complete blinded inputs from already-validated primary
receipts, original findings and original broker results. Assistant reasoning,
intermediate messages, producer/arm labels, native usage and reference/scoring
artifacts are excluded. Completion *status* is retained but duplicate native
completion bodies are not: source results are supplied only once.

Each finding needs exactly one keep/drop/uncertain decision with a real
evidence-call ID; no additions or message edits. Only unchanged kept rows
become output. Unknown/integrity/invalid verification outcomes stop subsequent
sessions, with usage recovery and explicit errors. Original artifacts are
hash-checked before and after; the proof protocol binds source code, inputs,
model, limits and prior run. Complete input over400,000 serialized characters
refuses before any inference rather than silently truncating source.

A standalone retained-finding scorer re-resolves precision claims using the
pinned semantics, remaps candidate indices and requires classifications for
every remaining unmatched candidate. It never invents FP labels or awards
perfect precision to empty output.

Initial proof qualification selects only the audited PROTRIDER task, both
original arms, modelSolmedium and a90-credit verification ceiling with45-credit
reservations. Reviewer output/labels are not regenerated. Scores and **review
plus verification cost** must be checked before applying it to the other four.
This is an explicit agent-workflow intervention shared by both arms, not
evidence that indexing itself can repair a model's reasoning.

### E12 first qualification: did not reject the disputed claim

All242 offline regressions passed. Both source-only verification sessions
completed, spending39.506400 credits. H kept allthree findings, including
the disputed checkpoint consequence; B keptthree and withheldone uncertain.
Thus this first proof pass is **not qualified** merely because it produced
valid JSON and lower H verification cost.

Its rationale claimed a failed-load sentinel remains unchanged. A further
source/AST audit found an intervening fallback assignment, plus conditional
training/save. Existing checkpoint + non-None fallback disables those actions;
failure alone does not establish overwrite. A None-valued successful fallback
remains unverified. This qualifies the earlier shorthand "known FP":
the central advertised path is unsupported, not an unconditional proof that
no configuration could ever reach it. All earlier verdicts are retained with
[the conflict corrigendum](adoption-e12-guard-conflict.md).

## E13: current-snapshot proof instead of mixed-version tool transcripts

**Hypothesis, not an established cause:** diff before/after text plus partial
source windows encourages stale-value/guard reconciliation errors. The proof
pass still did not reject the unsupported consequence. Test canonical current
source, not another reviewer rerun or a benchmark-specific hint.

Opt-in snapshot verification deterministically gathers the **complete exact
HEAD files containing the proposed findings**, with numbered lines and Git
blob/commit provenance. It supplies no removed/base patch lines, golden data,
judgments or explicit arm/producer labels. Missing external callers/base
versions remain explicit and may require uncertainty. This differs from E12:
new backend source reads are recorded as snapshot evidence IDs, not falsely
claimed original model tool calls. Both arms use the same policy.

The generic proof instruction also tracks intervening assignments/fallbacks
before evaluating guards; it contains no benchmark-specific name or label.
Original E12 inputs/results remain intact.41 focused proof/scoring tests pass,
including unchanged legacy mode, complete HEAD source, no mixed patch input,
artifact integrity, full-cost settlement and no default-FP scoring.

Next check is the same one-task/two-arm PROTRIDER qualification, separately
sealed with90 verification credits maximum. Do not apply it to allfive or25
unless it demonstrates useful source-grounded decisions at measured total cost.

### E13 qualification result

[All E12/E13 pair costs and retained scores](adoption-e12-e13-proof-results.json).
The canonical HEAD verifier rejected the disputed H consequence, but withheld
another H finding because the model/loss class implementations were absent.
B retainedthree and withheldone external-library-contract claim.
E13 verification cost19.738900 versus E12's39.506400. This is cheaper and
better calibrated on that unsupported claim, **not** a sufficient recall result.
No source/reviewer outputs are rewritten and no uncertain finding is called FP.

## E14: supply repository-local dependency implementations, without another agent loop

The remaining H uncertainty requested two repository-local type implementations.
Those are precisely the context the persistent symbol store can retrieve.
Test a deterministic related-source stage before the single verifier call:
select at most16 type/call identifiers from the original proposed findings and
current HEAD source, not reference labels or judge messages.

- H resolves stored names at HEAD and retrieves independently Git-verified
  definition bodies, qualified spans and ambiguity/coverage.
- B uses a **competitive conventional Git-grep declaration locator** and
  complete matching source files, not a deliberately weak all-reference dump.
  Declaration locators are explicitly heuristic, not entity parsing/identity.
- Missing index and unresolved indexed terms retain the same raw fallback.
  Reference-only names remain explicit; they are not loaded as implementations
  or treated as absence. No query-time Tree-sitter extraction or build.
- Whole-source100,000-character retrieval budgets and exclusions are explicit.
  Indexed bodies already contained in a supplied identical raw file are
  exact-byte-checked and referenced, not duplicated in the model input.

The first naive raw preflight loaded too many usage/test files. It was
strengthened **before any paid E14 call**, with source-first global budgeting
and declaration-locator evidence. Do not claim wins against that rejected
weaker baseline.

[Final no-model preflight](adoption-e14-related-preflight.json) supplies the
needed local class bodies. No budget omissions in either arm; prompt lengths
B111,035 /H94,190. This is only a context-size observation, not a credit/quality
claim.74 focused proof/related-source/scorer tests pass, including real committed
Git, source-byte checks, injection rejection, fallback, ambiguity, budget
omissions and exact-content deduplication.

Next paid qualification: same original one-task pair, both verifiers fixed
Solmedium, no reviewers rerun, a separate90-credit cap. It must recover
source-supported local-type findings without restoring the unsupported claim.
Do not apply it to allfive or25 based on fewer prompt characters alone.

### E14 final result: reject filtering despite lower relative cost

All ten source-proof sessions on the five declared development PRs completed.
The [five-pair aggregate](adoption-e14-verified5-results.json) includes both
review and verification inference:

| Metric | Git B | Gita H |
| --- | ---: | ---: |
| Agent credits | 240.08526 | 202.98240 |
| Input tokens | 1,114,442 | 768,872 |
| Retained findings | 6 | 6 |
| Macro grounded recall | 5.54% | 4.59% |
| Macro augmented precision / recall | 66.67% / 6.36% | 83.33% / 7.27% |
| Pooled augmented precision / recall | 83.33% / 7.69% | 83.33% / 7.58% |

H costs 15.45% less, but this is **not a successful quality-preserving
optimization**. Filtering approximately halves useful recall; B is below the
predeclared interpretability floor and pooled H recall is slightly lower.
The [filtering ceiling diagnosis](adoption-e14-filter-ceiling.json) is an
explicit oracle counterfactual, never an input or selection policy for agents.
Deletion cannot recover issues the original reviewer never found.
Stop optimizing late finding suppression. Preserve all original outputs,
verification decisions, uncertain cases and both quality policies.

## E15: deliver complete current-function evidence before the initial review

**Frozen development design, before new paid calls:** positions 6, 8 and 17,
the same three development PRs used in E8-E10, not held-out confirmation.
Fixed `gpt-5.6-sol`, medium reasoning, for B, H and the existing Copilot
semantic judge with pinned official scoring arithmetic. No late proof filter,
reviewer retries, per-case winner selection or gold-derived context.
Finite phase ceilings: 300 reviewer and 300 judge credits; native session
setting 30 with a 45-credit reservation to cover observed soft-cap overshoot.
Inspect and grade the first pair before authorizing the other two.

This is an **engine-plus-workflow treatment**, not optional-tool availability:

- Both arms receive identical all-path inventory and the same bounded Git
  patch prefix. B receives competitive current-HEAD +/-100-line hunk windows.
- H receives complete outermost published callable bodies where supported,
  independently checked against exact Git bytes, with the same raw-window
  fallback for gaps and oversized bodies. No query-time parsing/build.
- Whole related implementations are retrieved via the same bounded helper:
  conventional declaration lookup plus full files for B, stored definitions
  plus raw fallback for H. Metadata and explicit omissions count toward budget.
  An omitted related section is not a claim that dependencies do not exist.
- Each packet has a 100,000 serialized-ASCII-character maximum: remaining
  allocation 45% patch, 35% HEAD, 20% related. HEAD selection uses a shared
  source/non-test tier and changed-line coverage per serialized byte, with
  deterministic ties. It does not rank by reference issues.
- Packets are built before inference, frozen by task/arm digest, copied into
  each attempt and bound to the exact native prompt digest. No initial cursor,
  delivery-cache seeding or fabricated indexed-tool calls. Preloaded indexed
  source and subsequent tool use must be reported separately.

The [first offline preflight](adoption-e15-initial-preflight.json) caught two
actual composition faults before spending credits: filepath-first allocation
starved later HEAD blocks; related-body budgeting omitted five of six complete
sections because it did not reserve metadata. Generic global source selection
and at most three metadata-aware related-budget attempts fix the composition.
The [corrected offline preflight](adoption-e15-initial-preflight-v2.json)
validates identical raw/inventory components, 50,542-95,275 total characters,
and 3.72-14.59 seconds preparation per arm. H contains related source on all
three; B explicitly omits the over-budget related sections and retains raw
lookup access. No budget/coverage observation is a quality claim.

112 focused tests pass: packet source/budget/omission checks, existing related
retrieval, fixed model/reasoning, transport, frozen membership, wrong-arm
packets, native prompt attestation and tamper rejection. A preloaded review may
legitimately make zero further calls, but only with an explicit empty broker
log and attested initial evidence. Native malformed redacted **user-input
echoes only** may be quarantined and recorded under this frozen profile;
reasoning, final answers, tools, controls and usage remain strict.

### E15 live result: adoption recovered, joint gate failed

The first pair is valid and fully scored: B/H credits25.31762/25.13212,
input101,218/89,713, both grounded and augmented P100%, R18.18%.
H made two successful indexed source queries after its preload.
This justified only completing the remaining two declared pilot cases.

[All six attempts](adoption-e15-dev3-results.json) remain reported.
Total measured B/H credits85.27764/86.21496 and input417,868/477,975:
H is not cheaper overall. H received 13 complete indexed callable blocks
plus22 related indexed definitions across the three preloads, and made three
additional indexed-source requests. This fixes evidence adoption, **not**
quality. No dummy indexed calls or history quota.

Only two pairs have valid paired quality outputs: pooled grounded/augmented
P100% in both arms, R22.22% B versus11.11% H; macro R21.59%/12.22%.
The third H review is valid but unpaired; the third B session has no valid
review and is not scored as empty or silently retried.

Native events identify a separate termination confound, not index corruption:
the third H session reports "28.89/30" and explicitly disables tools on its
next, final model call; its total is31.90350 credits. B reports20.06,25.43
and31.47/30, then emits one completion without a corresponding start and no
valid review. Its31.46520 measured credits are retained. Fixed routing and
medium reasoning remain verified; no authoritative frame was repaired.
Thus the old funding-derived30 setting is actively truncating investigations.
Do not infer what unconstrained precision/recall would have been.

## E16: qualify adequate native review headroom before further engine tuning

This is a small development experiment, **not** the25-set launch.
Keep E15 evidence generation,100,000-character packets, task ordering,
seed, tools, reviewer objective and fixed Sol/medium unchanged.
The single treatment-policy change is native session setting60, with explicit
90-credit reservations,600 reviewer/400 judge finite campaign ceilings.
Apply60 consistently to both reviewer arms and the judge. Actual spend,
including any overrun, remains authoritative. Historical30 defaults and
already-failed experiments remain unchanged.

Native setting is now frozen in the protocol, command, invocation receipt
and replay audit rather than hardcoded in the CLI builder. Reject invalid
settings and any setting larger than its reservation.104 focused regressions
pass (7 environment-dependent upstream tests skipped), including explicit
transport to reviewer and judge. The first pair must pass native/protocol
checks before authorizing the remaining two. A larger allowance is **not**
an efficiency improvement or permission to claim a quality win; measure all
three outcomes before deciding on another source-delivery improvement.

### E16 result: reliable completion, no joint improvement

[All three pairs](adoption-e16-dev3-results.json) are valid and fully scored.
Native60 usage is directly observed in the later sessions; neither arm hits
the native ceiling. The external canary check initially expected an optional
usage-info message even below half-budget. That engineering assertion was
corrected using already-written command/invocation receipts; **no review was
rerun**. The correction is retained in the private run artifacts.

| Metric | Git B | Gita H |
| --- | ---: | ---: |
| Review credits | 111.14754 | 128.22336 |
| Input tokens | 778,160 | 778,376 |
| Macro augmented precision / recall | 91.67% / 14.04% | 91.67% / 14.77% |
| Pooled augmented precision / recall | 85.71% / 14.58% | 87.50% / 14.29% |
| Pooled grounded recall | 14.58% | 12.50% |

Judge inference costs46.99730 credits separately. H makes11 successful
indexed-source calls across the three sessions, in addition to the frozen
index-backed preload. Better adoption does not compensate for15.36% more
review credits or lower pooled recall. Reject scale-up.

### Measured source-relevance/format issue, not wrong indexed bytes

Offline decomposition of the unchanged packets finds related-source sections
of16,363/17,677/15,014 serialized characters for only7,514/3,740/6,307 source
characters. Candidate provenance is repeated in symbol mappings and bodies.
More importantly, the identifier selector collects qualified member calls
such as `Get` and standard-library calls. Name lookup correctly returns
ambiguous repository definitions, but those are **not resolved dependencies**:
in one Go case, automatically prefetched `Get` bodies belong to unrelated
store/cache types. Explicit ambiguity protects truthfulness, not relevance.
Do not treat lexical candidates as a verified call graph.

## E17: on-demand dependencies, unchanged current-function evidence

Remove gratuitous **automatic related-source presentation in both arms**.
Keep every private packet, raw-patch prefix, inventory and HEAD block identical
to E16; attest that equality before inference. Present an explicit on-demand
notice instead of the related section, without reallocating its unused budget.
Agents retain search/raw reads and indexed source/history/diff as needed.
This is not finding deletion or an absence claim, and the original full
packet remains privately available for provenance.

This narrow ablation tests the observed relevance/metadata cost rather than
inventing a speculative type resolver or tuning names to benchmark issues.
It does not yet solve semantic dependency resolution in the product.
Same declared positions6/8/17, order, seed, Sol/medium, native60,90 reserve
and600/400 phase ceilings. No judge-policy change or late proof filter.
Scale25 remains blocked until joint quality/cost evidence; if a promising
small signal appears, require an unchanged-candidate transfer check first.

### Production API improvement: one-call named committed source

The public MCP source tool previously required copying a complete occurrence
from a separate identity/state query. It now also accepts a unique indexed
name, path-qualified locator or EID with an optional committed revision:
`gita_source(entity="app.py::handle", at="HEAD")`. Returned source retains
the exact occurrence and generation. Existing occurrence calls are unchanged.
Ambiguity, missing identity, removed state and budget overflow remain explicit;
no query-time parsing, implicit index build or worktree fallback.

All45 existing/new MCP tests pass with the suite's explicit legacy-enabled
baseline. Six named-source/missing-index tests also pass with legacy explicitly
disabled, including the actual server schema and historical source.
This is a production usability improvement, **not a measured cause of E17's
results**: the frozen experimental broker still has its prior interface.

### E17 result: promising cost/coverage, runtime-contract scoring defect retained

[All three paired attempts](adoption-e17-dev3-results.json) are valid.
B/H review credits120.94146/114.18016 (**5.59% lower H**),
input717,836/635,734 (**11.44% lower H**). Raw automatic grounded pooled
P85.71%/87.50%, R12.50%/14.58%; raw augmented pooled P90.91%/90.91%,
R19.23%/19.61%. H receives indexed current functions in all three preloads;
three subsequent indexed source queries succeed. Raw judging costs56.64450.
These raw augmented scores are **provisional**, not an efficacy claim.

Audit catches the previously observed Node-contract error again: both arms'
novel claims assert that `TracingChannel.traceCallback` does not publish a
normal error-first callback on its error channel. The tool-free classifier
incorrectly marks both TP from call-site source alone.

- An authored built-in-only Node22.19 probe confirms one error notification
  for both synchronous and asynchronous error-first callbacks, and zero for
  success. No benchmark program, build, test or hook is executed.
- The version-pinned official Node implementation independently confirms
  `wrappedCallback` publishes truthy callback errors. A synthesized web answer
  incorrectly cited a different runtime and contradicted these direct facts;
  it was rejected, not used as evidence.
- Add a generic, source-triggered runtime evidence registry, frozen before
  judging. It supplies the authored script, digest, exact observations,
  version-pinned implementation link and version/feature-gate limitations.
  It never supplies a desired TP/FP label or a task/arm identity.

Preserve original E17 scores and all reviewer/matcher outputs. Apply the
registry policy to **all seven novel candidates**: reclassify changed inputs
for both arms; reuse an existing decision only when the entire classifier
input is byte-equivalent. No repeat sampling of unchanged judgments.
Keep this as a separately named policy variant, with all extra judge spend.
Do not launch25 while this known quality defect is unresolved.

### E17 verified-contract policy result and prospective transfer gate

The [separate corrected policy](adoption-e17-contract-quality-results.json)
marks the Node claim FP in **both** arms, consistent with the independently
observed runtime contract. All five unchanged-input novel classifications
are reused, not sampled again. All original matcher edges and reviewer
outputs remain fixed. Extra judge inference7.48420 credits; no review reruns.
An engineering script syntax error occurred before execution/inference and
was corrected before the new variant root existed.

| Corrected metric | Git B | Gita H |
| --- | ---: | ---: |
| Review credits | 120.94146 | 114.18016 |
| Input tokens | 717,836 | 635,734 |
| Macro augmented precision / recall | 82.22% / 17.87% | 86.11% / 18.42% |
| Pooled augmented precision / recall | 81.82% / 17.65% | 81.82% / 18.00% |
| Pooled grounded precision / recall | 85.71% / 12.50% | 87.50% / 14.58% |

This is the first new initial-context candidate with lower measured cost
and no quality point-regression across these displayed aggregate measures.
It is **only a small, repeatedly used development signal**, not proof that
noninferiority confidence bounds pass. Independent stochastic variation is
visible in the changing B outputs; do not attribute across-run differences
solely to the dependency ablation.

Next prospective check: unchanged E17 reviewer treatment on positions15/20,
not per-outcome selection, fixed Sol/medium, native60,90 reserve,400/400
finite phase ceilings. The verified runtime contract is frozen before
judging and is source-triggered, not task/arm-triggered. No further reviewer
prompt, tool, source-selection or product-policy tuning during that check.
These tasks were used in earlier development, so call this **transfer of
the unchanged candidate**, not a held-out confirmation. Inspect all outcomes
and combined five-case macro/pooled results before any25-set launch.

### E17 transfer completed: quality preserved, cost signal weakened

[All five results](adoption-e17-transfer5-results.json) now have quality scores.
The C# classifier explicitly requested missing implementation/DI source.
Verified complete HEAD files were supplied to **both arms**, with unchanged
reviewers and matcher; one new, different-input classifier call cost7.39370.
The original uncertainty and27.68680 transfer judge credits remain preserved.
No missing classifier result was defaulted to FP or zero findings.

| Combined five metric | Git B | Gita H |
| --- | ---: | ---: |
| Review credits | 179.35154 | 176.85430 |
| Input tokens | 1,019,613 | 1,022,683 |
| Macro augmented precision / recall | 79.33% / 18.25% | 81.67% / 18.58% |
| Pooled augmented precision / recall | 80.00% / 17.39% | 80.00% / 17.65% |
| Pooled grounded recall | 10.94% | 12.50% |

The two transfer cases alone cost **more** with H. Combined credit savings
shrink to1.39%, and H input tokens are0.30% higher. This is not robust evidence
for the requested efficiency improvement; **do not launch25 yet**.
Keep the positive three-case result and the unfavorable transfer equally visible.

## E18: expose the production one-call source API in the reviewer interface

Trace evidence identifies an actual remaining navigation defect: the H
interface still requires file/line coordinates, so it often performs a raw
name search before requesting indexed source. The already-tested public
`gita_source(entity=...)` API is now wired into `gita_review(operation="source",
token="name", path=...)`. Existing path/line, raw fallback and history/diff
routes remain. No index rebuild or query-time parsing.

- Name/locator/EID selection uses the **production MCP source adapter**, not
  a benchmark-specific guessed binding. Its exact occurrence, generation,
  revision and source bytes are independently checked against Git again.
- Ambiguous names return explicit bounded candidates with full EIDs; the
  reviewer can choose one with the same available tool. No suggestion to
  call a hidden identity tool, and no arbitrary source selection.
- Complete bodies are numbered and safely paginated; shared receipts reuse
  only fully delivered exact lines. CRLF bytes survive reconstruction.
- Freeze the public source and CLI adapter hashes as well as evaluator code.
  Existing schemas keep one review interface; there is no forced-use quota.

The [first offline lookup replay](adoption-e18-named-source-preflight.json)
incorrectly joined captured name components with dots. That loses literal
dots inside namespace/expression components; this was an engineering replay
error, not evidence of wrong indexed bytes. Native `::` component syntax
plus actionable ambiguity yields [11 exact same-occurrence source bodies
and one explicit ambiguity](adoption-e18-named-source-preflight-v2.json)
for all12 previously requested distinct entities. Original logs are hash-checked
unchanged. Seven focused source/paging/ledger tests and75 protocol/judge
regressions pass. A test initially normalized CRLF; the byte reconstruction
oracle was corrected, not the source producer.

### Preserve supporting evidence in the judge, rather than repeatedly losing it

The uncertain C# grader lacked files the reviewers had **already requested**.
An explicit new judge profile retains the symmetric union of successful
source-request paths and **actually visible** preloads, then fetches bounded
complete HEAD files from verified Git. It does not copy reviewer reasoning,
unused private preload sections or model conclusions into judging.
Already-complete snippets are not repeated; binary/non-UTF8/missing/over-budget
files remain explicit omissions, never absence evidence. Both arms share the
same judge context. The [offline qualification](adoption-e18-judge-context-preflight.json)
automatically supplies the formerly missing implementation and DI registration,
without hardcoding a benchmark ID or running corpus code.

Prospective paid gate: first positions15/20, where E17 incurred extra indexed
navigation and failed to save cost. If the named-source mechanism yields a
credible joint signal, run the unchanged candidate on6/8/17 before25.
Fixed Sol/medium, native60,90 reserve,400/400 phase ceilings for the two-case
stage. Same current-function preload, on-demand dependencies, no late filtering,
no reviewer retries. Stronger shared source grounding is a declared judge-policy
change, not a reviewer advantage. Offline lookup success alone is not efficacy.

### E18 result: the source API is used; aggregate cost still fails

[All two-case results](adoption-e18-transfer2-results.json) are valid and fully
scored without a manual missing-source follow-up. On the Rust case H makes
seven successful named-source queries, reduces credits49.60668 to40.06906
and input335,635 to239,085, and finds an additional validated issue.
The C# case still costs22.99988 H versus11.52268 B while finding an additional
validated issue. Report the tradeoff, not just the favorable case.

Total B/H credits61.12936/63.06894 (**3.17% higher H**), input371,805/370,684.
Pooled augmented P33.33%/75.00%, R6.25%/16.67%; this is a small, low-recall
development population, not a useful-baseline validation pass. No three-case
or25-set continuation is authorized from this aggregate cost result.
Judge costs65.52830 remain separate and visible.

Independently, [all50 initial evidence packets for the25 tasks are ready](adoption-e18-offline25-readiness.json).
Complete inventory/shared raw components and serialized limits pass with no
model calls. This is readiness, not an efficacy result or cold indexing timing.

## E19: repair real core namespace/path lookup failures, not another prompt slogan

The costly C# trace contains three explicit refusals:
one named source query scoped to a directory, and history/diff requests using
a normal fully qualified dotted C# namespace. The index is built and contains
the correct methods. Its stored scope tuple keeps a compound namespace as one
component; the resolver previously split every dot into a separate component.
The directory hint was also treated only as an exact filename.

- Core resolution now matches qualified ASCII display names against the
  canonical stored tuple representation without adding a JSON SQL extension,
  reparsing code or collapsing distinct identities.
- Directory locators use bounded byte-prefix ranges. Root namespace syntax
  is not silently reinterpreted as a directory: `directory/::Name` is explicit;
  the reviewer adapter constructs that scope from its separate path argument.
- Ambiguity remains explicit. Name-query cursor context is versioned so old
  name cursors are rejected rather than silently resumed under changed rules.
  Stored generations, builder/matcher/parser/schema and EIDs are unchanged.
- Directory lookup receipts bind the actual returned member path, not the
  directory hint. A dedicated test catches this coupled delivery-ledger case.

[Exact no-model replay of the three refusals](adoption-e19-core-lookup-replay.json)
now returns two valid history/diff results and one honest source ambiguity
with actionable EIDs; no original model trace is modified.
Ten real-SQL predicate tests cover namespaces, ambiguity, path boundaries,
literal names and injection-shaped input.82 core query/comparison regressions
pass; four legacy-dependent comparison tests initially lacked opt-in and pass
when rerun with the required legacy flag. Sixteen focused lookup/public-source
tests pass with legacy disabled; sixteen core/evidence tests cover directory
receipts and complete paging.

Native parser artifacts are independently reprovisioned and unchanged.
The new product-policy digest is query-only; existing immutable generations
are reused, never silently rebuilt or relabeled. Source/query preflight runs
again before inference. Retain the compatibility receipt with the new parser
manifest. This fixes CLI, MCP and reviewer history/diff name resolution at
their shared core, rather than working around incorrect outputs in scoring.

Next bounded qualification remains the same two navigation-heavy cases with
the same Sol/medium, native60,90 reserve,400/400 phase ceilings and shared
judge grounding. No new25 launch unless the joint efficiency/quality signal
improves beyond the failed E18 aggregate.

### E19 result: core refusals fixed, agent efficacy not established

[All four reviews are valid and scored](adoption-e19-transfer2-results.json).
B/H credits64.80672/78.53276 (**21.18% higher H**), input385,285/592,749
(**53.85% higher H**). Pooled augmented P83.33%/75.00%, R25.00%/16.67%;
grounded recall6.25% in both arms. Judge credits70.55280 are separate.
Do not interpret passing lookup tests or higher indexed usage as efficacy.
No larger reviewer cohort is authorized.

All prior source/history/diff refusals disappear. H now actually requests
four indexed histories on the Rust case and one on C#, plus two C# source
queries. This exposes another concrete fidelity problem in the experimental
interface: its history route returns low-level immutable event/reference
metadata, **without the code patches already supported by production indexed
history**. The five responses carry1-2 events and2,005-4,424 serialized
characters each, zero patches; three are paginated. This is usable identity
provenance, but poor direct review evidence.

## E20: code-bearing history using the existing production patch renderer

The unified reviewer history route now renders compact event locations and
verified entity patches using the existing production `_event_patch` helper.
Retain every event ID/EID, commit/parent, identity decision, flags/material,
names and source/destination spans. Full opaque occurrence dictionaries are
not repeatedly printed when the available interface uses name/EID/file-line
selectors instead. No query-time AST extraction, synthesized change or event
selection. Raw primitive history outside the unified frontend is unchanged.

Patch generation follows independent Git/source validation. Oversized patches
are omitted whole with an explicit status, never clipped or described as
unchanged. Remaining history pagination preserves native event order and exact
query/generation binding; a presentation marker prevents mixing cursor forms.

[Offline replay](adoption-e20-code-history-preflight.json) changes the two
one-event Rust pages from3,339 characters/zero patches to complete three-event
pages of7,697/7,712 characters with three patches. Other responses are retained
with explicit code availability, not forced size-reduction claims. Four focused
tests verify full event/decision preservation, exact source paging, no future
source, code deltas and oversized-patch disclosure. The initial large-fixture
test incorrectly assumed a large function implies a large diff; it now changes
the actual fixture body and tests the real omission condition.

Next paid check is bounded to the same Rust canary first. Only valid native
receipts plus a joint cost/quality signal justify completing the already-declared
C# case. Same Sol/medium, native60,90 reserve,400/400 phase ceilings and shared
source-grounded judging. No25 launch, no rerunning an unchanged review after
failure and no claiming that code-bearing output alone proves lower agent cost.

### E20 canary: cost/recall pass, original precision gate fails

[The original canary](adoption-e20-canary-results.json) records B/H credits
43.66830/33.85668 (**22.47% lower H**), input371,081/189,290
(**48.99% lower H**). Both have two novel TP and augmented recall16.67%;
precision is100% B versus66.67% H because one H finding matches a reference
labeled FP. No grounded reference TP is found in either arm.
Judge costs55.21190 are retained. The automatic gate **stopped the second case**.

H makes five successful indexed source queries and **no history query**.
Therefore the observed savings cannot be attributed to the new history renderer.
Do not turn a favorable cost result into a causal or quality-success claim.

### Audit the precision-losing correspondence before accepting its meaning

Direct inspection finds a specific possible equivalence error: the candidate
describes unvalidated extension components escaping a storage parent; the
matched reference describes Windows/mixed-drive handling on a macOS-only project.
The matcher broadens these into generic "path validation" rather than comparing
their actual triggering inputs/platform and causal mechanism. The reference's
FP label must not automatically classify a distinct neighboring concern.
This observation does **not** establish that the candidate itself is TP;
reachability/guards/callers still require independent source-grounded classification.

Strengthen the generic equivalence rule: do not repair or broaden a reference
with source evidence; require the same stated cause, trigger and failure.
Shared file/API/category is insufficient. Apply that rule to **all five
candidates and all references**, not only the disputed edge. Reclassify all
unmatched findings under the same unchanged classifier. Freeze a separate
policy variant and retain original matching, classifications, failed gate and
all additional spending. No reviewer reruns, no desired TP/FP label in inputs,
no automatic second-case authorization from rewritten historical metrics.

### E20 audit outcome: matching improved, joint quality still fails

The [separate all-candidate policy](adoption-e20-equivalence-quality-results.json)
correctly treats all five concerns as unmatched rather than equating neighboring
causes. Its classifier then explicitly requests the included migration SQL to
evaluate the baseline's missing-index performance claim. That exact committed
SQL is supplied to **all five candidates, both arms**, without executing it.
No reviewer is rerun and the already-completed stricter matcher is not sampled again.

The new source-grounded classifications find one TP in each arm:
B has one FP, H two FP. Augmented P50.00%/33.33%, R9.09%/9.09%.
The alleged unrecoverable retention state is contradicted by the next cleanup
and missing-file recovery behavior; the extension-write claim lacks a reachable
unsafe input in the supplied callers. The candidate/reference equivalence
defect was real, but correcting it does **not** rescue the desired outcome.

Retain original55.21190 judge credits,51.13040 for stricter rematching plus
the uncertain classification, and26.78820 for the different-input SQL supplement.
These are quality-study costs, not reviewer savings. The original and revised
precision gates both fail; the second case and25-run remain unstarted.
Further reviewer rerolls or favorable-policy selection on this one PR would
not establish a reliable product win.

### No-paid follow-up: test the missing-recovery-context hypothesis

With the user unavailable for a scope decision, preserve the existing constraint:
no ungated25 run. Inspect evidence before building another context-expansion
feature. The [byte-checked actual-input audit](adoption-e20-recovery-evidence-audit.json)
shows **both reviewers already received** the missing-file recovery guard and
expired-row reselection code. It checks only visible preloads and successful
source outputs at exact HEAD, not unused private related-source sections.

Thus missing caller/recovery source is **not supported as the cause** of the
irrecoverability false positive in this case. More automatic context is not a
justified fix from this observation. Prompt availability does not prove correct
model attention or reasoning. No further reviewer/judge inference was purchased
for this audit, and no larger benchmark was silently authorized.

Current delivery remains verified product/API/index-query improvements plus a
fully preserved development benchmark record, **not** a demonstrated joint
credit/precision/recall win. All50 offline packets remain ready. The unstarted
second case and25 task cohort are conditional remaining work, not completed
deliverables or zero-finding reviews.

## E21: readable unchanged evidence and first-pass disconfirmation, on different tasks

Continue autonomously without rerolling the precision-losing PR. The recovery
audit ruled out a missing-source explanation there. Test presentation and
review discipline instead of adding more source or filtering final findings:

- A lossless readable presentation shows literal source lines in dynamically
  sized fences, with all original metadata/coverage retained. Canonical visible
  JSON remains separately stored; actual presented bytes and the native prompt
  digest are attested. Embedded fences, Unicode, CRLF and non-Unicode Git patch
  bytes have explicit lossless handling. No source bodies/fields are dropped.
- Both arms get the same first-pass finding checks: reachable trigger, input
  boundary, guards, intervening changes, retry/recovery and actual callers.
  Report every supported issue, not a quota. No extra model proof pass, late
  deletion stage, reference-label hint or fabricated test execution.
- Flags default to the historical JSON/old instructions, preserving old receipts.
  The new modes are explicitly frozen in new protocols.

[All50 offline packets](adoption-e21-readable-preflight.json) fit the same
100,000-character ceiling; aggregate presentation size2,819,134 versus2,880,188
canonical characters. This modest size difference is not a native-token or
quality result.81 focused rendering/protocol/model/judge regressions pass,
including actual CRLF prompt transport and tamper detection.

Prospective three-case selection uses seed20261020 to shuffle history-ready
starter positions excluding0/6/8/15/17/20. The first three are3/4/7, selected
without reading reference labels or quality outcomes. They are different from
the repeatedly iterated five-case set, but remain development starter data.
Fixed Sol/medium, native60,90 reserve,600 reviewer/400 judge phase ceilings,
no reviewer retries. Inspect the first native pair before the remaining two;
assess the entire declared small cohort before any25 launch.
This is an engine-plus-presentation/workflow treatment, not proof of a
persistent-history-only effect. Keep both arms' prompts and judgments symmetric.

### E21 result: stronger useful baseline, no joint win

[All three prospectively selected pairs](adoption-e21-new3-results.json)
complete and score with no manual source supplement:
B/H review credits109.35370/110.25668 (**0.83% higher H**),
input704,918/678,140 (**3.80% lower H**).
Pooled augmented P100%/93.33%, R41.07%/32.73%;
macro grounded R36.11%/29.63%. Judge credits97.53380 are separate.
The useful baseline is substantially higher on this different development
cohort, but H loses quality. No25 authorization.

A bounded source-only trace diagnosis, without accessing reference labels,
finds a concrete discoverability gap: the first H packet selects8 of30
candidate source blocks, omits22, and reports40 no-callable plus2 oversized
gaps only as aggregate counts. Its29 calls include clipped raw patches but
do not systematically recover the omitted changed ranges. Existing warnings
are honest, yet do not tell the agent which precise changed units remain
unseen or provide direct range locators. Complete file inventory alone is
not complete changed-unit discoverability.

Implement a compact complete changed-hunk manifest with individual delivered
coverage and direct source locators, preserving identical neutral ranges/raw
allocation for B/H, without forcing calls or selecting benchmark issues.
It must not treat one delivered block as covering every change in that file,
silently truncate missing ranges, or claim deleted identity from a zero-count
hunk. Implementation remains bounded and offline before further inference.

### Independent uncertainty assessment of the earlier promising E17 five-case set

[Repository-cluster bootstrap](adoption-e17-fivecase-uncertainty.json) reuses
the existing paired-report resampling method,10,000 draws, seed20261020;
pooled-precision count arithmetic is cross-checked against the pinned scorer.
The95% credit-reduction interval is **-8.03% to+8.23%**, input reduction
**-27.39% to+12.99%**, grounded recall H-B **-5.68pp to+7.53pp**, and pooled
augmented precision H-B **-7.94pp to+5.56pp**. Thus the earlier1.39% saving
does not establish cost superiority or the strict noise/recall margins.
These are descriptive intervals on adaptively used development data, not
post-selection-valid confirmation. Five PRs also cannot satisfy the S4
requirement of known TP delivery on at least20 confirmation PRs.

### Independent native-query usability measurement

[A published4,354-definition LANdalf scope](indexed-query-qualification-20261020.json)
is measured with100 calls per operation across five fresh sessions, exact pinned
HEAD and generation, without rebuilding or executing project code.
Warm native p95: qualified resolution113.42ms, state82.64ms, first history
page111.10ms. Five fresh opens range up to749.65ms, median249.58ms.
Hardware/software details are recorded. The proposed small-repository250ms
warm-call diagnostic is met for this named scope; five opens are descriptive,
not a confidence-backed latency SLA. This does not establish large-repository
indexing speed or review quality, and excludes rendering/model time.

## E22: complete changed-range discovery under the unchanged budget

The initial packet now retains every textual Git `--unified=0` hunk, aligned
to the complete changed-file inventory. Each range has an explicit delivered
coverage code computed from unions of **visible current-source blocks only**.
Same-file membership and unused private related bodies do not count as coverage.
Zero-count/deletion ranges are adjacent-context-only, never deleted identity.
Shared legends provide exact decoding and valid raw/source locators for both arms.

First real full25 offline qualification was48/50: the408-file/1,360-hunk task
could not fit verbose manifest objects. It was not dropped and the100,000
budget was not raised. Inventory-aligned delta rows and shared defaults retain
all coordinates plus per-hunk coverage compactly. The compressed representation
has an explicit stateful decoding legend; no coordinate guessing is required.
Both `raw_diff(path)` and unified `gita_review(operation='raw_patch',path=path)`
are named; `read_file` is available in both arms.

[Corrected full25 qualification](adoption-e22-manifest-qualification.json)
is **50/50 ready**, with identical B/H inventories/raw patches.
The largest packet is98,931 serialized characters, including all1,360 hunks;
the affected manifest alone is14,505 characters.37 owned manifest/rendering
tests and96 integrated context/protocol/model/judge tests pass. Existing
readable source and receipt tamper/CRLF checks remain in force.

Prospective comparison: same declared positions3/4/7 and seed20261020 as E21,
no task substitution after quality inspection. Keep readable evidence,
first-pass finding checks, on-demand related source, fixed Sol/medium,
native60/reserve90 and600/400 phase ceilings unchanged. Complete manifest
metadata consumes budget before source allocation equally in both arms;
source/raw allocations can consequently shrink relative to E21. This is an
explicit evidence-selection change, not a free-token claim. Inspect the first
native pair, then assess the entire three-case development cohort before25.
No late filtering, reruns or inferred absence from omitted source.

### E22 result: preserved precision/recall, token signal, slight credit regression

[All three cases complete and score](adoption-e22-complete3-results.json).
B/H credits105.37536/106.66390 (**1.22% higher H**), input681,961/584,625
(**14.27% lower H**). Both pooled augmented precision100%;
grounded recall40.74%/42.59%, augmented recall41.82%/43.64%.
Judge credits95.81680 are separate. Better source discovery is consistent with
this point signal, but cost superiority is not established. No25 authorization.

The small Python case accounts for the unfavorable credit difference:
H22.89280 versusB14.93532 and two refusals. A source-only replay shows that
`send_request_to_open_ai` is uniquely published in a different provider file;
the requested path was its importer, not its declaration. `DB_PATH` is a real
literal constant but not a published indexed entity. Strict source lookup
correctly refuses both requests as phrased, yet incurs an avoidable search/read
round trip for a normal caller-oriented context request.

## E23: explicit context lookup, without weakening strict source semantics

Add a distinct `context(token,path_hint)` operation to the unified interface.
Existing `source` remains strict. Context first tries the hinted indexed scope,
then a unique global published name, explicitly disclosing a lexical match
outside the hint rather than claiming import/caller binding. For an unindexed
simple identifier in an exact member file, it can return bounded exact Git
literal context, marked **raw references, not an indexed definition**.
Ambiguity remains explicit, source/commit checks and safe pagination remain.

Raw fallback is excluded from indexed-use accounting. Shared judge context
retains the actual provider/raw-source path, not merely the original importer
hint. Completed exact-line receipts also bind that actual path. No index
rebuild, source parsing, caller-graph claim or manufactured constant identity.

[Offline replay](adoption-e23-context-preflight.json) turns the two recorded
refusals into one1,841-character verified indexed definition and one2,687-character
explicit raw window. Original traces remain unchanged. Ten source/context/
history tests and64 protocol/judge/model/rendering tests pass.
This is prototype reviewer-interface work reusing production indexed source,
not yet a separate public MCP context API or a demonstrated efficacy result.

Prospective gate: the same three cases, starting with position4 to exercise the
two diagnosed requests, then3/7 only if the canary's native receipts and joint
cost/quality point gate pass. Same Sol/medium, native60/reserve90,600/400 ceilings,
complete manifest/readable source/first-pass checks and strict source-grounded
judging. No reroll on failure, no25 launch from offline query success.

### E23 canary: cheaper, equally precise, but official coverage still lower

[Original stopped canary](adoption-e23-canary-results.json): B/H credits
14.64438/12.33132 (**15.80% lower H**), input70,615/45,542
(**35.51% lower H**), both augmented precision100% and two TP findings.
Official grounded/augmented reference recall33.33%/22.22%. Direct audit of
all four correspondences shows B's broader second finding also covers the
separate reference about a mock side effect mutating global history.
Do not replace official coverage with equal finding counts or rematch until
it passes. The original continuation gate remains failed; other two cases
are not run under that protocol.

### Native cost partitions identify a different overhead than total input

E22 H's lower total input is mostly fewer cache-read tokens. B/H cache-write
tokens128,442/133,857, output9,496/10,841 and model requests20/21 explain why
lower cumulative input did not yield lower native credits. Actual measured
credits remain primary; do not model all input tokens as equally priced.

## E24: remove asymmetric navigation coaching, retain all capabilities

The unified interface now documents source/context/diff/history itself.
The reviewer prompt still contains substantially more H-specific navigation
coaching and receipt instructions than B, even after the shared preload and
finding checks. Test a minimal availability profile: same base review goal,
same first-pass checks and initial-evidence instructions for both arms, with
tools described by their frozen typed schemas. No H-only mandatory start/map/
source workflow or extra receipt paragraph. All tools, index checks, raw fallback,
source content, complete manifest and resource ceilings remain unchanged.

This explicit protocol flag defaults off for historical replay. Assess the
same complete three-case set3/4/7 under one prospectively frozen protocol;
first native pair is a transport/integrity canary, not a per-PR winner selector.
Keep all three outcomes for the joint cohort gate. Same Sol/medium,
native60/reserve90,600/400 phase ceilings and source-grounded judge policy.
No more optimistic label reinterpretation or late filtering; no25 authorization
without the cohort outcome. This tests actual interaction overhead and choice,
not an assertion that a shorter prompt guarantees quality.

### E24 technical stop: legitimate empty source was misclassified as corruption

[The interrupted cohort](adoption-e24-stopped-results.json) has141.62780
measured review credits across four attempted sessions. Only the first pair is
fully quality-scored; its favorable H cost/quality is conditional, not a cohort
win. The third task is unstarted. The terminal stop is retained permanently.

The failing H calls read two real empty `__init__.py` files. `read_file` correctly
returned empty text and zero lines, but the shared delivery ledger split `""`
into a purported unnumbered line and raised integrity. This is a harness
empty-source handling defect, **not false indexed bytes**. It must be fixed
rather than ignored, and the interrupted review is not retroactively made valid.

## E25: validate exact empty source without fabricating line receipts

Read-file coverage now explicitly records whether committed bytes are empty.
The delivery ledger accepts an empty response only when independently read Git
source has zero lines, response `file_lines` is zero and the producer marks empty
source. It creates no line receipt. A forged empty response for nonempty committed
source still raises terminal integrity.

[Exact no-model replay](adoption-e25-empty-source-replay.json) confirms both
stopped queries now return truthful empty evidence and no receipt, without
altering old logs or the original terminal gate. Twelve focused source/history/
empty/context tests pass. Resume only through a fresh protocol/run root with
the same three cases3/4/7 and minimal workflow candidate, not a ledger reset.
Keep all measured E24 costs and missing slots in its report. Same Sol/medium,
native60/reserve90,600/400 phase ceilings and unchanged source-grounded judging.
No25 inference until a valid complete small cohort meets the joint gate.

### E25 completed: operational recovery, negative efficacy result

[All three paired outcomes](adoption-e25-emptyfix3-results.json) are retained.
Six valid reviews, no terminal stop, all three pairs scored. B/H review credits
101.24972/114.08340 (**H +12.68%**), input543,845/605,731 (**H +11.38%**),
output9,359/12,273. Judging costs128.07950 separately.
Pooled augmented precision94.12%/92.86%, recall37.50%/37.50%;
macro augmented precision95.83%/96.30%, recall44.35%/34.09%.
Equal pooled recall does not erase the per-task/macro loss.
Only one H session requested indexed tools (two attempts, one successful source).
The other two used no indexed calls. This is not evidence of a history advantage.
The empty-source repair is operationally validated; the treatment fails the
joint efficacy objective. No25 expansion and no rerun of unchanged reviewers.

## E26: separate source preloading from on-demand indexed tools

E25 still conflates different source selection with tool availability: B receives
raw HEAD windows, H receives selected indexed callable bodies. Whether that
selection hurts review coverage is a hypothesis, not an established cause.
Freeze a controlled ablation with **identical raw initial packets in both arms**.
The explicit `indexed_preload=False` setting is hashed into the protocol;
sealing refuses unequal B/H packet digests. Default behavior remains unchanged.
Both keep the complete hunk manifest, same budget, readable rendering and
first-pass checks. H retains its qualified indexed tools on demand, B does not.
Raw preload is not counted as indexed usage.

Use the same ordered three development cases3/4/7, Sol/medium for both reviewers
and judges, native60/reserve90,600/400 phase ceilings. First pair validates
transport only; keep all three outcomes regardless of per-case direction.
No adaptive task selection, extra verifier, reference-guided source selection,
unchanged-review retry or25 expansion. This ablation tests a mechanism; it
does not promise a win or qualify as independent confirmation.

### E26 completed: efficiency and adoption improve, recall does not

[Full cohort results](adoption-e26-common-preload3-results.json):
B/H review credits108.68900/89.30794 (**H -17.83%**),
input715,110/425,290 (**H -40.53%**), output10,142/9,229.
Judge spend127.26220 separately. Six valid reviews, all three pairs scored.
Both pooled and macro augmented precision100%.
Pooled grounded recall42.59%/33.33%, augmented recall45.61%/34.55%;
macro augmented recall52.61%/37.59%. Joint objective fails.
All three H sessions request indexed tools; eight successful source queries
and two source lookup failures. No history/diff advantage is established.
Identical preloads do not establish source selection as the cause of prior
recall losses. Do not rerun unchanged reviewers or expand to25.

Trace audit without reference labels reveals two concrete evidence-use problems:
recursive path filters silently omit zero-depth members; and five of eight
successful source queries return partial bodies with explicit continuations.
The latter is an incomplete-delivery observation, not proof that a missing
finding was caused by pagination. Actual continuation consumption needs separate
accounting; success counts alone overstate complete indexed evidence delivery.

## E27: repair shared search/list path semantics before more inference

An authored Git fixture reproduces the real failure: `src/**/*.py` returns no
match for `src/code.py`. The broker uses `fnmatch`, whose `**/` requires at least
one slash. A literal directory such as `src` also returns no member. These
are shared B/H broker defects, not evidence of incomplete entity indexes.

One shared path predicate preserves every prior fnmatch match, supports
zero-or-more complete `**` components with bounded iterative matching, and
accepts literal directories. Search/list both report `coverage.matched_files`,
distinguishing an empty path scope from no literal content matches.
Descriptions document these semantics. Both arms get the same repair.
No index rebuild, goldens, target-program execution or hidden source supplement.

[Exact no-model replay](adoption-e27-search-replay.json) recovers four
`insertBanner(` and three `lastFontUsed` matches for H's previous empty queries,
and four `defaultFont` matches for B's directory query. Existing exact-file and
nested glob results remain unchanged. Original E26 receipts/judgments stay fixed.
The failing repro preceded the fix;92 evidence/protocol/readable tests pass.

Next small qualification uses the same frozen E26 design and complete ordered
three-case cohort, changing only the shared broker repair. Same Sol/medium,
native60/reserve90,600/400 ceilings, no automatic retries or per-case selection.
This tests the repair's effect; it is not guaranteed quality improvement.
Keep25 blocked unless the joint cohort gate is supported.

### E27 completed: quality signal, no indexed efficacy claim

[All outcomes and both judge policies](adoption-e27-searchfix3-results.json)
are retained. B/H review credits105.88706/106.19760 (**H +0.29%**),
input677,264/620,034 (**H -8.45%**), output9,371/10,401.
Original judging100.45050 credits; separate requested-source stage19.16320.
All six reviews valid. Original policy scores two pairs and leaves one uncertain.

The classifier specifically requested the Poetry lock and fixture definitions.
The separate frozen input includes all39 tracked paths, all tracked Python
source and the complete parsed lock, omitting only package wheel/sdist hash
inventories. All dependencies and other fields remain. No candidate/matcher
rerun or reference-guided reviewer input. With this symmetric evidence, all
three pairs score: pooled augmented P100%/100%, R29.09%/44.64%;
grounded R27.78%/42.59%; macro augmented R27.59%/52.04%.
Original incomplete scores remain separately labeled. Supplement assembly
initially failed the strict scorer because correspondences were not ordered
by arm-local candidate index; production ordering was restored using the
saved classifier answer, without another model invocation.

H uses one successful complete indexed source query in one of three sessions;
two source lookups fail. The other two H sessions request no indexed tools.
Therefore the aggregate quality difference cannot be attributed to indexed
history or source. Shared search repair, tool availability and model variation
remain distinct possible influences. No strict credit-reduction win and no
confirmation claim. Stop tuning these same three tasks.

## E28: prospective unused-task transfer, unchanged E27 candidate

Freeze the E27 implementation/settings before seeing new reviewer outcomes.
Sample three positions with `random.Random(20261021).sample`, from the16 positions
not previously selected in focused development pilots:
`[1,2,5,9,10,11,12,13,14,16,18,19,21,22,23,24]`.
The resulting ordered positions are **21,16,22**:
Mcp-Docker, pika and wirechat. The last has endpoint indexed context but no
connected history; retain it rather than selecting only history-ready tasks.
These are unused by focused pilots, not a new held-out benchmark population.

Same Sol/medium reviewers and judges, readable common raw preload,
minimal symmetric goal, complete hunk manifest, first-pass checks and
qualified on-demand H tools. Same600/400 review/judge ceilings,
native60/reserve90. First pair checks operational validity, not favorable
efficacy. Keep every selected case and all missing/failed slots. Do not
promote to25 from a favorable subset or change the candidate mid-cohort.

### E28 completed: prospective transfer fails; do not expand

[All selected transfer outcomes](adoption-e28-transfer3-results.json) are
retained, including the endpoint-only task. B/H review credits112.14360/118.42238
(**H +5.60%**), input566,638/645,440 (**H +13.91%**), output14,215/14,915.
Judge spend114.06620 separately. Six valid reviews, all three pairs scored;
no supplementary judge inputs or model retries required.

Pooled augmented P100%/100%, R23.21%/21.43%;
grounded R18.87%/16.98%; macro augmented R22.86%/20.27%.
This does not meet either the efficiency or quality objective. Do not average
it away with E27's favorable reused-task result or run25 on that basis.

Meaningful indexed history was actually used in one H session: two history
queries returned four events with three code-bearing patches. One history page
still had a continuation. The other two H sessions requested no indexed tools.
Neither requested named indexed source. This is better evidence of real history
use than status-only counts, but not a demonstrated aggregate quality/cost gain.

### Completed source bodies, not just successful requests

[E26 cursor-consumption audit](adoption-e26-source-delivery-audit.json)
follows actual issued source cursors through later successful tool calls.
Eight successful named-source requests yielded three complete first responses
and **four complete bodies after continuations**. Four remained partial.
One class request consumed a second page but still did not finish its body.
No source execution, new inference or reference labels were used in this audit.
Partial evidence was explicitly flagged, not incorrect Git bytes; whether
more conspicuous presentation improves completion is still untested.

### Latest complete pilots at a glance

Values are B/H; precision/recall below are pooled augmented values.
These adaptive experiments must not be pooled into a claimed confirmation win.

| Trial | Review credits | Input tokens | Precision | Recall | H sessions requesting indexed tools |
|---|---:|---:|---:|---:|---:|
| E25 | 101.25 / 114.08 | 543,845 / 605,731 | 94.12% / 92.86% | 37.50% / 37.50% | 1/3 |
| E26 | 108.69 / 89.31 | 715,110 / 425,290 | 100% / 100% | 45.61% / 34.55% | 3/3 |
| E27, dependency-complete judge stage | 105.89 / 106.20 | 677,264 / 620,034 | 100% / 100% | 29.09% / 44.64% | 1/3 |
| E28, prospective transfer | 112.14 / 118.42 | 566,638 / 645,440 | 100% / 100% | 23.21% / 21.43% | 1/3 |

These four trials cost855.98070 review credits and489.02160 judge credits
(including E27's requested-source stage), total1345.00230. Earlier experiments,
technical stops and indexing/preparation costs remain separately retained;
this is not an all-project cost total. No paid process or25-task campaign
is left running after this transfer check. Further paid comparisons need a
new, independently verified mechanism, not another unchanged reroll.

## E29: complete source delivery within verified native transport bounds

The source-consumption audit establishes an actionable delivery problem, not
just low tool counts. Add an explicit `large_evidence_pages` profile, default
false. The frozen protocol binds it identically to B/H broker configuration.
Historical8000-character responses/6000-character pages remain the default.
The new profile allows16000 serialized ASCII characters and14000 source
characters, additionally bounding the JSON-embedded string to20000 characters
so escaping cannot bypass the transport margin. Paginated results remain
lossless; oversized nonpaginated metadata fails explicitly.

[No-model replay](adoption-e29-complete-source-replay.json) completes all
eight recorded named bodies in their first response, versus three previously.
Largest response12969 serialized characters/13348 embedded characters.
Source bytes, generation and complete-line receipts are independently verified.
Both arms receive the same larger raw/source page allowance; initial100k
preload allocation is unchanged. This is not an asymmetric context-budget gift.

### Native qualification found and fixed a second budget boundary

The first authored canary failed during Windows parser-worker setup because
its launcher lacked a spawn-safe main guard. Preserve that zero-inference-cost
preparation root; use a guarded launcher and fresh roots thereafter.

[The next native attempt](adoption-e29-native-transport.json) proves recovery
of hidden source tails but is **not** a clean named-source qualification:
the public adapter's inner6000-token whole-answer budget refuses a heavily
escaped body before the frontend can paginate. Three tool errors and8.85450
credits are retained. A passing final answer does not erase those errors.

Separate bounded local retrieval from agent disclosure: only the larger-page
profile requests64000 adapter tokens internally; each model-visible result
still obeys16000 serialized/20000 embedded characters. Public MCP defaults and
the original small-page profile remain unchanged. A failing authored regression
reproduces the inner refusal before the fix.

[Fresh clean native qualification](adoption-e29-native-transport-v3.json):
Sol/medium, four calls, zero errors; unpredictable ASCII and escaped-source tail
markers recovered exactly, no source execution or builtin view access.
Largest responses14055 serialized and19052 embedded characters. Cost10.51876
credits, separately from review/judging efficacy. This is transport evidence,
not benchmark quality.96 broader regressions passed before the inner-budget
fix;12 focused named-source/profile/escaping tests pass after it.

### Prospectively frozen efficacy check

Retain E28's complete ordered positions21/16/22, seed20261021, unchanged common
raw preload, minimal symmetric goal, Sol/medium review/judging and600/400 phase
ceilings/native60/reserve90. Change only the explicit larger-page profile and
its verified bounded retrieval path. These cases are now development reuse,
not newly unseen transfer. First pair checks transport, not winning outcomes;
retain all three cases, all failures and all source/history delivery counts.
No25 authorization from no-model completeness or the canary alone.

### E29 completed: lower cost, recall still fails

[Complete results](adoption-e29-completepages3-results.json): B/H review
credits146.08262/125.82628 (**H -13.87%**), input801,251/644,600
(**H -19.55%**), output15,087/15,961. Judging138.31920 credits;
transport canaries19.37326 separately. Six valid reviews, three scored pairs,
no reviewer/judge retries or supplementary inputs.
Pooled augmented P100%/100%, R25.86%/22.03%; grounded R18.87%/13.21%;
macro augmented R24.36%/21.81%. Do not promote the efficiency point estimate
while recall is lower.

H requests three successful indexed history calls on Mcp-Docker.
The Swift/PHP sessions attempt source/history/context queries but retrieve no
indexed bodies. Direct published `entities_in` inspection shows zero entities
for two real Swift files and the PHP chat class. All paths exist at HEAD.
These extensions were absent from the language registry, not merely missing
grammars in an otherwise complete index. Published generations and a connected
history do not establish language/file coverage.

## E30: real Swift/PHP extraction and cross-process grammar installation

Register Swift and PHP in the shared legacy/index extraction rules. Recognize
named classes/structs/enums/protocols, functions/methods, Swift initializers,
deinitializers/subscripts/type aliases, PHP traits/properties/constants and
assigned closures. Preserve literal whitespace and byte-exact source spans.
Support bracketed PHP namespaces and one unbraced file namespace. Multiple
unbraced namespaces produce an explicit unknown rather than incorrect names.
Swift multi-binding properties remain owned by the enclosing entity rather
than pretending a declaration is a single binding.

Update installation documentation and CI's explicit grammar provisioning.
Use an isolated copied native cache, leaving the existing shared13-grammar
inventory untouched. Upstream1.14.3 `download()` reports requested names without
materializing missing files; explicit `prefetch()` and native AST probes verify
the new Swift/PHP grammars. No benchmark programs, tests or hooks are executed.

The first broad validation passes189/191 cases; two F3 checks expose a real
cross-process installation mismatch: the controlled CLI environment discards
the selected cache. Add `GITA_PARSER_CACHE`, an explicit absolute cache shared
by extraction, CLI/MCP and spawned parsing. Hash the cache-selection helper in
the parser policy. The verification runner retains this installation setting;
the benchmark freezes it and injects only that frozen value into its MCP
environment instead of relaxing the native sandbox's general GITA-variable
filter. A changed cache rejects protocol reuse. The54 focused rerun checks pass,
including published identity/source/history for both new languages, F3,
offline grammar behavior and frozen MCP configuration. Earlier103 extraction/
provisioning checks passed; do not sum overlapping test counts.

### Fresh contexts and exact failed-query replay

[Independent qualification](adoption-e30-language-qualification.json)
builds new `rb-e30-*` contexts; old generations are retained, never relabeled.
The three previously uncovered target files now contain25/103/83 definitions
respectively (**211 total**, including their file roots). Counts match fresh
independent extraction; every published source span matches committed Git bytes.
All five original source requests now complete: ContentView, swap, mount,
initializeConversation and pushMessage. No paid inference.

Cold bounded rebuild-plus-preflight wall times were546.375s (Mcp-Docker),
922.250s (pika) and47.406s (wirechat endpoints). These are substantial, not a
claim that whole-repository indexing is already fast enough. The prior map
[readiness probe](adoption-e30-map-preflight.json) remains transport/readiness
evidence only; no workflow-only paid trial was launched before fixing coverage.

## E31: support the actual benchmark language mix, not only selected PRs

The user requests all major benchmark languages. [Pinned manifest audit](
reviewbench-language-coverage.json) reads only declared repository-language
metadata, not reference findings. Swift/PHP were **not** the final gaps:
even the25 starter contains Java, Shell, Kotlin, Ruby and Jupyter Notebook.
The full219 has additional C, Dart, PowerShell and HCL tasks.

Implement and independently test the remaining eight plain-language/config
families: Java(7), Shell(7), Kotlin(4), HCL(2), C(1), Dart(1),
PowerShell(1), Ruby(1). Notebooks(3) and Go templates(1) are mixed/container
formats; plain Python/Go adapters are not sufficient. One task's declared
language is unknown. These counts describe repository primary languages,
not proof that every changed file uses that language or is covered.
Keep actual changed-file coverage qualification separate from registration,
and do not launch25 merely because all primary language names are listed.

### E31 implemented: eight adapters, actual source and conservative identity

Add Java, Kotlin, C, Ruby, Bash, PowerShell, Dart and HCL/Terraform to the shared
extraction/byte-index rules. Include Java/Kotlin package scope, C declarator
names/prototypes/function macros, Ruby module/class/method bindings, Kotlin
properties, PowerShell declarations and HCL block/attribute addresses.
Update CLI provisioning, CI, quickstart, technical boundaries and README.
Explicitly provision native grammars in a fresh23-grammar cache; retain the
previous15-grammar E30 installation and original shared13-grammar cache.

Dart requires a real source projection: its grammar splits signatures and bodies
into siblings. One typed, byte-preserving projection joins only directly paired
regions (allowing intervening comments), retains exact original span/line data
and projects nested local functions under the correct callable. Shared traversal
keeps inner-body edits out of outer owned tokens. Unrecognized members remain
in enclosing source, not falsely complete header-only bodies.

Initial tests catch phantom Ruby entities from unnamed `class`/`module` keyword
tokens; those are excluded. A tiny HCL value does not survive a cross-file move
under the existing minimum-body/copy guards. Do not lower the guards to pass:
substantive HCL evidence preserves edit/move identity, while a separate regression
requires conservative re-observation for the tiny value. A multiline-literal
probe catches HCL heredoc whitespace being lost; preserve that literal as bytes
and test both legacy and indexed token evidence.

The broad extraction/publication/query/compare/protocol run passes264 checks.
After the final heredoc correction,44 focused adapter checks pass. These overlap;
do not sum them into a unique total. The adapter tests verify declarations,
parents, exact source digests, significant literal whitespace, real SQLite
publication, exact historical source and edit/move identity in all eight
families, plus conservative tiny-HCL handling. No corpus program is executed.

### E31 actual-file coverage, including all failures

[All25 changed-file endpoint audit](reviewbench-e31-changed-file-coverage.json):
1261 file endpoints;1110 complete parses,126 unsupported extensions/formats,
25 parse errors. Every emitted definition's source-span digest is checked
against original Git bytes. The25 parse errors are22 TypeScript,1 TSX and2 Swift.
Unsupported files include58 Astro endpoints as well as assets, templates,
Dockerfiles, CSS, SQL and other formats. Retain every file in the denominator.
The audit takes143.734s with no paid inference; it is not an index-build SLA.

[All five additional C/Dart/PowerShell/HCL tasks](
reviewbench-e31-extra-language-coverage.json) are sourced from the pinned official
mirrors, with no labels, future fixes, corpus execution or task selection:
- Dart:7/7 endpoints parse;174 named definitions.
- PowerShell-primary task:8/10 endpoints parse, including both PowerShell
  endpoints and six JavaScript endpoints; two CSS endpoints are unsupported.
- HCL tasks:234/234 endpoints parse, including229 HCL,4 Bash and1 JSON.
- C:both FreeRTOS `tasks.c` endpoints fail whole-file parsing. This is a real
  remaining gap, not a successful C benchmark qualification.

Bounded diagnosis verifies original bytes and LF line endings. Native C reports
55/56 syntax errors; C++ compatibility parsing still reports33/34 errors.
Macro-heavy declarations and preprocessing prevent a whole-file proof.
Do not accept a compatibility parse with errors, preprocess/execute the corpus,
or publish selected declarations as a complete population to hide the failure.
The basic C adapter works in authored publication tests, but raw fallback remains
necessary for this benchmark task.

[Post-implementation primary-language inventory](
reviewbench-language-coverage-e31.json) has registered adapters for214/219 task
labels. This is **not** a214-task coverage or quality claim. Notebook/template/
unknown labels and the actual C/Astro/syntax gaps are separately recorded.
The original pre-change inventory is preserved.

## E32: qualify the reviewed PR window before another paid comparison

Freeze the E31 product and larger-page treatment on the same complete ordered
positions21/16/22, seed20261021. Build fresh `rb-e32-*` contexts with full
repository inventories but history bounded to effective merge base through HEAD.
Endpoint-only tasks retain two explicit snapshots and unavailable connected
history. Both arms retain the same full allowed raw Git graph, including the
prior-history fallback. Record the narrower indexed window explicitly.

This is a different preparation profile from the100-prior-commit cold E30
rebuilds. Reduced preparation time must not be described as an equivalent-work
whole-history speedup. It supplies the exact reviewed source and persistent
identity through the PR's first-parent span, not arbitrary older indexed history.
Do not relabel old contexts or claim that the remaining C/Astro/format gaps
were repaired.

Before inference, independently verify changed endpoint source and the five
formerly missing Swift/PHP requests in the new contexts. Freeze identical raw
initial packets in B/H, the same typed tools and source budgets, minimal workflow,
Sol/medium reviewers and judges, native60/reserve90 and600/400 ceilings.
All three cases stay in the cohort; first-pair checks are operational, not
selection of a favorable outcome. No25 expansion from preparation readiness.

### E32 completed: coverage repair is real; recall parity still not established

[All paired outcomes](adoption-e32-languages3-results.json): B/H review
credits128.26860/113.17166 (**H -11.77%**), input523,582/526,561
(**H +0.57%**), output13,564/14,529. Judging124.79220 separately.
Six valid reviews, all three pairs scored, no supplementary judging or retries.
Initial packet digests match B/H and the E29 packets exactly.
Pooled augmented P100%/100%, R26.32%/21.82%;
grounded R20.75%/18.87%; macro augmented R25.84%/21.65%.
No strict quality/cost win and no25 expansion.

The Go session makes four successful indexed history calls with seven
code-bearing patches. Swift/PHP sessions request no indexed tools despite the
qualified repaired bodies. That is observed availability versus adoption, not
evidence that the repaired queries return incorrect source.

Native accounting also differs: B/H cache-write48,936/92,359 and uncached
input160,241/57,128. Do not attribute measured credit savings to lower total
input (which did not fall), or extrapolate provider-cache partitions into an
intrinsic Gita pricing claim.

## E33: capability-aware index-first workflow, not an indexed-call quota

E32's optional minimal profile still follows mostly raw navigation. Test the
existing index-first workflow on the repaired product: start with the real
indexed changed-code map, then select source/entity deltas and use raw fallback
where appropriate. Both arms retain the same objective, first-pass proof checks,
raw initial evidence, source allowances and allowed commits. B receives its
explicit raw workflow; H its capability-aware indexed workflow. No mandatory
history calls, no additional verifier or late filtering, and no finding quota.

Readiness audit finds that the unified facade advertised diff/history even for
endpoint-only preparation, unlike the older separate-tool profile. Filter its
operation enum without mutating shared schemas, enforce availability before
querying, and make descriptions/coaching explicitly direct endpoint snapshots
to raw before/after and history tools. Do not coach unavailable operations.
This is part of the candidate, not a retrospective alteration of E32.

Freeze the same complete three development cases21/16/22, seed20261021,
E32 qualified contexts and larger-page profile, changing minimal-workflow to
false plus the necessary discovery enforcement. Sol/medium all review/judge,
native60/reserve90,600/400 ceilings; exact first pair is operational only.
No unchanged-review retry, per-case winner selection or25 expansion before
the complete cohort quality/cost result.

### E33 completed: instruction alone does not establish a win

[All costs and both judge stages](adoption-e33-indexworkflow3-results.json):
B/H review credits124.62396/121.31098 (**H -2.66%**),
input684,579/703,800 (**H +2.81%**), output11,463/12,830.
All six reviews valid. Original judging140.17770 credits scores two pairs;
the third classifier requests the complete Chat.php methods and query helpers.

A separate frozen source stage validates and replaces only three same-HEAD
numbered windows with the complete committed Chat.php. No other evidence is
removed, no review/matcher is rerun, and context176397 stays within180000.
Additional judging25.68870 credits. The two outstanding classifications both
resolve TP; original incomplete grading is preserved.
Full pooled augmented P91.67%/83.33%, R25.00%/21.43%;
grounded R20.75%/16.98%. No25 expansion or successful-outcome claim.

Only PHP requests indexed source (nine successful calls); Go/Swift remain raw.
The source ledger elides71 previously delivered lines in PHP, zero in the other
sessions. This treatment-side harness behavior is separate from persistent
identity/history; do not attribute every cost difference to indexing.

### Confirmed preload interaction

Actual prompt construction places a later instruction after the workflow:
"Begin with this evidence; do not repeat a start/map/source call merely to
satisfy the workflow above." The shared packet can contain100k characters of
raw/source evidence and a complete changed-hunk manifest. Avoiding a redundant
map is consistent with that instruction, not proof that the map is broken.
E33 therefore did not isolate a genuinely tool-led first investigation.
Source selection and finding completeness remain hypotheses, not established
causes of the precision/recall losses.

## E34: tool-led review without automatic code preloading

Use the same frozen product, qualified PR-window contexts, complete ordered
three development cases21/16/22, seed20261021 and capability-aware B/H workflow.
Set `initial_context=False` for **both** arms. Supply no preselected code,
packet map, reference clues or initial source windows. Both obtain all evidence
through their allowed tools with the same40-call budget and16k response profile.
H's first indexed changed-code map now supplies actual missing initial evidence,
rather than a duplicate of a large shared packet. Raw fallback is retained.

This tests an ordinary tool-led agent workflow, closer to the legacy AACR
interaction pattern, not a model upgrade, guaranteed efficacy improvement or
held-out confirmation. Same Sol/medium reviewers and judges, first-pass checks,
native60/reserve90 and600/400 phase ceilings. Keep all three cases and all
cost/quality outcomes; no unchanged retry, irrelevant history quota or25
authorization based only on greater tool use.

### E34 completed: first promising complete tool-led cohort

[Full results and both judge stages](adoption-e34-toolled3-results.json):
B/H review credits124.77530/98.66282 (**H -20.93%**),
input814,908/533,327 (**H -34.55%**), output13,362/14,129;
model requests30/25 and API seconds242.389/218.316.
All six reviews valid. Original judging100.46370 credits scores two pairs,
leaving one classifier uncertain because non-English localization tables
were not supplied.

The separate frozen source stage supplies all eight complete locale tables.
Only duplicate numbered windows already represented by exact complete files
are removed, with every replaced line verified against Git. Source context179689
stays within180000, no budget increase or information filtering. Original
matching/reviews and uncertain grading remain. Additional judge cost30.80370.
An independent source/API sanity check confirms the candidate's missing-key
mechanism is consistent with Apple's published CFBundle string lookup
implementation; no macOS execution or reclassification was performed.

Complete pooled grounded P100%/100%, R20.75%/24.53%;
augmented P100%/100%, R22.22%/27.27%.
Macro grounded R19.91%/23.18%; macro augmented R21.31%/26.30%.
This is a joint point-estimate signal, not a confidence-bound confirmation or
an official leaderboard result.

All three H sessions request the indexed map. Swift additionally requests
four indexed sources and an entity delta; PHP requests three indexed sources.
Six indexed source first responses are complete. One PHP map request is
refused before a corrected successful request; retain the tool error.
The session ledger references74 already delivered lines across H, versus zero
in B. Therefore this is evidence for the **whole Gita-enabled workflow package**,
not isolated attribution to persistent history or an unmodified public MCP
installation. All adaptive prior failures remain visible.

## E35: unchanged-candidate prospective transfer

Freeze E34's product, no-preload tool-led workflow, proof checks, page limits
and reviewed-PR preparation profile. Sample once with seed20261022 from the
13 positions not previously used in focused pilots:
`[1,2,5,9,10,11,12,13,14,18,19,23,24]`.
The ordered result is **19,11,18**: ForgeMap, jadx-collaboration and gimmes.
Do not inspect labels or outcomes when selecting. Retain endpoint-only
preparation rather than selecting only connected-history tasks.

Same Sol/medium reviewers/judges, native60/reserve90,600/400 phase ceilings
and full raw history in both arms. Prepare and independently verify the new
contexts before inference; first pair checks transport only and all selected
cases enter the cohort. No candidate edits or unchanged retries mid-cohort.
Require a complete credible transfer result before committing to25; a
favorable reused-task result alone is insufficient.

### E35 completed: quality transfers, lower cost does not

[All three prospective outcomes](adoption-e35-transfer3-results.json):
B/H review credits64.03060/76.21306 (**H +19.03%**),
input230,010/332,994 (**H +44.77%**), output8,019/11,655,
model requests14/22. Judge93.50670 separately. Six valid reviews, all pairs
scored without source supplements or retries.
Pooled augmented P85.71%/85.71%, R19.35%/27.27%;
grounded P83.33%/75.00%, R16.67%/20.00%.
The quality direction transfers, but this is **not** a lower-cost transfer.

[The descriptive six-case same-candidate aggregate](
adoption-e34-e35-sixcase-summary.json) verifies identical harness/product
fingerprints and review settings before pooling: credits188.80590/174.87588
(**H -7.38%**), input1,044,918/866,321 (**H -17.09%**);
pooled augmented P94.12%/94.74%, R21.18%/27.27% and grounded
R19.28%/22.89%. Do not use this post-transfer descriptive aggregate to erase
the fresh-stage cost regression or call it held-out confirmation.

## E36: fix fragmented same-file evidence retrieval

Trace audit shows14 source/context calls in the Kotlin case, **13 of them in
Plugin.kt**, versus a baseline that can read related file context together.
The existing indexed `<module>` source already supports whole-file retrieval.
Expose an ergonomic source(path) form in the experimental unified facade,
requiring an exact member file and preserving generation/commit/source checks.
Directory hints do not silently become whole-file reads. Public MCP instructions
document the existing `file::<module>` locator rather than inventing a shipped
path parameter.

Add an explicit, default-off, frozen file-context guidance flag for **both**
arms: prefer an appropriately bounded file read when several related functions
are needed, and batch known independent requests. H can use indexed source(path),
B read_file. Single-function source remains available; no unrelated source is
automatically injected or mandatory extra pass added.

[No-model replay](adoption-e36-file-source-replay.json) retrieves all650 original
Plugin.kt lines in three bounded calls including continuations, versus13
fragmented same-file calls. This proves a retrieval mechanism, not a cost/quality
win. Empty indexed file roots explicitly return zero lines and no fake receipt.
Line-display tests compare original line contents separately from CRLF byte
length, while the provider still validates original bytes before formatting.

Freeze the same complete E35 three-case set19/11/18, seed20261022, with the
new granularity capability/guidance and otherwise unchanged product/context/
model/budgets. These are reused development cases, not another fresh transfer.
No25 authorization until the complete quality/cost result supports it; retain
all previous costs, source failures and negative outcomes.

E36 validation:107 source/protocol/rendering regressions pass, including exact
file roots, multi-page reconstruction, zero-byte files, directory refusal and
attested per-arm granularity guidance. The feature uses the existing index;
no parse-policy relabeling, grammar change or new implicit build is involved.

### E36 rejected; preserve the tool-led default

[All E36 outcomes](adoption-e36-filecontext3-results.json):
B/H credits59.44324/53.92186 (**H -9.29%**),
input171,197/211,270 (**H +23.41%**), output10,274/7,835.
Judge69.21700. All six reviews valid, all three pairs scored.
Augmented P85.71%/80.00%, R18.75%/12.50%.
Grounded R13.33%/6.67%. Reject this guidance; cheaper but less complete is
not the goal. Do not choose favorable E36 per-task rows.

Keep the whole-file convenience behind its explicit profile. The default
tool schema, instruction prompt and actual evidence for the E34/E35 tool-led
candidate must remain equivalent, rather than silently promoting the rejected
variant. The whole-file empty-source handling remains a correctness fix.

## E37: fixed full25 development study, not a guaranteed efficiency claim

The unchanged candidate improved recall in both E34 and fresh E35, but fresh
cost rose. Across all six same-candidate cases, the descriptive joint point
estimate is lower-cost/higher-recall. That is enough to justify a bounded
larger **development measurement**, not an efficiency confirmation or a claim
that the fresh-stage cost gate passed. Stop tuning those six outcomes.

Freeze every starter task exactly once for this candidate: retain all six
E34/E35 primary pairs, including higher-cost transfer rows, then execute all
19 remaining cases in fixed population order. Do not retry unchanged reviews,
select cheaper per-case variants or relabel original protocol identities.
The six retained pairs are from adaptive development, so the resulting25-task
study is not held-out or leaderboard confirmation.

Before reuse, source-only attestation checks12 original native receipts,
exact current-vs-original instruction bytes, exact typed tool schemas and
every replayed evidence-result digest. All12 reviews pass, with zero new
model invocations. Dry-run instruction generation is not a native attempt.
Original raw artifacts, source protocols and the localization-complete judging
stage remain separately pinned.

All19 new cases use the same tool-led, no-preload, no-file-guidance profile,
Sol/medium reviewers/judges, native60/reserve90, identical source budgets and
explicit PR-window preparation. Seal one fixed pair per native protocol so an
ordinary case failure does not hide later independent tasks. Every case and
missing/invalid slot remains in the25-task accounting; integrity or unknown
spend stops the whole study. No automatic retries.

Primary study ceilings, including retained pairs:2500 review credits and1500
judge credits. Each new pair is capped at200 review/200 judge credits and is
started only with enough remaining global budget. Source-only preparation and
retained-pair attestation cost no credits. R&D spend from rejected variants is
retained separately, not erased or mixed into comparative per-arm review cost.
Report macro/pooled quality, all model/native partitions, true indexed use,
raw fallback, preparation time, missingness and repository-clustered uncertainty.
If the full25 does not establish the target, report that result honestly.

### E37 preparation interruption and user-requested six-pair flow audit

Before any new E37 reviewers started, bounded preparation completed12 of19
continuation cases, including one explicitly recorded timeout/raw fallback.
The next case, Rust nebula, failed independent name/id conformance. Published
definitions were present, but fully qualified locators such as
`context.rs::fmt::Debug for CredentialContext::fmt` did not resolve when a
captured scope itself contained `::` and spaces. The gate stopped the study;
the failing case was not silently downgraded.

The query fix compares escaped canonical name payloads for compound literal
scopes, retaining parameterization, exact path restrictions and ambiguity.
Five authored regressions failed before the fix. The focused name lookup,
Rust/delta and review-evidence suite then passed103 tests. Original published
generations and the failed preparation attempt remain intact. Full source
requalification and a new-policy retained-review attestation are still
required before sealing/running the remaining19 pairs.

The user then requested a pause to understand the six completed pairs rather
than advance the study. An offline interactive HTML explainer was produced in
session artifacts: generalized Git/Gita investigation stages, actual request
examples, indexed-vs-raw operation counts, cost contributions, known/new issue
metrics, and optional per-pair request/final-comment details. It makes no new
reviewer/judge calls, exposes no private model reasoning and does not assign
fictional stage-specific credit prices.

The trace audit found that the earlier source-only attestation had appended
replay calls to the original broker log destinations. The report recovers
each original native byte prefix by its receipt-bound SHA-256: all12 prefixes
match, with278 native calls included and278 later replay calls excluded.
All other native receipt-bound artifacts also hash-match. Neither original
receipts nor logs were rewritten for this report. Replay helper configurations
now exclude the original `tool_log` destination. Strict historical validation
and the full-study collector must account for these appended tails before
study continuation; do not blindly count the current whole log as native use.

Measured native activity is120 Git calls versus158 Gita calls. Gita requests:
six successful change maps (eight requested),18 source requests, seven context
requests, one structural diff, and33 raw-patch requests. Raw patches are exact
Git fallback, not indexed evidence. There are zero explicit indexed-history
requests; change maps can still use indexed lineage/diff previews internally.
Seventeen source responses and five context responses are marked complete
entity delivery. Candidate-only responses are not equated with full source.
The H delivery layer reports77 reused source lines, including reuse from raw
reads; this is not a persistent-ID-only effect.

The PHP pair saves15.58026 credits, more than the six-pair net13.93002-credit
saving. It uses more tool calls (36 to42) but fewer model requests (11 to8).
The Kotlin pair makes14 named source/context requests,13 into one file,
costs4.21482 more credits and has unchanged known coverage/new-TP count.
ForgeMap returns less tool text but doubles model requests (five to10) and
costs3.41744 more credits. Extra known-issue coverage comes from Go (six to
seven), Swift (three to four) and Python (zero to one), not every task.
These observations support workflow/granularity hypotheses, not isolated
causal attribution to any one Gita feature.

The report's totals match the existing six-case result, and browser checks
cover all three cohort filters, five flow stages, all278 request rows,
theme switching, and390px/1280px layouts. No new native reviews or judges
were started. Full25 execution remains paused at the user's request.

### E37 continuation resumed with explicit query-fix and native-version freeze

On the user's subsequent instruction, resumed without introducing a new
reviewer workflow. The repaired query replays all278 original native evidence
results identically across12 retained reviews; prompts and typed discovery
schemas also match exactly. A detached historical product snapshot, with
the original exact newline bytes, validates all12 original receipts against
separate native-artifact views. Views contain the exact receipt-hashed log
prefix, not edited original logs. Every original replay tail is preserved
and its whole-file hash is bound in the historical validation report.

Requalified all19 continuation cases:18 have published, independently checked
indexes and one retains the original timeout/raw fallback. The Rust case
reuses its already-published immutable endpoint generations after the query
repair; no costly rebuild or silent relabeling. The first requalification
helper stopped on a circular JSON reference caused by retaining a live
`index_status` dictionary in a nested validation record. A separate v2
preparation root uses a deep snapshot; the failed root remains, no model
credits consumed. Existing grammar fingerprints and generations are unchanged.

Before any new reviewers, froze `adoption-e37-queryfix-annex.json`: new product
fingerprint, exact retained-evidence attestation, historical receipt validation,
all19 qualified scopes and parser manifest. The six original protocols and
primary outcomes remain unchanged. This is explicitly **not** byte-identical
product source across all25; it is a pre-model query correctness repair with
verified equivalence of every observed retained evidence response. It remains
a development measurement, not held-out confirmation.

Sealing caught shared CLI drift from1.0.97 to1.0.100. Did not relax the gate or
change the shared installation. Downloaded the official1.0.97 Windows archive,
verified its published SHA-256, and selected an isolated frozen executable
with self-update disabled. An initial version probe auto-updated its isolated
copy; it was retained and not used for the study. A second isolated copy
reports1.0.97 with stable executable SHA-256
`682eb8de6a433ad5a1f5b291183c90e4b05239d58f5f0345acc89279da4f0735`.
All19 per-case protocols then sealed successfully. New native reviews and
judgments are now running in fixed population order with Sol/medium.

Collector verification covers percentile interpolation, positive gates,
uncertainty crossing the recall margin, incomplete fixed populations, and
refusal to claim lower cost when credits rise (five checks pass). The final
collector will verify receipt bindings, exact native views, judge provenance,
delivery bounds, complete-population clustered uncertainty and both quality/
efficiency routes. Supplemental judge costs remain separately visible.

### E37 complete: robust cost direction, unresolved quality-preservation gates

Results: [full25 machine-readable report](adoption-e37-full25-results.json),
[completion binding](adoption-e37-completion.json). All50 primary reviews are
valid and all25 pairs are scored. No missing judgments, automatic retries,
new supplementary judge calls or unresolved spend. All19 new pairs ran once
in the fixed original population order; all six original pairs remain.

| Full25 metric | Git B | Gita H |
|---|---:|---:|
| Reviewer credits | 950.09944 | 858.65378 |
| Input tokens | 6,297,473 | 5,241,728 |
| Model requests | 225 | 229 |
| Mean per-PR grounded recall (primary) | 20.93% | 22.38% |
| Pooled grounded recall | 20.45% | 21.59% |
| Pooled grounded precision | 89.23% | 92.31% |
| Pooled augmented recall | 23.50% | 25.41% |
| Pooled augmented precision | 91.14% | 91.76% |
| Known TP reference issues covered | 72/352 | 76/352 |
| Additional accepted TP findings | 14 | 18 |
| Sum of warm review seconds | 2,272.376 | 2,297.528 |

Credits fall9.62485% and input16.76458%. The paired10,000-draw whole-repository
bootstrap (25 repository clusters, fixed seed20261023) gives95% intervals:
credit reduction[1.99%,16.11%], input reduction[0.84%,29.76%], macro grounded
recall difference[-4.41pp,+6.69pp], pooled augmented precision
difference[-4.00pp,+4.82pp]. Cost/input intervals exclude zero in this
development population. Quality intervals do not establish the frozen
1.62511pp recall/3pp precision noninferiority margins. Warm elapsed rises1.11%.
Operational, useful-baseline, FP/PR, input-reduction and actual-credit gates
pass; the overall quality-preserving efficiency/quality-improvement gate is
**not established**, not a pass. F1 fields are explicitly harmonic means of
the named aggregated precision/recall, not relabeled official macro-F1.

The19 new pairs are reported separately from the adaptive six:
B761.29354 versus H683.77790 review credits (10.18% lower), B5,252,555 versus
H4,375,407 input tokens (16.70% lower), macro grounded recall22.41% to22.93%,
pooled augmented precision90.32% to90.91%, known coverage56 to57 of269.
Unlike E35 alone, the larger continuation does transfer cost savings, but
the primary quality point-estimate gain is small and not a confidence result.

Meaningful adoption is no longer near zero:23 successful change-map requests,
93 source requests,14 indexed-context requests, one explicit structural diff,
three explicit indexed-history requests,96 complete indexed-source first
responses and382 reused source lines. Keep93 raw-patch calls and four raw
context fallbacks separate from indexed use. Source errors12, map errors9
and one context error remain visible. Complete-source counts exclude
candidate-only replies; reuse may include raw reads, not just persistent IDs.

Total primary comparative reviewer cost is1,808.75322 credits; judge cost
946.63820 including retained source-complete stages. No new source supplements
were needed for the19 continuation pairs. These judge and preparation costs
are not smuggled into or excluded from a claimed end-to-end cost ratio: the
headline comparison is explicitly reviewer credits. All preparation stages,
timeout/raw fallback, errors and known timings stay in the report. An exact
source snapshot and manifest preserve completed-study verification before
any next experiment changes the harness.

Post-run refusal audit also identifies a preparation defect in the recovered
Rust scope: the private recovery helper omitted `build_status` from both
already-published endpoint descriptors. Independent source/name checks passed,
but the first `changes` request returned a missing-key error. This was an
observed operational tool refusal, not falsified source. Keep the original
valid review and score unchanged; do not rerun/relabel that pair to remove
the defect. The following E38 preparation guard addresses the blind spot.
