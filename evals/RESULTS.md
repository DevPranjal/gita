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

---

## Iteration 3 — 2026-08-10 · one-shot answers · **VALID**

60 runs · 10 tasks · 3 repetitions · git-only vs git+gita · claude-opus-5
Artifacts: `evals/runs/20260810-201333/`

### Result

| Metric | git | gita | |
| --- | ---: | ---: | --- |
| Turns per task | 3.87 | **3.30** | -15% |
| Raw prompt tokens (paired) | 4,835,100 | **4,133,935** | **-15%** |
| **Billed (uncached) tokens** | **26,535** | 27,531 | **+3.8% worse** |
| Tool output per run | 45,997 | **3,421** | -93% |
| Wall clock per task | 160.9s | **72.2s** | **-55%** |
| Entity recall | 97% | **98%** | +1pt |
| Adoption (unprompted) | — | 93% | |

### The two cost figures disagree, and the billed one is the honest one

gita sends 15% less total context and uses 15% fewer turns. But its output is *fresh*
content, billed as cache writes at ~1.25x, while git's repeatedly re-sent context is
served from cache at ~0.1x. Price-weighted, the two arms are **a wash**.

The unambiguous wins are **turns (-15%)**, **tool output (-93%)**, **wall clock (-55%)**
and **recall (+1pt)**. The token-cost win claimed from raw prompt totals does not survive
price weighting, and should not be quoted.

### Per task

| task | Δ tokens | turns | recall git → gita |
| --- | ---: | --- | --- |
| flask-teardown-review | **-67%** | 6.0 → 2.0 | 100 → 100 |
| flask-dependency-update | **-57%** | 7.0 → 3.0 | 100 → 100 |
| got-new-option | -12% | 4.7 → 4.0 | 100 → 100 |
| gin-copy-fix | -11% | 3.0 → 2.7 | 100 → 100 |
| express-ci-bump *(control)* | +0% | 2.0 → 2.0 | 100 → 100 |
| express-send-condition | +1% | 3.0 → 3.0 | 100 → 100 |
| gin-public-api | +1% | 2.0 → 2.0 | **67 → 100** |
| ripgrep-walker | +28% | 3.7 → 4.7 | **100 → 78** |
| gin-history | +32% | 3.3 → 4.3 | 100 → 100 |
| flask-uncommitted | +34% | 4.0 → 5.3 | 100 → 100 |

The shape is right: large wins where the diff is large, exactly neutral on the control.

### Correction to the cost model used in iterations 1 and 2

Earlier analysis quoted "a turn costs ~126,000 tokens", comparing a **naive** token count
against gita's **priced** output. Measured from the run logs, 92.7% of prompt tokens are
cache reads at ~0.1x and 7.3% are cache writes at ~1.25x, so price-weighted input is
**0.18x** of the naive total: a turn costs about **23,000 token-equivalents, not 126,000**.

One-shot answers remain the right design, but for a narrower margin than claimed:
an extra turn costs roughly 14-23k equivalents, an extra 5k of output roughly 7k.
**About 2-3x, not 100x.**

### Open problems

1. `flask-uncommitted` is still +34% despite the working-tree default fix.
2. `ripgrep-walker` recall fell to 78%, the only quality regression, and it also costs more.
3. `gin-history` +32%: `gita history` walks commits one at a time and is slow.
4. Baseline noise is real: `gin-public-api` scored 67% recall for git and 100% for gita.
5. 10 tasks, 3 repetitions, one model. Directional, not significant.

### Correction, again: actual credits, not token proxies

Real GitHub Copilot pricing for Claude Opus 5, credits per 1M tokens:
input **500**, output **2500**, cache read **50**, cache write **625**.

Three readings of the *same* iteration-3 run disagree:

| Metric | Reading | Verdict |
| --- | ---: | --- |
| raw prompt tokens | gita **-15%** | ignores that 93% are cached |
| prompt minus cached | gita **+3.8%** | treats cache reads as free and ignores output |
| **actual credits** | **gita -8.3%** | **the true figure** |

| arm | fresh | cache read | cache write | output | credits/run |
| --- | ---: | ---: | ---: | ---: | ---: |
| git | 8 | 456,975 | 26,528 | 1,070 | **42.11** |
| gita | 7 | 385,863 | 27,524 | 842 | **38.60** |

