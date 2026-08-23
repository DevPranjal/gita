# gita

**git, rebuilt for a world of agent coders.**

When a coding agent needs to understand a change, it runs `git diff` and reads every
changed line. That is a wall of text, most of it irrelevant — and the agent pays for
all of it, again, on every step that follows.

gita answers the question instead: **which functions changed, and does anything
break?** One command, and never bigger than the `git diff` it replaces.

```bash
gita diff HEAD^ HEAD
```

```
13 files | 192 changes | 172 interface | 1900 noise filtered
files: documentation/2-options.md, source/core/options.ts, test/hooks.ts, ...

source/core/options.ts::Options::allowAbsoluteUrls  [added]
source/core/options.ts::applyUrlOverride            [signature_changed]
test/hooks.ts       (58 tests: 57 added, 1 body_changed)
test/pagination.ts  (47 tests: 46 added, 1 body_changed)

--- source/core/options.ts::Options::allowAbsoluteUrls  [added]
@@ ...
```

That commit is **16,713 tokens** of `git diff`. gita answers it in **3,969**, code
included — and names `allowAbsoluteUrls`, which is what you actually asked.

---

## The problem, plainly

An agent reads code through a context window it pays for by the token, and a raw diff
is a poor way to fill it.

| What goes wrong | Why | What gita does |
| --- | --- | --- |
| **The answer is buried** | A diff is ordered by file, not by importance. The one function that broke sits somewhere inside 400 lines, and models pay least attention to the middle of a long input. | Puts what matters first. The broken signature is on line 3, and the whole answer is short enough that there is no middle to get lost in. |
| **Most of it is not the change** | Reformatting, reordered imports, and three unchanged lines printed around every edit. | Removes it, and says how much it removed: `1900 noise filtered`. |
| **The formatting itself costs money** | Every line carries a `+` or `-`, filenames repeat three times per file, and commit hashes are long random strings. Measured here: **25% extra tokens**, and **20 tokens** for a single commit hash. | Names each file once, no per-line prefixes, no hashes in the body. |
| **You pay for it again on every step** | Tool output is re-sent to the model on every later step. A 16,713-token diff read early in a five-step task is billed five times — **83,565 tokens**. | One answer of 3,969 tokens is **19,845** over the same five steps, and it usually needs fewer steps. |
| **It causes real mistakes** | Deleted code still looks like working code in the window, so agents call functions that no longer exist. One lockfile update can fill the entire window. | States a deletion as a fact (`[removed]`) rather than leaving it lying around as source. Hard size limit: a dependency update goes from **220,377 tokens to 2,203**. |

---

## Install

```bash
pip install -e ".[dev]"     # Python 3.11+
```

## Commands

```bash
gita diff                      # uncommitted work, including untracked files
gita diff HEAD^ HEAD           # one commit
gita diff main HEAD            # a branch or PR range

  --interface-only   only changes that can break a caller
  --patch            plain unified diff, noise removed
  --brief            summary only, no code
  --filter auth      narrow by name or path
  --budget N         hard cap, honoured exactly
  --json             machine-readable

gita show <name>               # exact hunks for one entity
gita expand <id>               # open a rolled-up line
gita history <name>            # how one function changed over time, with the code
gita savings                   # what this session cost vs raw git diffs
gita serve                     # MCP server
```

`-C <path>` selects the repository. Bare names work (`gita history fetch`).
Ambiguity is **reported, never guessed**.

## Change kinds

| Kind | Breaks a caller? |
| --- | --- |
| `signature_changed`, `removed`, `renamed` | **yes** |
| `added`, `moved`, `body_changed` | no |
| `cosmetic` | filtered out before you see it |

Computed from four hashes per entity — raw, normalised, signature-only, body-only —
so `signature_changed` is a fact, not an inference.

## How it works

**Deterministic core, probabilistic garnish.** Every fact is computed: tree-sitter
parse, then matching by stable entity id, then content hash, then similarity. No model
decides what changed. A model may later improve summary *prose*; it may never supply a
fact. gita degrades to worse prose, never to wrong facts.

Python, JavaScript, TypeScript, TSX, Go, Rust, plus structural Markdown, YAML, JSON and
TOML. Unsupported files degrade to a whole-file entity rather than vanishing.

## Does it actually work?

18 tasks over 5 real repositories (Flask, Express, Gin, ripgrep, got), two arms: an
agent with plain `git`, and the same agent with `gita` also on `PATH`. gita is
**never mentioned in the prompt** — it must be discovered and chosen. Cost is priced
in real credits from the model's own logs.

**216 runs** across two sweeps, three repetitions of every task in each:

| | plain git | with gita | |
| --- | ---: | ---: | ---: |
| credits per task | 43.7 | 35.3 | **−14.3%** |
| turns per task | 3.51 | 2.76 | **−21.4%** |
| entity recall | 99.5% | 99.7% | +0.2pt |

The cost figure is quoted after discarding the runs that lost the prompt cache —
noise priced at 12.5×, unrelated to either tool — and carries a **95% interval of
−3% to −25%**, bootstrapped over tasks. The two sweeps read −16.7% and −11.2%
individually and **cannot be told apart** by this harness (the interval on their
difference is −9 to +26 points), so they are pooled and both are shown rather
than the better one being picked.

Tool output is **not** reported as a headline. It reads −46% pooled, but it read
−92% on ten tasks and **+0.4%** on eighteen — a number that swings that far with
the sample is not measuring the tool.

Two earlier claims did not survive more data, and are corrected here rather than
quietly dropped:

- **Tool output was published as −92.4%.** It was carried by one lockfile task
  and collapsed once the corpus was large enough to dilute it. Withdrawn as a
  headline claim.
- **The headline was published as −15.4%.** The point estimate compared arm
  totals while the interval beside it paired by task. On a balanced sweep those
  agree exactly, which is how it survived fourteen sweeps. Paired, those same
  runs read −17.9%.

One benchmark task was also corrected: its answer key demanded the name of a test
that the commit *deletes*. Both arms named the added test and both were marked
down, so the fix moves both from 50% to 100% and cannot favour either.

The invariant holds on every task — gita is never larger than the `git diff` it
replaces — so adopting it cannot cost more than not adopting it. On small diffs it is
roughly the same size as git; the wins concentrate where the pain is.

## What gita does not do

- **No call graph.** `unreferenced` is *name matching*: it catches dead code, not
  dependents. Labelled as such wherever it appears.
- **No "what should I re-test?"** Needs real caller edges. gita refuses rather than guesses.
- **No natural-language questions.** gita reports facts it computed and refuses
  questions it cannot answer exactly.
- **Prose diffing is structural only** — sections and keys, not meaning.
- **Bulk tests are summarised** when they accompany source changes; `--filter` to expand.

## Roadmap

- **WS-2 Blast radius** — real caller edges, making "what should I re-test" exact.
- **WS-5 Memory** — persistent entity index, so history stops being O(commits).
- **WS-7 Document semantics** — meaning-level diffing for prose and config.
- **WS-3 Local narration** — on-device model for summary prose only, never facts.

## Development

```bash
pytest -q --ignore=tests/test_corpus.py    # 335 tests
python -m gita.eval.main --reps 3          # full evaluation sweep
```

Rationale in [docs/SCOPE.md](docs/SCOPE.md), measurements in
[evals/RESULTS.md](evals/RESULTS.md), working log including failures in
[scratch/JOURNAL.md](scratch/JOURNAL.md).
