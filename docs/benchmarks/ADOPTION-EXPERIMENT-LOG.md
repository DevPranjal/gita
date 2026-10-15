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
