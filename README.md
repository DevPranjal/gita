# gita

**git, rebuilt for a world of agent coders.**

`git diff` answers *which bytes moved*. An agent needs *which named things changed,
and can that break a caller*. gita answers the second question, in one command, and
is **never larger than the `git diff` it replaces**.

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

## Why raw git output is bad context, and what gita does instead

| Problem | Why it hurts | gita's answer |
| --- | --- | --- |
| **Lost in the middle** | Diffs land in the attention trough between system prompt and task, pushing instructions further away each turn | Ranked, not chronological: interface breakage on line 3, not line 400. Small enough to have no middle |
| **Tokenizer overhead** | Per-line `+`/`-` prefixes, repeated filenames, `@@` headers. Measured **+25.2%** here; one 40-char SHA costs **20 tokens** | Entity ids, no line prefixes, filenames once, no SHAs in the body |
| **Low signal density** | 3 context lines around every hunk, whitespace churn, timestamps, advice text | Formatting, comment and import-order churn removed by comparing normalised hashes, and the count reported (`1900 noise filtered`) |
| **Zombie tokens** | Tool output is re-billed every following turn. 16,713 tokens over 5 turns = **83,565 token-turns** | One self-sufficient answer: 3,969 over 5 turns = **19,845** — and it cuts turns too (3.73 → 3.00) |
| **Induced failures** | Deleted code stays visible and gets called again; agents oscillate re-reading their own edits; a lockfile diff blows the window | Removal is a fact (`[removed]`), not lingering source text. Hard token budget. Dependency-update task: **220,377 → 2,203 tokens** |

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

10 tasks over 5 real repositories (Flask, Express, Gin, ripgrep, got), 3 repetitions,
two arms: an agent with plain `git`, and the same agent with `gita` also on `PATH`.
gita is **never mentioned in the prompt** — it must be discovered and chosen. Cost is
priced in real credits from the model's own logs.

| iteration | change | vs plain git |
| --- | --- | ---: |
| 1–2 | progressive disclosure, agent drills down | **+44%** |
| 3 | one-shot self-sufficient answers | −8.3% |
| 5 | ASCII-safe output, history detail | −16.7% |
| 7 | harness scaffolding excluded | **−19.5%** |
| 8 | unreferenced-addition reporting | −9.5% *(regression, diagnosed and fixed)* |

Also: turns **3.73 → 3.00**, tool output **−89% to −93%**, wall clock **−55%**, entity
recall **100% in both arms**, adoption **100% unprompted**.

The invariant holds on every task — gita is never larger than the `git diff` it
replaces — so adopting it cannot cost more than not adopting it. On small diffs it is
roughly the same size as git; the wins concentrate where the pain is.

**Every cost regression so far has been a correctness defect wearing a cost disguise**
— an encoding crash on a Windows pipe, history that said *when* but not *what*, Rust
`impl Trait for Type` losing the trait, 159 test names burying the one API change
asked about. The cost number is a bug detector.

## What gita does not do

- **No call graph.** `unreferenced` is *name matching*: it catches dead code, not
  dependents. Labelled as such wherever it appears.
- **No "what should I re-test?"** Needs real caller edges. gita refuses rather than guesses.
- **No natural-language questions.** An earlier `ask()` was withdrawn for degrading to
  confidently wrong instead of bluntly right.
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