Where the money actually goes:

| arm | fresh | cache read | cache write | output |
| --- | ---: | ---: | ---: | ---: |
| git | 0% | **54%** | 39% | 6% |
| gita | 0% | 50% | 45% | 5% |

**Cache reads are the largest single line item** in both arms, despite costing a tenth of
list rate, purely because there are so many of them. gita wins by re-reading less context
(fewer turns) and by making the model **generate 21% fewer output tokens** -- the most
expensive class at 5x. It loses slightly on cache writes, because its output is fresh
content.

Per task, in credits:

| task | git | gita | |
| --- | ---: | ---: | ---: |
| flask-dependency-update | 64.96 | 35.36 | **-46%** |
| flask-teardown-review | 76.33 | 48.08 | **-37%** |
| gin-copy-fix | 31.26 | 28.65 | -8% |
| got-new-option | 45.75 | 45.02 | -2% |
| express-ci-bump *(control)* | 23.63 | 23.73 | +0% |
| express-send-condition | 30.09 | 30.89 | +3% |
| gin-public-api | 43.04 | 44.23 | +3% |
| ripgrep-walker | 36.05 | 42.63 | +18% |
| flask-uncommitted | 36.35 | 45.58 | +25% |
| gin-history | 33.62 | 41.87 | +25% |
| **total** | **421.07** | **386.04** | **-8.3%** |

The pricing model now lives in [`eval/pricing.py`](../src/gita/eval/pricing.py) and the
harness records credits per run, so this is never hand-computed again.

## Iteration 5 — 2026-08-11 · regressions fixed · **VALID**

60 runs. Artifacts: `evals/runs/20260811-054629/`

| metric | git | gita | |
| --- | ---: | ---: | --- |
| **credits / task** | 38.94 | **32.42** | **-16.7%** |
| turns | 3.67 | **3.00** | -18% |
| recall | 100% | **100%** | equal |
| adoption | - | **100%** | |

Progression across iterations, credits versus plain git:

| iteration | design | result |
| --- | --- | ---: |
| 1-2 | progressive disclosure, agent drills | **+44%** |
| 3 | one-shot answers | -8.3% |
| 4 | regression fixes, one new defect | -4.4% |
| **5** | ASCII output, history detail, discovery | **-16.7%** |

Per task, credits versus git:

| task | it3 | it4 | **it5** |
| --- | ---: | ---: | ---: |
| flask-teardown-review | -37% | -50% | **-60%** |
| flask-dependency-update | -46% | -40% | **-27%** |
| express-send-condition | +3% | -5% | **-25%** |
| ripgrep-walker | +18% | -7% | **-18%** |
| gin-public-api | +3% | -20% | **-8%** |
| got-new-option | -2% | +149% | **-1%** |
| gin-copy-fix | -8% | -8% | **-0%** |
| express-ci-bump *(control)* | +0% | +0% | **+0%** |
| gin-history | +25% | +29% | **+6%** |
| flask-uncommitted | +25% | +19% | **+26%** |

**Nine of ten tasks now match or beat plain git**, and the control sits at exactly +0%.

Every cycle-2 diagnosis was confirmed by the numbers:

- `got-new-option` +149% -> -1% once output stopped raising `UnicodeEncodeError` on a
  piped Windows shell. The encoding defect was the entire regression.
- `gin-history` +29% -> +6% once history reported *what* changed, not only *when*.
- `ripgrep-walker` -7% -> -18% with `impl Trait for Type` naming.

### Remaining: flask-uncommitted (+26%)

Identical in all three repetitions: `gita diff` answered in 86 tokens, then the agent ran
`git status --short`, then `git diff`. Probing why exposed a **correctness gap rather than a
cost one**: `git diff HEAD` does not list untracked files, so a module an agent has just
written is invisible to gita. "What did I change" includes files that do not exist in HEAD
yet, and answering half the question sends the agent to git for the other half.

Untracked files are now included when diffing the working tree.

---

## Iteration 9 - 2026-08-11 - test churn rolled up - **VALID**

