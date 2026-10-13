# Historical evaluation summary

**Study completed:** 2026-09-19. **Product revision:** `8b812d4`.
**Model:** GPT-5.6 Sol, medium reasoning, default context.

This is a summary of an older experiment, not validation of the current 1.0.0
release. Generated reports, trace bundles and investigation logs have been
removed from the release tree. The summary alone cannot reproduce the recorded
measurements. The [harness guide](README.md), [protocol](PROTOCOL.md) and
[input manifest](aacr-manifest.json) support new experiments.

## Scope and methodology

The frozen AACR-Bench snapshot scheduled 196 PRs from 50 repositories across
ten language labels. Both arms used the same review prompt, isolated sessions
and common read-only evidence tools. Treatment added gita diff/show/expand;
neither arm could execute benchmark tests. Each review had a 40-evidence-call
budget. Quality was measured by blinded reference-issue agreement, not by
executable task completion.

| Cohort | Scheduled | Valid pairs | Disposition |
| --- | ---: | ---: | --- |
| Pilot | 10 | 9 | One unavailable pinned comparison |
| Balanced gate | 40 | 40 | Four PRs per language, 30 repositories |
| Independent holdout | 146 | 135 | Ten unavailable pins; one baseline MCP initialization failure |
| Total | 196 | 184 | 370 reviewer attempts with native usage records |

The holdout alone supplies the final inferential estimates. Pilot and gate
results are not pooled into that claim. Missing or failed attempts were not
treated as zero-cost successes. All-attempt reviewer usage was 57,371,340 input
tokens and 11,297.32 reported credits, excluding judging and historical or
aborted calibration work. Credits are CLI-reported units, not a reconciled bill.

## Independent holdout: 135 pairs, 46 repositories

Intervals use 10,000 repository-cluster bootstrap resamples.

| Metric | Baseline | Gita | Effect, 95% interval |
| --- | ---: | ---: | --- |
| Mean input tokens | 163,235 | 150,922 | 7.5% reduction [-1.8%, 14.9%] |
| Mean input + output tokens | 166,483 | 154,177 | 7.4% reduction [-1.8%, 14.6%] |
| Mean reported credits | 35.94 | 27.64 | 23.1% reduction [18.0%, 27.1%] |
| Macro per-PR reference recall | 10.00% | 9.69% | -0.31 pp [-1.64, +1.15 pp] |
| Same-file semantic recall | 10.71% | 11.12% | +0.42 pp [-0.88, +1.89 pp] |
| PRs with at least one reference match | 64/135 (47.4%) | 57/135 (42.2%) | -5.19 pp [-11.57, +0.79 pp] |
| Localized reference matches | 77/1,058 | 69/1,058 | Fewer matched annotations with gita |
| Reference-agreement precision | 30.2% | 28.4% | Not verified real-world precision |
| Gita invocation | Not installed | 135/135 | Invocation is not proof of utility |

Serialized tool-output text was 18.7% smaller in diagnostic tokens. Input tokens
per matched reference were 286,191 baseline versus 295,281 gita: 3.2% higher
with gita. Credits per matched reference were 63.01 versus 54.07.

## Limits and negative results

- Reported-credit savings were established only in this measured environment.
  Input-token reduction and unchanged PR-level success were not established.
  Macro recall met a predeclared five-percentage-point non-inferiority margin;
  that does not establish equality or preserve every quality metric.
- Cache behavior changed across an overnight host suspension. Excluding six
  interrupted whole pairs gave 24.7% credit reduction [19.3%, 28.9%]; the
  before-resume cohort gave 3.6% [-4.4%, 11.4%], and the after-resume cohort
  gave 32.7% [27.5%, 37.3%]. These cohorts contain different PRs, so the
  difference is diagnostic, not a causal cache experiment.
- No latency improvement was established: uninterrupted, timing-complete
  pairs showed 0.9% wall-time reduction [-4.0%, 5.5%].
- 115/135 treatment reviewers also called raw diff; 90/135 received a truncated
  first gita answer. The evidence-call ceiling was reached in 65 baseline and
  51 treatment cases. The nine-case pilot cost more with gita.
- Benchmark annotations include questionable allegations and localization
  concerns. Reference agreement is not actual defect precision. Reviewer and
  judge shared a model family; contamination and correlated errors remain
  possible. Consistent repeated judgments are not proof of ground truth.
- The baseline used four restricted read primitives, not unrestricted Git or
  Copilot. This study did not measure history reuse, all nine product tools,
  general coding success or index-build costs.

Do not describe this study as "23% fewer tokens at equal accuracy."

Historical validation recorded 144 targeted tests and portable replay of 184
paired grades, 368 arm usage totals and 12,256 tool-call records without new
model calls. Those are historical observations, not current-release test
results or a claim that the removed evidence bundles are still distributed.
