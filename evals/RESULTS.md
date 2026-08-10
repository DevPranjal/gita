# Evaluation results

## Iteration 1 — 2026-08-10 · **INVALID for the gita arm**

60 runs · 10 tasks · 3 repetitions · git-only vs git+gita · claude-opus-5
Artifacts: `evals/runs/20260810-164600/`

### Reported numbers

| Metric | Result |
| --- | --- |
| Billed (uncached) tokens | gita 16.4% cheaper |
| Raw prompt tokens | gita 27.2% more |
| Tool output | gita 86.2% less |
| Entity recall | 100% both arms |
| Adoption | 90% |

### Why these numbers do not mean what they appear to

The harness invoked gita through a `gita.cmd` wrapper. **`cmd.exe` treats `^` as an
escape character**, so every `gita diff <sha>^ <sha>` arrived as
`gita diff <sha> <sha>` — a commit diffed against itself.

gita answered correctly for the question it was asked: *no material changes*, four
tokens. The agent read that as a useless tool, fell back to `git`, and spent an
extra turn doing so.

Evidence:

- `gita diff` output averaged **16 tokens** (max 114). A real context diff is 300–500.
- `--json` confirms the mangling: `"base": "18e5985", "head": "18e5985"`.
- In the gita arm the agent still made **3.3 git calls per run** — it was not using gita.
- Turns: git 4.13 → gita 5.30. The extra 1.17 turns is the failed attempt plus fallback.

The same defect affected `git.cmd`, so the baseline arm was partly degraded too:
`git diff` calls show a minimum output of 0 tokens, consistent with mangled revisions.

### What remains valid

- **Harness plumbing works**: real prompt/completion tokens, per-run logs, telemetry
  from both arms, entity recall scoring.
- **Baseline context is ~126,000 tokens per turn**, identical in both arms. This is
  environmental overhead, not gita's, and it means turns dominate total cost.
- **The one task where the diff genuinely dominated** (`flask-dependency-update`,
  1.69M tokens of git output) is the only place gita won: −51% prompt tokens,
  −87% tool output, 13.3 → 6.7 turns. Even crippled, gita won that task.
- **Control task behaved as a control**: `express-ci-bump` lost, as predicted.

### Fixes applied before iteration 2

1. Revisions are resolved to full SHAs by the harness, so no `^` ever reaches a shell.
2. The gita launcher is the real `gita.exe`, not a `.cmd` wrapper.
3. `gita diff` now reports when base and head are the same revision, instead of
   the ambiguous "no material changes" that hid this bug for a whole run.

### Lesson

Every gita call returned `ok=True`. Nothing crashed, nothing errored, and the
aggregate numbers were plausible. The tool was answering a question it had been
asked wrongly, and only the *size* of its answer gave it away.

---

## Design validation — one-shot answers (offline, no model)

Measured directly on the nine committed evaluation tasks, comparing output size only.

| task | raw git | brief (old) | one-shot | vs git | includes code |
| --- | ---: | ---: | ---: | ---: | :--: |
| flask-dependency-update | 220,377 | 721 | 2,203 | **-99%** | yes |
| gin-history | 76,637 | 992 | 5,817 | **-92%** | yes |
| got-new-option | 16,713 | 996 | 5,133 | **-69%** | yes |
| ripgrep-walker | 622 | 118 | 471 | -24% | yes |
| gin-public-api | 1,140 | 57 | 908 | -20% | yes |
| express-ci-bump *(control)* | 1,057 | 153 | 948 | -10% | yes |
| flask-teardown-review | 4,659 | 553 | 4,463 | -4% | yes |
| express-send-condition | 703 | 190 | 682 | -3% | yes |
| gin-copy-fix | 665 | 85 | 661 | -1% | yes |
| **total** | **322,573** | **3,865** | **21,286** | **-93%** | |

The old output was 98.8% smaller than git but **incomplete**, which forced a drill-down and cost
~1.25 turns at ~126,000 tokens each. The new output is 5.5x larger and **self-sufficient**: every
task includes the actual hunks, so no follow-up is required.

Two properties matter more than the headline:

- **The invariant holds on every task.** gita is never larger than the `git diff` it replaces,
  so adopting it cannot cost more than not adopting it.
- **On small diffs gita is roughly the same size as git** (-1% to -10%), so there is no penalty
  where there is nothing to compress. The wins are concentrated exactly where they should be.

This predicts the turn penalty should disappear. Iteration 3 will test that against a live agent.
