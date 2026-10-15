# Indexed Gita versus Git: ReviewBench pilot results

**Status:** All 21 completed primary pairs are quality-scored. The originally
planned 25-task reviewer campaign remains incomplete. These are local research
reference-agreement scores, not official leaderboard results.

The [machine-readable report](reviewbench-auto-indexed-quality.json) includes
per-PR metrics, provenance, all costs and exploratory uncertainty.
The [stopped reviewer cohort](reviewbench-auto-pilot2-stopped.md) retains its
failed/unstarted slots. No reviewers were rerun to obtain quality scores.

## Population and treatment

- Pinned public ReviewBench: `c17b05be9c90b85a1fc012ce39e455e2d09a7255`.
- Official development starter: 25 planned tasks, not held-out confirmation.
- 21 valid paired tasks / 21 repositories; one valid unpaired H review,
  one failed B review and six unstarted review sessions.
- B: Copilot CLI `auto` plus scoped common Git evidence.
- H: the same objective/common evidence plus optional indexed tools.
- Separate verified endpoint indexes, not connected indexed historical continuity.
- All 21 paired outputs were scored against the same 298 golden TP findings.

## Consumption on the same 21 pairs

| Measure | B: Git | H: Git + Gita | Observed H reduction |
| --- | ---: | ---: | ---: |
| Reported credits | 102.677625 | 86.180626 | 16.1% |
| Input tokens including cache | 1,742,247 | 1,193,688 | 31.5% |
| Uncached input | 9,906 | 8,552 | 13.7% |
| Cache read | 1,373,798 | 920,998 | 33.0% |
| Cache write | 358,543 | 264,138 | 26.3% |
| Output tokens | 40,570 | 30,774 | 24.1% |
| Model requests | 145 | 110 | 24.1% |
| Evidence-tool attempts | 167 | 115 | 31.1% |
| Mean review wall seconds | 37.20 | 27.86 | 25.1% |
| Median review wall seconds | 30.88 | 25.97 | 15.9% |
| Findings produced | 42 | 39 | Not an efficiency/quality success criterion |

Latency excludes indexing/preparation. Input includes cache partitions; it is not
a count of exclusively uncached/billed input. Native aggregate credits are used
once, never summed with nested model totals.

Auto realized 18 Claude Sonnet 5.5 / 3 GPT-6 Luna sessions in B and 19 / 2 in H.
Routing/cache behavior, different exploration and single-repeat variation limit
causal interpretation.

## Quality: mixed, not a uniform improvement

| Macro metric | B | H | H minus B, percentage points |
| --- | ---: | ---: | ---: |
| Grounded precision | 76.47% | 78.70% | +2.23 |
| Grounded recall | 12.20% | 14.57% | +2.37 |
| Augmented precision | 80.53% | 76.67% | -3.86 |
| Augmented recall | 14.61% | 16.45% | +1.84 |

| Micro metric | B | H |
| --- | ---: | ---: |
| Grounded precision | 75.76% | 80.65% |
| Grounded recall | 9.40% | 10.74% |
| Augmented precision | 80.95% | 79.49% |
| Augmented recall | 12.05% | 12.50% |

The pinned official scorer uses its null-denominator conventions; macro
precision averages only defined per-PR precision values. Grounded and augmented
metrics use different populations/denominators. They must not be conflated with
verified defect precision or with raw finding count.

B matched 25 candidates to golden TP and 8 to golden FP; H matched 25 and 6.
The semantic classifier labeled B's 9 unmatched candidates TP; H's 8 unmatched
candidates became 6 TP / 2 FP. These novel classifications need human audit.
Coverage can exceed one reference per candidate under the official resolver:
B covered 28 golden TP findings and H covered 32.

The direction is therefore mixed: slightly stronger grounded agreement and
recall in H, but lower augmented precision. No formal superiority or quality
non-inferiority claim is made.

## Uncertainty and missingness

Exploratory paired repository-cluster percentile bootstrap: 20,000 resamples,
seed `20261015`, conditional on the 21 completed pairs:

| Quantity | 95% interval |
| --- | --- |
| Credit reduction in H | -18.12% to +37.56% |
| Input-token reduction in H | +14.46% to +43.65% |
| Grounded recall difference | -1.45 to +6.22 percentage points |
| Augmented recall difference | -2.04 to +5.74 percentage points |
| Grounded precision difference | -9.65 to +15.00 percentage points |
| Augmented precision difference | -14.29 to +7.19 percentage points |

These are not a preregistered confirmation/non-inferiority test. In particular,
the credit interval includes increased consumption. Neither quality superiority
nor a population-wide credit saving is established.

Keeping all 25 TP-bearing task slots gives worst/best delivered grounded-recall
bounds of 10.25%-22.25% for B and 12.24%-28.24% for H. Failed delivery contributes
zero delivered recall; valid unpaired and unstarted outcomes remain unknown.
These bounds are not confidence intervals or successful empty reviews.

## Actual indexed-tool adoption

Only 2/21 H sessions requested indexed tools, making four attempts:

- `index_status`: two successful calls.
- `entities`: one successful call.
- `events`: one refusal because no common historical generation was available.
- `id`, `state`, `source`, `history`: no calls.

Only one session retrieved entity evidence. The observed arm differences are
not evidence that persistent identity/history caused savings or improved review.
This pilot tests optional tool availability poorly as a mechanism study; it does
not validate an integrated index-first review workflow.

## Judging method and preservation

Custom blinded Copilot `auto` matching/classification uses the pinned official
resolver/scorer arithmetic, not the upstream hosted provider/session prompts.
Both arms' candidates share each blinded task context and judging process.

Ten original full-context judgments were retained unchanged. Oversized contexts
were replaced, only for previously unscored tasks, by merged exact HEAD windows
around both arms' candidates and the references. One classifier required
additional source-verified repository-wide literal searches and caller/source
files; its already completed matching decisions were preserved. All 20 other
completed decisions remained unchanged.

Native transport failures and unknown reservations remain recorded. Tool-free
judges explicitly record malformed redacted input-echo/diagnostic frames without
using or repairing their contents; final answers, controls and native usage are
validated strictly. Three such warnings were recorded. No missing/uncertain
judgment was converted to default FP or successful zero findings.

The full official local judging command also requires a selected API provider
and model; this report deliberately stays with the authorized Copilot-credit
route. It is not an official ReviewBench or leaderboard-equivalent evaluation.

## Complete native spending

| Accounting population | Known credits | Conservative upper bound |
| --- | ---: | ---: |
| Second reviewer campaign, all started slots | 200.231092 | 230.231092 |
| All quality analysis, qualification and adjudication | 71.376424 | 131.376424 |
| All native evaluation/development protocols together | 409.474791 | 589.474791 |

The all-protocol figure includes earlier stopped campaigns/canaries, not just
the 21 paired reviews. Upper bounds retain native reservations with missing
usage; they are not measured expenditure. Engineering-chat costs are separate
and not available from this accounting.

## Engineering conclusion

Keep the measurements, not a promotional savings claim. Quality is now
measured for the completed cohort and is mixed. The principal next experiment
must provide connected indexed history and a meaningful index-first evidence
workflow, preserve raw fallback and fixed eligibility, verify adoption before
paid scale-up, and use one consistent judging protocol. Human-audit uncertain
and novel decisions before making external efficacy claims.