60 runs. Artifacts: `evals/runs/20260811-104841/`

| metric | git | gita | |
| --- | ---: | ---: | --- |
| **credits / task** | 36.37 | **29.79** | **-18.1%** |
| turns | 3.33 | **2.67** | **-20%** (best so far) |
| tool tokens | 9,317 | **1,940** | -79% |
| recall | 100% | 98.3% | **-1.7pt** |
| adoption | - | **100%** | |

### The regression was where the diagnosis said it was

`got-new-option` **+111% -> -30%**, turns **7.7 -> 2.0**. Rolling 159 test cases
up to one line per file put `allowAbsoluteUrls` back in the agent's view.

| task | it7 | it8 | **it9** |
| --- | ---: | ---: | ---: |
| flask-teardown-review | -56% | -52% | **-58%** |
| got-new-option | +16% | +111% | **-30%** |
| express-send-condition | -22% | -20% | **-22%** |
| gin-copy-fix | +1% | -0% | **-15%** |
| flask-dependency-update | -41% | -42% | **-13%** |
| gin-public-api | -20% | -8% | **-8%** |
| express-ci-bump *(control)* | -2% | -9% | **-1%** |
| ripgrep-walker | -9% | -5% | **+1%** |
| gin-history | -15% | -9% | **+3%** |
| flask-uncommitted | +41% | -2% | **+12%** |

The git baseline itself moved 7% between sweeps (390 -> 364 total credits), so
per-task deltas carry real noise. The aggregate is the number to trust, and
-16.7 / -16.8 / -19.5 / -18.1 across four clean sweeps is a stable band.

### The recall miss was worth more than the cost win

One `flask-dependency-update` repetition scored **0.50**: it named `pyproject.toml`
but not `uv.lock`. All three repetitions ran identical commands and gita returned
byte-identical output, so this was agent variance -- but probing why exposed two
real defects.

**gita mentioned `pyproject.toml` 25 times and `uv.lock` twice.** Four
`examples/*/pyproject.toml` files gained the same isort block, and each was
listed separately. The same repetition defect as the test churn, in a different
shape. Identical changes across three or more files now collapse to one line.

**A dependency version bump was invisible.** Chasing the lockfile led to this:
tree-sitter gives a TOML string two quote children with the value in the gap
between them, owning no node. `_own_tokens` took only leaves, so it collected
`"` and `"` and dropped the value. `flask = "3.0"` and `"3.1"` hashed
identically and were filtered as **cosmetic**. gita reported *no material
changes* for a version bump, in TOML and YAML alike.

This is the worst class of bug gita can have -- not an unhelpful answer, a
confidently wrong one -- and it sat in the config category the whole time,
under a task that was scoring -41%. A cost win was masking a correctness hole.

**Lesson:** a recall miss is worth more attention than a cost regression. The
cost numbers found five bugs by being bad; this one was found by a quality
number moving 1.7 points while the cost number looked fine.

---

## Iteration 11 - 2026-08-11 - consistent surface - **VALID**

60 runs. Artifacts: `evals/runs/20260811-134253/`

| metric | git | gita | |
| --- | ---: | ---: | --- |
| credits / task | 44.8 | **32.4** | **-27.7%** |
| turns | 3.93 | **2.73** | **-31%** |
| tool output / run | 48,224 | **2,873** | -94% |
| recall | 98.3% | **100%** | **+1.7pt** |
| adoption | - | **100%** | |

**Nine of ten tasks beat plain git**, and every task that had regressed is fixed:

| task | it9 | it10 | **it11** |
| --- | ---: | ---: | ---: |
| flask-dependency-update | -13% | -34% | **-52%** |
| gin-copy-fix | -15% | -8% | **-43%** |
| express-send-condition | -22% | -22% | **-32%** |
| flask-uncommitted | +12% | +52% | **-31%** |
| flask-teardown-review | -58% | -67% | **-26%** |
| got-new-option | -30% | -14% | **-18%** |
| gin-history | +3% | +41% | **-17%** |
| express-ci-bump *(control)* | -1% | -8% | **-9%** |
| gin-public-api | -8% | -8% | **-8%** |
| ripgrep-walker | +1% | +15% | **+7%** |

