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
