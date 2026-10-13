# Paired AACR review evaluation, v1

This historical protocol was frozen before benchmark model calls. It evaluates code-review
issue detection, **not general coding task completion**. A good result is not
assumed. The study used product revision `8b812d4`, not the current release.
Recorded outcomes are summarized separately in [RESULTS.md](RESULTS.md).

## Dataset and selection

- Public [AACR-Bench](https://github.com/alibaba/aacr-bench), Apache-2.0,
  commit `68a569759289a83654a59d06db2a72910edf0a4a`.
- Use every PR in `dataset/positive_samples.json` (actual file: **196**, despite
  the README's rounded 200). The actual language labels include C, not Ruby.
- `negative_samples.json` contains **rejected comments on overlapping PRs**,
  not clean/no-bug PRs. Never treat those records as negative review tasks.
- Frozen manifest records both exact revisions, source hash, reference digest,
  language and declared diff size. Target is the pinned review head.
  **Pre-confirmation amendment:** the source can be a divergent later base tip.
  Resolve and persist the merge base of the two pinned commits, then compare
  merge-base -> target (GitHub PR three-dot semantics). Preserve both supplied
  SHAs and the effective SHA. Never substitute today's PR head.
- Hash order with seed `gita-aacr-20260919-v1`, one pilot PR per language.
  Remaining PRs are confirmation. Pilot outcomes are never merged into the
  confirmation headline. Selection does not depend on gita compression,
  expected wins, issue categories or model outcomes.
- No size/language exclusion. Preparation failures remain visible, including
  denominators and task identities. An initial bounded confirmation prefix is
  round-robin across all ten languages, not selected for favorable outcomes.

## Controlled two-arm experiment

Both arms: fresh Copilot CLI home/session; `gpt-5.6-sol`, medium reasoning,
default context; identical prompt; full repository source at the two fixed
commits; read-only raw unified diff, source reading, literal search and file
listing. Gita arm additionally receives structural diff/show/expand tools
delegating directly to the product implementation. Adoption is voluntary.

This is an isolated **tool-augmented agent** experiment, not unrestricted
Copilot or the official AACR leaderboard setup. Shell, web, custom instructions,
user MCPs, memory and subagents are unavailable. This prevents reading reference
labels, later commits, the other arm, or personal workspace data. References
never enter the evidence server or reviewer prompt. No test/build execution is
offered to either arm. Tool names/schema overhead count in model input usage.
The common surface does not expose every Git option (for example a standalone
diff-stat command); results are conditional on this documented interface.

All textual evidence tools paginate at 6,000 cl100k tokens, with explicit
continuation. Gita's product-native 6,000-token budget and truncation notices
are preserved; raw diff fallback remains available. At most 40 evidence calls
and 900 seconds per arm. Pair order is seeded, not always baseline-first.
Pairs run serially within each task; independent task concurrency is recorded.
No cache-miss filtering, answer retry, best-of-N selection or success-only
cost headline.

## Measurement and failure policy

- Primary efficiency: total model input tokens, including all repeated and
  cached context; also total input + output, output, API requests and wall time.
- Authoritative source: CLI `--usage-output-file` **once**, verified against
  its decomposed token details. Do not regex-sum repeated debug-log objects.
  Missing/malformed/model-mismatched telemetry is invalid, never zero.
- Input tokens = uncached input + cache reads + cache writes. Report each.
  Report CLI credit units separately from estimates using user-supplied rates:
  400/40/500/2000 per million uncached/read/write/output tokens.
- Save raw JSONL CLI events, exact invocation, final answer, usage, per-call
  arguments/results and latencies. Independently reconcile CLI tool calls with
  evidence-server logs and inspect exposed tool schemas.
- Invalid/incomplete tasks remain in the coverage report; measured paired-cost
  estimates use only pairs with both measured arms, explicitly labelled.
  Model/tool failures are not favorable token savings.
- Analysis clarification: measurement validity, response-format compliance and
  usable review content are separate. A missing unscored `category` is a visible
  formatting failure, not lost cost or an invented zero-issue review. Offline
  reconciliation preserves original answers/results and grades usable path,
  side, lines, title and body without guessing categories. Truly unusable content
  receives zero issue recall while measured cost stays included.
- Count gita adoption, raw fallback, tool errors and evidence tokens separately.
  Evidence tokens are a tokenizer-based diagnostic, **not billed model tokens**.

## Quality and decision rules

Reviewer returns structured actionable findings with path, side, source line
range, issue category and explanation. Primary quality is reference issue
recall (and Code Defect subset recall); secondary metrics include one-to-one
reference agreement precision/F1, all-reference recall, and PR-level detection.
Reference agreement precision is not verified real-world precision: a valid new
finding can be absent from the reference set.

Grade blinded to arm, cost, timing and tool traces with a separately isolated
GPT-5.6 Sol judge, comparing technical causes and consequences rather than
keyword mentions. Match on exact file and side with a declared line tolerance
(five lines); report semantic same-file recall separately to expose localization
sensitivity. Enforce one-to-one matching in code, not trusting the judge to count.
Judge uncertainty and invalid responses remain explicit. Audit a deterministic
subset of matched/unmatched findings against source and run duplicate/swapped
judge checks before interpreting small quality differences.

Confirmation estimates resample **repositories** (cluster bootstrap), not
individual repetitions as independent tasks. Report 95% intervals and absolute
quality differences. Non-inferiority margin is 5 percentage points in
macro per-PR recall, declared before outcomes. Only claim non-inferiority if
the lower 95% interval bound exceeds -0.05. Failure to detect a difference is
not proof of equivalence. Cost improvement requires a positive lower interval
bound on paired aggregate input-token reduction.

## Scale gates

1. CLI smoke: requested model, nonzero consistent usage, isolated configuration.
2. Harness tests: bounds, access isolation, no silent missing metrics, deterministic
   split, actual gita calls, structured output.
3. Pilot: actual exposed-tool audit, correct revision direction, real evidence,
   valid findings and valid blinded grading. A weak product outcome is **not**
   grounds to replace tasks or tune the model prompt.
4. Freeze any instrumentation fixes with an explicit version note; preserve
   invalid attempts. Scale only after the harness works.

### Fixed independent final holdout (declared after the 40-case gate)

The holdout plan required **all remaining 146 frozen non-pilot tasks**, with no optional
stopping based on interim outcomes. They were never used for tuning or inspection
of model results. Use this disjoint holdout as the final inferential cohort.
Keep the pilot, 40-case gate and 146-case holdout separate in the main report;
a pooled all-case view is descriptive, not an independent confirmation.

Six task workers are used for the holdout (versus four at the gate), after 80
complete gate reviews showed no process, telemetry or tool-isolation failures.
Model, reasoning, context, prompt, evidence budgets, product and seeded pair
order remain unchanged. Do not pool cross-cohort wall-clock latency as if host
concurrency were identical.

## Limits

The benchmark is public and may be in model training data. Some annotations are
subjective or wrong; annotation recall is not semantic ground truth. Same-family
reviewer/judge bias remains possible. The benchmark cannot validate persistent
history reuse, issue fixing, tests passing, or all developer workflows. Host
latency/cache state can vary; order randomization and separate time/cache metrics
make that visible but do not remove it.