`gin-history` (+41% -> -17%) and `flask-uncommitted` (+52% -> -31%) were both pure
surface defects, not answer quality: gita's output was byte-identical before and
after. One demanded `--since` where every other command takes revisions
positionally; the other made the agent run `git status` because gita would not
say which files were new.

### Do not read -27.7% as the headline

The git baseline drifts between sweeps, and this time it drifted up:

| run | git | gita | delta |
| --- | ---: | ---: | ---: |
| it9 | 36.4 | **29.8** | -18.1% |
| it10 | 42.8 | 36.4 | -15.0% |
| it11 | **44.8** | 32.4 | -27.7% |

gita's own cost, 32.4, is not its lowest -- it9 was cheaper in absolute terms.
Part of this sweep's margin is git getting more expensive, not gita getting
cheaper. The defensible claim is **a band of -15% to -28% with a median near
-18%**, on a paired design where both arms run in the same session.

The baseline-independent results are the stronger ones: **turns 3.93 -> 2.73**,
**tool output -94%**, and **recall above the git arm for the first time**.

### Remaining: ripgrep-walker (+7%)

The smallest task in the corpus -- 622 tokens of raw diff, so there is almost
nothing to compress and any second call costs more than gita saves. One
repetition guessed `gita show Walk --at <rev>`; `--at` does not exist, and the
error printed the *global* usage rather than `gita show`'s own signature. Fixed,
but the honest position is that gita has little to offer on a diff this small,
which is what the invariant already predicts.

---

## Iteration 13 - 2026-08-11 - **control run: no code change**

60 runs. Artifacts: `evals/runs/20260811-160809/`. gita's behaviour is
byte-identical to iteration 12; only the scoring code changed between them.

**The point of the run was to measure how much moves when nothing does.**

| | it12 | it13 | |
| --- | ---: | ---: | --- |
| credits, cache-clean | -20.1% | **-10.3%** | **10 points apart** |
| turns | 3.93 -> 2.67 | 3.57 -> 2.93 | stable |
| tool output | -95% | -93% | stable |
| recall | 1.000 / 0.989 | 1.000 / 1.000 | stable |

Per task, with no code change at all:

| task | it12 | it13 | swing |
| --- | ---: | ---: | ---: |
| got-new-option | -20% | +63% | **82pt** |
| ripgrep-walker | -6% | +39% | 45pt |
| gin-public-api | -47% | -8% | 39pt |
| flask-teardown-review | -70% | -33% | 38pt |
| gin-copy-fix | +55% | +23% | 31pt |
| gin-history | -16% | +15% | 31pt |
| express-send-condition | -2% | -28% | 26pt |
| flask-dependency-update | -31% | -52% | 21pt |
| flask-uncommitted | +2% | +15% | 13pt |
| express-ci-bump *(control)* | -8% | -9% | 1pt |

**At three repetitions, per-task cost deltas are noise.** Every per-task story
told in iterations 8 through 12 needs re-reading with that in mind.

### What this does and does not invalidate

It does **not** invalidate the fixes. Each one was verified by mechanism, not by
watching a number move:

- the TOML/YAML value bug was caught by a unit test that fails without the fix
- `gin-history`'s wasted turns were deterministic, in all three repetitions, and
  reproducible by hand at the command line
- the test-churn roll-up was verified by measuring the answer directly:
  5,273 -> 3,969 tokens, with `allowAbsoluteUrls` moving into view

It **does** invalidate the cost attributions -- "this fix took the task from +41%
to -17%" is not a claim three repetitions can support.

### The honest number

Pooling the two identical-behaviour sweeps gives 110 cache-clean runs, six
repetitions per task per arm:

| | git | gita | |
| --- | ---: | ---: | ---: |
| credits / task | 36.2 | **30.6** | **-15.4%** |
| turns / task | 3.62 | **2.78** | **-23.1%** |
| tool output / run | 35,163 | **2,689** | **-92.4%** |
| recall | 100% | 99% | -0.6pt |

Eight of ten tasks cheaper. **Turns and tool output are the stable results; cost
is the noisy one.**

