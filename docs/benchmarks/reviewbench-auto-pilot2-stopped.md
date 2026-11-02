# Indexed ReviewBench auto pilot: second stopped cohort

**Status: incomplete development experiment, no quality or efficacy claim.**
Protocol: `ddc9607478cb72f1f17a8d2a4a8f00f8776903c6fe287a3f94206d37e70711e9`.
Source revision: `97afb8fae05d2c38d655d6c8a44c7e081474a3ef`.
Public corpus/scorer pin: `c17b05be9c90b85a1fc012ce39e455e2d09a7255`.
See the [machine-readable projection](reviewbench-auto-pilot2-stopped.json)
and [harness contract and limitations](reviewbench-indexed.md).

## Delivery and stopping cause

The fixed official development starter has 25 tasks and 50 planned sessions:

| Arm | Valid | Failed | Unstarted |
| --- | ---: | ---: | ---: |
| B: common Git evidence | 21 | 1 | 3 |
| H: same evidence plus optional indexed tools | 22 | 0 | 3 |

There are **21 valid complete pairs**, one unpaired valid H review, and one
measurement-invalid B review. The invalid session emitted a malformed physical
JSON frame in `assistant.reasoning_delta`. The strict reader stopped the process;
no terminal native usage file was exported. This was not a call-limit failure:
that session made 11 completed evidence calls.

The unknown session retains its 30-credit reservation. No attempts were retried,
receipts rewritten, usage estimated as zero, or stopped ledger resumed.
The first stopped cohort remains [separate](reviewbench-auto-pilot1-stopped.json).

## Conditional observations on 21 valid pairs

| Measure | B | H |
| --- | ---: | ---: |
| Reported native credits | 102.677625 | 86.180626 |
| Input tokens including read/write cache | 1,742,247 | 1,193,688 |
| Output tokens | 40,570 | 30,774 |
| Evidence-tool attempts | 167 | 115 |
| Findings, ungraded | 42 | 39 |
| Sessions requesting indexed tools | 0 | 2 |
| Realized Claude Sonnet 5.5 sessions | 18 | 19 |
| Realized GPT-6 Luna sessions | 3 | 2 |

The conditional differences are 16.1% fewer reported credits and 31.5% fewer
input tokens in H. They are **not full-cohort savings or a causal indexing
effect**: completion is incomplete, routing/cache behavior varies, adoption is
low, and review quality has not been measured. Finding counts are not correctness.
The observed credit direction changed during the run; intermediate prefixes
must not be cherry-picked.

## Costs and missingness

All 43 measured sessions, including the unpaired H session, consumed
**200.231092 known credits**. The reviewer-cost interval including the unknown
reservation is **[200.231092, 230.231092]**. No judges have run.

Across earlier development, the first stopped protocol and this protocol,
the reviewer/development interval is **[338.098368, 458.098368]**. This is not
the cost of the 21 paired reviews and does not include engineering-chat usage.
The projection retains the earlier unknown reservations and separately explains
the harness's older plumbing-overhead constant.

Neither full-cohort mean tokens nor full-cohort credit savings are available.
The fixed denominators remain 25 tasks and 50 sessions. Missingness is not
converted to successful zero-finding reviews.

## Index and quality boundaries

Independent preflight verified 9,009 occurrences. Twenty-one tasks have separate
base/head publications; four index failures remain in the cohort, with raw
fallback. There are 167 extraction gaps. Both arms share the same declared raw
history. This does not evaluate persistent indexed historical continuity,
amortization or complete language/repository coverage.

Quality grading is paused behind the unchanged unresolved-reviewer-spend gate.
There is no official leaderboard score, demonstrated non-inferiority,
human-audited novel defect precision, or AACR replication for this indexed product.
A separate partial-cohort quality analysis would need an explicit new protocol
that binds the existing receipts, retains all cost/missingness bounds, and does
not represent the stopped campaign as successfully resumed.
