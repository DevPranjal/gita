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
