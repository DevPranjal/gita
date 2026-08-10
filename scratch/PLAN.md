# gita — working plan

> **git rebuilt for a world of agent coders**

Everything below must serve that. No feature exists to move a metric; each one
exists because an agent cannot do its job without it.

## Where we are (start of autonomous session)

Iteration 3, 60 runs, real credits:

| metric | git | gita |
| --- | ---: | ---: |
| credits / task | 42.11 | **38.60** (-8.3%) |
| turns | 3.87 | **3.30** |
| tool output | 45,997 | **3,421** (-93%) |
| wall clock | 160.9s | **72.2s** (-55%) |
| recall | 97% | **98%** |

Branch `feat/one-shot-context`. 283 tests. Not merged: three tasks regress.

## Cost model (learned the hard way, twice)

Credits per 1M tokens, Claude Opus 5: input **500**, output **2500**,
cache read **50**, cache write **625**.

- Cache reads are **54% of the baseline bill**. Volume beats unit price.
- Output is **5x** input. Making the model *write less* is worth more than
  making it *read less*.
- Never quote a token count as cost. Three readings of one run gave -15%,
  +3.8% and -8.3%. Only `eval/pricing.py` is authoritative.

## Priorities

### P0 — the three regressions (blocking merge)

| task | credits | symptom |
| --- | ---: | --- |
| flask-uncommitted | +25% | 5.3 turns vs 4.0; working-tree flow still wrong |
| gin-history | +25% | 4.3 turns vs 3.3; `gita history` walks commits serially |
| ripgrep-walker | +18% | recall **78%** — the only quality regression |

### P1 — maturity, in the sense that a platform team would demand

- Errors that tell an agent what to do next, never a bare traceback.
- Performance: `gita history` is O(commits) with a full parse each.
- Determinism: identical input must give byte-identical output, or prompt
  caching breaks for every downstream turn.
- Honest limits stated in `--help` and SKILL.md, not buried.

### P2 — only if evidence demands it

Blast radius (WS-2) is the largest missing capability, but it is only worth
building if the evaluation shows agents failing for want of it.

## Loop

1. Diagnose with data from `evals/runs/*/` — never from intuition.
2. Write the fix with a test that fails first.
3. Re-run the affected task in isolation before a full sweep.
4. Full 60-run sweep, record in `evals/RESULTS.md`, commit.
5. Update this file and `scratch/JOURNAL.md`.

## Rules for myself

- A change without a measured reason is not a change worth making.
- If a fix does not move the number, revert it and say so.
- Record failures in the journal as prominently as successes.
