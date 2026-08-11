# gita

**git, rebuilt for a world of agent coders.**

`git diff` answers *which bytes moved*. An agent almost always needs *which named
things changed, and can that break a caller*. gita answers the second question, in
one command, and is **never larger than the `git diff` it replaces**.

```bash
gita diff HEAD^ HEAD
```

```
13 files | 192 changes | 172 interface | 1900 noise filtered
files: documentation/2-options.md, source/core/options.ts, test/hooks.ts, ...

documentation/2-options.md::Options::`allowAbsoluteUrls`  [added]
source/core/options.ts::Options::allowAbsoluteUrls        [added]
source/core/options.ts::applyUrlOverride                  [signature_changed]
source/core/options.ts::assertRelativeUrlIfNeeded         [added]
test/hooks.ts       (58 tests: 57 added, 1 body_changed)
test/pagination.ts  (47 tests: 46 added, 1 body_changed)

--- source/core/options.ts::Options::allowAbsoluteUrls  [added]
@@ ...
```

That commit is **16,713 tokens** of `git diff`. gita answers it in **3,969**, including
the code — and it names `allowAbsoluteUrls`, which is what you actually asked.

---

## Why git output is bad context

git was designed for a human reading a patch in a terminal, once. An agent reads
through a context window, pays for it, and pays for it **again on every subsequent
turn**. Five specific things go wrong.

### 1. Attention dilution — the "lost in the middle" problem

Attention is not uniform across a context window. Instructions at the start and the
task at the end are weighted heavily; everything in between sits in a trough.

```
High |  system prompt   |   RAW GIT DIFF   |  current task   |
     |  & tool defs     |   & CLI dumps    |  & instructions |
 Low  ---- BEGINNING ---+----- MIDDLE -----+---- RECENT -----
```

Every `git log` or `git diff` dumps hundreds of lines straight into that trough, and
pushes the system prompt further from the task with each turn. The model starts
drifting from constraints it was given because its attention budget is spent on
line-by-line noise.

**What gita does.** The answer is ranked, not chronological. Interface breakage is on
line 3, not line 400. The whole answer is small enough that there is no middle to get
lost in.

### 2. Tokenizers are hostile to diff syntax

Measured on this repository with `cl100k_base`:

| | measured |
| --- | ---: |
| `git log -p -8` | 23,572 tokens |
| the same text without diff prefixes and headers | 18,831 tokens |
| **overhead from diff syntax alone** | **+25.2%** |
| of which `diff --git` / `index` / `@@` headers | 3,369 tokens |
| a single 40-character commit SHA | **20 tokens** |

Every line carries a `+`, `-` or space that the tokenizer splits badly. Filenames
repeat three times per file chunk. Twenty commits of history spend **~400 tokens on
hexadecimal strings with no semantic value**.

**What gita does.** Entity ids instead of hunk headers, no per-line prefixes in the
summary, filenames stated once. Where hunks genuinely help, they are scoped to one
entity rather than one file.

### 3. Low signal density

| git output | high-entropy noise | actual signal |
| --- | --- | --- |
| `git log` | SHAs, committer emails, timestamps, PGP signatures, merge commits | the message, the paths |
| `git status` | untracked build artefacts, branch tracking, advice text | which files are modified |
| `git diff` | 3 context lines around every hunk, line-number headers, whitespace churn | the logic that changed |

**What gita does.** Noise is classified and removed before you see it — the headline
reports how much (`1900 noise filtered`). Formatting-only changes, comment edits and
import reordering are detected by comparing normalised content hashes, so removing
them is a fact, not a heuristic guess.

### 4. Zombie tokens — the compounding multiplier

Tool output does not cost you once. It is re-sent on every following turn.

```
Turn 1: git diff        -> 1,000 tokens
Turn 2: + pytest        -> Turn 1 is billed again
Turn 5: final fix       -> Turn 1 has now been billed five times
```

Taking the real example above: 16,713 tokens of `git diff` across 5 turns is
**83,565 token-turns**. gita's 3,969-token answer across the same 5 turns is
**19,845** — and in practice it also *reduces* the turn count, because the answer is
complete enough that no follow-up is needed.

**What gita does.** One self-sufficient answer. If the budget forced something out,
the output says so and names what is missing, so a follow-up is a deliberate choice
rather than a forced one.

### 5. Failure modes that git output actively induces

- **Hallucinated edits from deleted code.** A diff showing `- def old_function()`
  leaves `old_function` sitting in the context window. Models later import or call
  it. gita reports removal as a *fact about an entity* (`[removed]`) rather than as
  text that still looks like source.
- **Infinite fixing loops.** Large diffs let an agent re-read its own earlier edits
  and oscillate. gita reports the current state of named things, not a scrolling
  record of edits.
- **Lockfile disasters.** An unconstrained `git diff` after a dependency update pulls
  tens of thousands of lines of `package-lock.json` into context. gita treats data
  files structurally and enforces a hard token budget. On the dependency-update task
  in our corpus, this is the difference between **220,377 tokens and 2,203**.

---

## Install

```bash
pip install -e ".[dev]"
```

Requires Python 3.11+. Parsing is tree-sitter; token counting is tiktoken.

## Commands

All commands take `-C <path>` for the repository.

```bash
gita diff                      # uncommitted work, including untracked files
gita diff HEAD^ HEAD           # one commit
gita diff main HEAD            # a branch or PR range

gita diff <base> <head> --interface-only   # only changes that can break a caller
gita diff <base> <head> --patch            # plain unified diff, noise removed
gita diff <base> <head> --brief            # summary only, no code
gita diff <base> <head> --filter auth      # narrow by name or path
gita diff <base> <head> --budget N         # hard cap, honoured exactly
gita diff <base> <head> --json             # machine-readable

gita show <name>               # exact hunks for one entity
gita expand <id>               # open a rolled-up line
gita history <name>            # how one function changed over time, with the code
gita savings                   # what this session cost vs raw git diffs
gita serve                     # MCP server
```