> **Superseded by iteration 14.** Two of these figures did not survive the corpus
> growing to 18 tasks: `-92.4%` tool output is withdrawn (it reads `+0.4%`), and
> `-15.4%` was computed with an estimator that did not match its own interval
> (paired, it reads `-17.9%`). See the iteration 14 section at the end of this
> file. The sentence above is left as written, because a record that edits its
> own past claims cannot be used to check them.

---

## Iteration 14 -- 18 tasks, and two published claims withdrawn

`evals/runs/20260823-141818` -- 108 runs, 18 tasks, 3 repetitions per arm,
balanced 54/54. Measures v1.0.0: the only commit between this sweep and the tag
touched `evals/tasks.yaml`.

The corpus was doubled because the interval, not the point estimate, is what
ten tasks could not deliver. Resampling is over tasks, so repetitions cannot
narrow it; only more tasks can.

| | git | gita | |
| --- | ---: | ---: | ---: |
| credits / task | 46.5 | **35.5** | **-23.5%** |
| turns / task | 3.56 | **2.70** | **-24.0%** |
| recall | 96.3% | **97.2%** | +0.9pt |
| tool output / run | 10,478 | 10,522 | **+0.4%** |

95% interval **[6.0%, 38.9%]**. Dropping the 16 runs that lost the prompt cache
gives **16.7%** on **[6.2%, 26.5%]** -- half-width 10.2 points against 12.9 on
the ten-task basis. The interval excludes zero on both readings, which is the
first time that has happened at this scale. Adoption 100%.

### Withdrawn: tool output -92.4%

On ten tasks the arms read 35,163 against 2,689. On eighteen they read 10,478
against 10,522 -- **no difference at all.**

The old figure was carried by one task. `flask-dependency-update` contains a
214,918-token `uv.lock` diff, and at one task in ten that single number set the
mean. At one in eighteen it is diluted, and by iteration 14 the gita arm was
reading that lockfile too.

That is the more useful half of the finding. Across the 54 gita runs, gita's own
calls cost 76,067 tokens and falling back to raw git cost 492,105: **87% of the
gita arm's tool output was not gita.** The saving was won despite the fallbacks.

A claim that holds on ten samples and vanishes on eighteen was never a property
of the tool; it was a property of the sample. It is withdrawn rather than
restated on the corpus where it still looks good.

### Corrected: the headline was not the quantity its interval described

`reduction.credits` compared arm totals. `credit_interval` pairs by task. On a
balanced sweep the two agree to four decimal places, which is how the difference
survived fourteen sweeps unnoticed.

It shows up as soon as the arms are unbalanced -- which is exactly what dropping
cache misses does. On the 92 cache-clean runs of this sweep, 44 git against 48
gita, the totals ratio read **9.1%** where the paired figure read **16.8%**. On
a synthetic case with two tasks of unequal cost it reads **-119%** where the
truth is **+20%**.

Both now use the paired estimator. Re-scoring all fourteen previous sweeps
reproduces every historical figure exactly -- it9 18.10%, it11 27.69%, it12
23.74%, it13 control 5.40% -- because all of them were balanced. The only figure
that moves is the published one, which was computed on a cache-filtered subset:

| | published | corrected |
| --- | ---: | ---: |
| it12+it13 pooled, cache-clean | -15.4% | **-17.9%** |

The interval [4.3%, 30.1%], the turn counts and the tool-output figures all
reproduce exactly; only the point estimate moves, because only it used the wrong
estimator. The correction is in gita's favour, which is why it is stated with
its mechanism and a test that fails without it rather than simply applied.

### Standing corrections

| claim | status |
| --- | --- |
| credits -15.4% | superseded: **-16.7%**, [6.2%, 26.5%], 18 tasks |
| turns -23.1% | holds: **-24.0%** on 18 tasks |
| tool output -92.4% | **withdrawn**: +0.4% on 18 tasks |
| recall 100% / 99% | now 96% / 97%, both depressed by one miscalibrated key |

`flask-context-copy` scores 50% on both arms because its answer key demands
`test_greenlet_context_copying`, a test that commit *deletes*; the test that
covers the new behaviour is `test_copy_context_thread`, which it adds. Both arms
name the right one. The key is corrected after the sweep now running, so that
run is scored against the file it started with.