Bare names work: `gita history fetch`, not just `gita history svc.py::fetch`.
Ambiguity is **reported, never guessed**.

Aliases, because the project is named after a text about doing your duty well:
`darshan` (diff), `shloka` (show), `katha` (history), `vistaar` (expand).

## Change kinds

| Kind | Meaning | Breaks a caller? |
| --- | --- | --- |
| `signature_changed` | parameters or return type changed | **yes** |
| `removed` | entity is gone | **yes** |
| `renamed` | same code, new name | **yes** |
| `added` | new entity | no |
| `moved` | same code, new location | no |
| `body_changed` | behaviour changed, interface intact | no |
| `cosmetic` | formatting, comments, import order | filtered out |

These are computed from four separate hashes per entity — raw bytes, normalised
content, signature-only and body-only — so `signature_changed` is a fact, not an
inference.

---

## How it works

**Deterministic core, probabilistic garnish.** Every fact gita reports is computed:
parsed with tree-sitter, matched by stable entity id, then by content hash, then by
similarity. No model is consulted to decide what changed. A language model may later
improve the *prose* of a summary line; it may never supply a fact. The design target
is that gita degrades to worse prose, never to wrong facts.

```
git blobs -> tree-sitter -> entity tree -> three-pass matching -> ranking -> budgeted answer
```

Entities carry stable ids (`path::Parent::child`) so a function that moves within a
file is recognised as the same function. Cross-file moves are reconciled explicitly.

Supported: Python, JavaScript, TypeScript, TSX, Go, Rust, plus structural handling of
Markdown, YAML, JSON and TOML. Unsupported files degrade to a whole-file entity
rather than disappearing.

---

## Does it actually work?

There is a real evaluation harness: 10 tasks over 5 real repositories (Flask,
Express, Gin, ripgrep, got), 3 repetitions, two arms — an agent with plain `git`, and
the same agent with `gita` also on `PATH`. gita is **never mentioned in the prompt**;
it has to be discovered and chosen, or it does not count. Cost is priced in real
credits from the model's own logs, not token proxies.

| iteration | design change | credits vs plain git |
| --- | --- | ---: |
| 1–2 | progressive disclosure, agent drills down | **+44%** |
| 3 | one-shot self-sufficient answers | −8.3% |
| 4 | regression fixes, one new defect | −4.4% |
| 5 | ASCII-safe output, history detail | −16.7% |
| 6 | untracked files included | −16.8% |
| 7 | harness scaffolding excluded | **−19.5%** |
| 8 | unreferenced-addition reporting | −9.5% *(regression, diagnosed and fixed)* |

Alongside cost, and consistent across sweeps: **turns 3.73 → 3.00**, tool output
**−89% to −93%**, wall clock **−55%**, entity recall **100% in both arms**, adoption
**100% unprompted**.

Two properties matter more than the headline:

- **The invariant holds on every task.** gita is never larger than the `git diff` it
  replaces, so adopting it cannot cost more than not adopting it.
- **On small diffs gita is roughly the same size as git** (−0% to −10%). The wins
  concentrate exactly where the pain is: the dependency update is −99%, the large
  history query −92%.

### What the evaluation is really for

Every cost regression so far has turned out to be a **correctness or robustness
defect wearing a cost disguise**. Not one was a tuning problem.

| symptom | actual defect |
| --- | --- |
| +149% on one task | `UnicodeEncodeError` on a piped Windows shell |
| +29% on history | reported *when* something changed but not *what* |
| +18%, recall 78% | Rust `impl Trait for Type` named after the type, losing the trait |
| +111%, 7.7 turns | 159 test names buried the one API change that was asked about |
| gita never invoked | the tool description omitted the working-tree form |

The cost number is a **bug detector**. That is why the loop is worth running.

---

## What gita does not do

Stated plainly, because a tool that overstates its reach gets distrusted on the first
counter-example.

- **No call graph.** `unreferenced` is *name matching* — it reliably catches dead code
  and half-wired additions, and it does **not** tell you what depends on something.
  Labelled as such everywhere it appears.
- **No "what should I re-test?"** That needs real caller edges. gita refuses rather
  than guessing.
- **No natural-language question interface.** An earlier `ask()` was withdrawn because
  it degraded to *confidently wrong* instead of *bluntly right*.
- **Semantic diffing of prose is structural only.** Markdown and YAML are diffed by
  section and key, not by meaning.
- **Tests are summarised in bulk** when they accompany source changes. Use `--filter`
  if you need them individually.

## Roadmap

| | |
| --- | --- |
| **WS-2 Blast radius** | real caller edges, so "what should I re-test" becomes answerable exactly. Today only the name-matching slice exists. |
| **WS-5 Memory** | a persistent entity index, so history queries stop being O(commits) with a full parse each. |
| **WS-7 Document semantics** | meaning-level diffing for prose and config, beyond structure. |
| **WS-3 Local narration** | a small on-device model to improve summary *prose* only, never facts. The output must remain correct with the model switched off. |

## Development

```bash
pip install -e ".[dev]"
pytest -q --ignore=tests/test_corpus.py    # 335 tests
python -m gita.eval.main --reps 3          # the full evaluation sweep
```

`tests/test_corpus.py` runs separately because the evaluation mutates the corpus
repositories.

Design rationale lives in [docs/SCOPE.md](docs/SCOPE.md), measured outcomes in
[evals/RESULTS.md](evals/RESULTS.md), and the working log — including the failures —
in [scratch/JOURNAL.md](scratch/JOURNAL.md).
