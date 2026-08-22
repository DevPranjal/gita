# Changelog

All notable changes to gita are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are grouped by workstream (WS-*) as defined in [docs/SCOPE.md](docs/SCOPE.md).

## [1.0.0] - 2026-08-22

First release intended for use rather than for evaluation. Three of these were
found by building the wheel and running it, not by the test suite.

### Fixed - the budget was not a budget

- **`--budget N` was exceeded whenever an addition was unreferenced.** The
  "unreferenced (name appears nowhere else)" line is up to 133 tokens and was
  appended *after* the budget had been spent, so `--budget 120` emitted 211. It
  is now trimmed to whatever room is left, and dropping it counts as truncation.
- **The answer could exceed the raw `git diff` by one token.** Cost was summed
  per part, which missed the blank line joining the summary to the hunks. The
  assembled text is now what gets measured. Swept across every budget from 1 to
  400: no violations.
- **`tiktoken` was an optional extra**, so a plain `pip install gita` produced a
  build that counted tokens as `chars // 4` -- an estimate that undercounted 61%
  of corpus files, making the documented hard cap a suggestion. It is now a
  dependency. The fallback survives for exotic environments but errs high on
  purpose: an estimate used as a cap must never guess low.

### Changed

- The token encoder is built on first use rather than at import, taking ~280ms
  off every invocation that prints usage or an error and never counts a token.
  Commands that do count are unaffected, as expected.

### Added - packaging

- MIT licence, `py.typed`, project URLs, classifiers, keywords and a readme
  reference. The version is now read from the package, so it cannot drift.
- CI covers Python 3.11, 3.12 and 3.13.

- 445 tests, plus 20 corpus invariants.

## [Unreleased]

### Added — WS-4 · Context layers

- **Token accounting** ([`context/tokens.py`](src/gita/context/tokens.py)) — tiktoken when
  available so budgets match what an agent actually pays, with a character estimate fallback.
- **Ranking** ([`context/rank.py`](src/gita/context/rank.py)) — deterministic weights. Interface
  breakage outranks behaviour change outranks relocation; test paths are discounted.
- **Clustering** ([`context/cluster.py`](src/gita/context/cluster.py)) — groups entity changes
  under their enclosing top-level entity, ordered by score.
- **Depth-adaptive rollup** ([`context/rollup.py`](src/gita/context/rollup.py)) — `rollup_lines`
  collapses entity paths to N segments with a nested count; `fit_lines` picks the deepest view
  that fits a token budget, dropping lines only as a last resort.
- **L0/L1 assembly** ([`context/layers.py`](src/gita/context/layers.py)) — `build_view` returns a
  `ContextView` with an L0 headline, a budgeted L1 entity view, cluster list, chosen depth and a
  truncation flag. **L0 is built from facts alone**, so gita answers with the model switched off;
  WS-3 will upgrade that line with intent rather than enable it.
- **L2 on demand** ([`context/patch.py`](src/gita/context/patch.py)) — `entity_diff` returns a
  unified diff scoped to a single entity, so hunks are paid for only after L0/L1 identified what
  is worth reading.
- **Drill-down and query slicing** ([`context/navigate.py`](src/gita/context/navigate.py)) —
  `expand(changes, entity_id, budget)` returns the descendants of a rolled-up L1 line, and
  `query_view(changeset, question, budget)` narrows a view to the changes a question is about via
  deterministic term and intent matching. Query routing falls back to the full view when nothing
  matches: an empty answer to a badly-worded question is worse than an unfocused one.
- Measured on flask `fbb6f0bc4c`: raw diff 4,324 tokens → **L0 28 tokens (99.4% reduction)**,
  **L0+L1 528 tokens (87.8%)**, expand one cluster 60 tokens, L2 for one entity 216 tokens.

### Added — WS-1 · Core engine

- **Entity model** ([`entities/model.py`](src/gita/entities/model.py)) — `Entity`, `EntityKind`
  and `EntityTree`. Stable identities of the form `path::Parent::child`, with sibling
  disambiguation for colliding names. `EntityTree.enclosing(line)` resolves a line to its
  innermost entity, preferring leaves over containers.
- **Four-hash change detection** — `raw_hash` (exact bytes), `content_hash` (comments and
  whitespace normalised), `signature_hash` (body excluded), `body_hash` (signature excluded).
  Noise filtering and rename identity fall out of hash comparison, with no heuristics.
- **Language specs** ([`entities/languages.py`](src/gita/entities/languages.py)) — Python,
  JavaScript, TypeScript, TSX, Go, Rust.
- **Extractor** ([`entities/extractor.py`](src/gita/entities/extractor.py)) — tree-sitter parse to
  entity tree. Anonymous functions are named from their binding site (variable, object key,
  assignment target, or enclosing call). Each file gets a synthetic `<module>` entity owning
  top-level statements.
- **Differ** ([`diff/differ.py`](src/gita/diff/differ.py)) — three passes, most certain first:
  stable id, then unambiguous hash match, then Jaccard similarity over token shingles.
  Classifies `added`, `removed`, `renamed`, `moved`, `signature_changed`, `body_changed`,
  `cosmetic`, `unchanged`.
- **Cross-file move reconciliation** — `reconcile_moves()` runs after per-file diffing, so
  extracting a helper into a new module reports as `moved` rather than a delete plus an
  unrelated add.
- **ChangeSet** ([`diff/changes.py`](src/gita/diff/changes.py)) — the fact container, with
  `material()` (noise excluded) and `interface_changes()` (caller-visible) views.
- **Git layer** ([`vcs/git.py`](src/gita/vcs/git.py), [`revisions.py`](src/gita/revisions.py)) —
  `diff_revisions(repo, base, head)` produces a `ChangeSet` for two git revisions.
- Integration over real history from five corpus repositories.

### Added — Infrastructure

- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — unit tests on Python 3.11 and
  3.12, plus an integration job that clones the corpus, runs the real-history tests and executes
  the attribution benchmark.
- **Language extraction tests** ([`tests/test_languages.py`](tests/test_languages.py)) — Go, Rust
  and TSX were previously only exercised by corpus integration, which proves the engine does not
  crash but not that the right entities come out.
- 136 tests.

### Added — WS-8 · Evaluation

- **Spike A harness** ([`spikes/attribution/spike.py`](spikes/attribution/spike.py)) — measures
  hunk-to-symbol attribution accuracy and token compression across a multi-language corpus.
  Retained as the permanent regression benchmark.
- Results: **94.0% attribution coverage, 0.01% real miss rate, 0.0% parse errors, 96.1% token
  compression** across 66 commits and 28,595 changed lines.
  See [docs/SPIKE-A-RESULTS.md](docs/SPIKE-A-RESULTS.md).

### Added — Docs

- [docs/SCOPE.md](docs/SCOPE.md) — thesis, architecture, workstreams, metrics, open questions.

### Fixed

- `build_view` emitted L0 unconditionally, so any budget below its own cost was silently
  exceeded — the contract an agent relies on to size its context was not actually enforced.
  L0 is now trimmed to fit, and `view.tokens <= budget` holds for every budget including zero.
- Entity hashes covered the whole subtree, so a class reported a change whenever any of its
  methods did and the same edit was counted at every level of the tree. An entity now owns only
  the code no descendant entity has claimed. The module-entity fix below was a special case of
  this rule; it now applies uniformly.
- The synthetic `<module>` entity was excluded from diffing, so a commit that only touched
  imports or top-level statements produced an **empty ChangeSet**. The module entity now
  participates in change detection, and is still excluded from move and rename matching, since
  git already handles file renames.
- Go `type_declaration` was registered as an entity, but the name lives on the inner `type_spec`,
  so every Go type extracted as `<anonymous>`.
- Go function literals bound via `var x = func() {}` were unnamed; `_binding_name` did not know
  about `var_spec`, `const_spec`, `short_var_declaration` or `expression_list`.
- `content_hash` included an entity's own name, so a pure rename never matched by hash and fell
  through to fuzzy scoring at 0.6 confidence. Added `body_hash`; renames now match exactly.
- The JavaScript `function` node was registered as an entity. In the current tree-sitter grammar
  that is the *keyword leaf*, not a function, and it manufactured a phantom `<anonymous>` entity
  inside every function expression.
- `Repo._run` used `check=False`, so an invalid git flag exited 129 with empty stdout — which is
  indistinguishable from "no changes". Git failures now raise `GitError`; only `blob()` stays
  tolerant, since a missing blob is normal for added and deleted files.

### Known limitations

- Cross-file matching is hash-only. Fuzzy similarity across every file pair is quadratic, so a
  helper that is moved *and* edited in the same commit still reports as add plus delete.
- Ambiguous entities are deliberately left unmatched: when two entities share a content hash on
  both sides, no move is inferred. Parent-guided matching (resolving children within an
  already-matched parent) would close most of this and is not yet implemented.
- Entity ids embed a `#2` suffix for colliding sibling names, assigned in document order.
  Inserting a new sibling before an existing collision shifts those ids.
- Blast radius, call graphs and caller resolution are not implemented — that is WS-2.
- No layering, rollup or token budgeting yet — that is WS-4.
- No model integration — that is WS-3, blocked on GPU hardware.

### Added — WS-6 · Consumers

- **CLI** ([`cli/`](src/gita/cli/)) — `gita diff`, `show`, `ask`, `expand`, `savings`, `serve`,
  with `-C`, `--budget`, `--json` and colour that disappears when piped. Gita-inspired aliases:
  `darshan`, `shloka`, `prashna`, `vistaar`, `sarathi`.
- **MCP server** ([`mcp/`](src/gita/mcp/)) — `gita_diff`, `gita_expand`, `gita_show`, `gita_ask`,
  `gita_savings`. Tool logic lives in `tools.py`, free of the SDK, so the contract an agent
  depends on is testable without a transport. Every result carries a `next` field naming the
  entities an agent can act on, making progressive disclosure discoverable rather than
  documented. Supports MCP SDK 1.x and 2.x.
- **Agent skill** ([`skills/gita/SKILL.md`](skills/gita/SKILL.md)) — teaches an agent to reach for
  gita instead of `git diff`, with the four-step drill-down, how to read entity ids and change
  kinds, worked examples, and an explicit limits section.
- 186 tests.

**Resolved open question:** each layer is a separate MCP tool rather than a depth parameter on one
tool, so an agent sees the cost of each step in the name it chooses.

### Added - WS-8 · Telemetry

- **Event capture** ([`telemetry/events.py`](src/gita/telemetry/events.py)) - append-only JSONL,
  enabled by `GITA_TELEMETRY`. Sessions, tasks and arms are tagged via `GITA_SESSION`,
  `GITA_TASK` and `GITA_ARM`. Every failure is swallowed: telemetry must never break the thing it
  measures.
- **git shim** ([`telemetry/shim.py`](src/gita/telemetry/shim.py)) - a `git` stand-in that records
  what git returned and forwards stdout, stderr and exit code untouched. The baseline arm has to
  be measured the same way as the treatment arm, or the comparison is worthless.
- **Aggregation** ([`telemetry/aggregate.py`](src/gita/telemetry/aggregate.py)) - per-arm calls,
  tokens, averages per call and per session; paired per-task reduction; and **drill depth**, the
  share of sessions that never needed L2.
- CLI and MCP tools emit telemetry automatically, measuring exactly what they wrote.
- `python -m gita` and `python -m gita.cli` now work.
- 214 tests.

**Measurement caveat, recorded deliberately:** what these events capture is *tokens of tool output
injected into context*, not total model spend. ~~The Copilot session store has no token columns, so
true prompt cost is not recoverable locally.~~ **Corrected:** the session store has no token
columns, but the Copilot CLI logs do record real `prompt_tokens`, `completion_tokens` and cache
details per request. End-to-end session cost is therefore measurable, and the dashboard should
report both levels: per-call tool output (micro) and real session spend (macro).

### Added - Coverage and history

- **Working-tree and staged diffs** - `diff_revisions(repo, "HEAD", None)` compares against the
  working tree and `STAGED` against the index, matching `git diff` and `git diff --cached`.
  Previously the most common thing an agent looks at -- *what did I just change?* -- failed
  outright with a `GitError`.
- **Docs and config are no longer invisible** - Markdown (heading tree), YAML, JSON and TOML
  (key paths) are parsed into entities, and any other text file falls back to a single whole-file
  entity. gita previously saw one of three changed files and reported nothing for the rest, which
  reads as *unchanged* -- a silent wrong answer. Binary files are still skipped, deliberately.
- **Series-of-events view** ([`history.py`](src/gita/history.py)) - `series()` gives per-commit
  entity changes and `entity_history()` follows one entity across a range. A two-revision diff is
  cumulative and loses the sequence; this answers *when* behaviour actually changed. Exposed as
  `gita history` (alias `katha`).
- Root commits now diff against git's empty tree rather than failing on `<sha>^`.
- 238 tests.


### Changed - `ask` deferred, replaced by exact filters

`gita ask` and the `gita_ask` MCP tool have been withdrawn until WS-3.

They matched query words against entity ids, so `ask "what should I re-test?"` returned every
entity with "test" in its path. That answer *looked* right and omitted exactly the tests at risk:
the ones covering changed source that did not themselves change. Every other gita surface degrades
to blunt-but-correct; this one degraded to confidently wrong, which is worse.

Replaced by two exact flags on `gita diff`:

- `--interface-only` - only changes that can break a caller, computed from signature hashes.
  This answers "did the public API break?" without guessing.
- `--filter TERM` - only entities whose name or path matches. It is a filter, and now says so.

An unmatched filter returns nothing rather than silently widening. `gita history` covers "when did
this change". "What should I re-test?" is now explicitly unsupported pending WS-2 caller edges -
a stated gap beats a confident wrong list.

Also fixed: the JSON payload listed every change while `l1` honoured the filter, so the two
disagreed. Both now derive from the same focused ChangeSet.

## Unreleased - branch `feat/one-shot-context`

### Changed - answer in one call instead of making the agent drill

Iterations 1 and 2 both measured gita costing about **+1.25 turns** per task. A turn is
roughly **126,000 tokens** of re-sent context; gita's entire output is ~1,500. Output is
therefore about **100x cheaper than a turn**, and progressive disclosure -- which optimises
bytes per call -- was buying pennies while spending pounds.

- **`gita diff` now answers completely in one call** ([`context/answer.py`](src/gita/context/answer.py)):
  headline, changed files, changed entities, **and the actual hunks** for the highest-ranked
  entities, all within budget. The agent should not need a second command.
- **New invariant: gita output is never larger than the `git diff` it replaces.** The budget is
  capped by the raw diff size, so adopting gita cannot cost more than not adopting it. Tested.
- **`--patch`** emits an ordinary unified diff with formatting-only changes removed. Familiar
  format, no new syntax, no drilling -- the lowest-friction way to save an agent tokens.
- **`--brief`** restores the previous summary-only behaviour.
- **Default budget raised from 1,000 to 6,000 tokens.** Being stingy with output to avoid a
  follow-up call is a false economy at ~126k per turn.
- **Changed files are named in the headline.** Iteration 2 lost recall on a file-level question
  because an entity list alone cannot answer "which files changed".
- MCP `gita_diff` returns `answer` rather than `l0`/`l1`, and only returns `next` when the budget
  actually forced something out -- suggesting a follow-up that is not needed costs a turn.
- `AGENTS.md` and `SKILL.md` rewritten around a single canonical command, to remove the
  orientation turn the agent was spending on discovery.

### Fixed

- Per-entity patches repeated difflib's `--- a/x` and `+++ b/x` headers, which on small diffs cost
  more than the change itself and made gita's output *larger* than plain git.

## Unreleased - branch `feat/one-shot-context` (continued)

### Fixed - the three tasks that regressed in iteration 3

Each was diagnosed from run artifacts, not intuition.

- **`gita history` demanded a fully-qualified id.** An agent asked to trace `SaveUploadedFile`
  typed exactly that, got "no recorded changes", and fell back to `git log -L`. Entity names now
  resolve from bare input ([`context/resolve.py`](src/gita/context/resolve.py)), and ambiguity is
  **reported with the candidate ids rather than guessed** -- silently picking one would be the same
  confident-wrong failure that got `ask()` withdrawn.
- **Rust `impl` blocks were named after the type only**, so `impl Iterator for Walk` and
  `struct Walk` collided into `Walk` and `Walk#3`. The trait was invisible, which cost recall on a
  task whose ground truth included `Iterator`, and the `#N` suffixes discarded real information.
  Impl blocks are now named `Trait for Type`.
- **The evaluation harness rewrote whole files when applying setup.** `write_text(read_text() + x)`
  round-trips LF to CRLF on Windows, so git reported every line of `helpers.py` as changed and the
  agent was handed a huge confusing diff. Setup now works in bytes. This was a harness defect
  reported as a gita regression.

`gita show` and `gita expand` also accept bare names, but only as a **fallback** after the literal
argument fails: resolving first turned the valid container query `app.py::Store` into
`app.py::Store::get` and answered a question nobody asked.

- 295 tests.

### Fixed - iteration 4 findings

- **gita died on piped output on Windows.** The headline used a middle dot; a Windows shell pipe
  is cp1252, so `gita diff` raised `UnicodeEncodeError`. The agent retried with
  `PYTHONIOENCODING=utf-8` and that single task cost **+149%** against plain git. Output is now
  ASCII, and `render.write` falls back to a lossy encode rather than losing the answer. A tool
  that fails when its output is redirected is not production-ready.
- **`gita history` said *when* but not *what*.** Knowing a function changed in commit `d9307db`
  does not answer "what changed about it", so the agent went back to `git log` and `git show`.
  History now includes the hunks for each change, within budget, with `--brief` to opt out. This
  is the same incompleteness that made `gita diff` expensive before one-shot answers -- learned
  once, missed in a second place.
- **Agents did not know gita handles uncommitted work.** `AGENTS.md` and `SKILL.md` only showed
  `gita diff <base> <head>`, so on the working-tree task gita was never invoked at all. Both now
  show the bare form, and state that bare function names are accepted.

- 304 tests.

### Added - untracked files are part of "what did I change"

`git diff HEAD` cannot see untracked files, so a module an agent had just written was
invisible to `gita diff`. On the uncommitted-work task the agent called gita, then
`git status --short`, then `git diff` -- in every repetition -- because gita answered only
half the question. Working-tree diffs now include untracked files (respecting `.gitignore`),
with their entities extracted normally. Commit ranges are unaffected: history has no
working tree.

- 313 tests.

### Added - "is this wired in?" answered without being asked

On the uncommitted-work task the agent asked whether anything was "incomplete or unwired"
and answered it with `git status` plus `git diff -U15` -- reaching for surrounding context
because gita could not say whether a new function was referenced anywhere. gita became an
extra call instead of a replacement.

`gita diff` now reports additions whose name appears nowhere else:

```
src/flask/helpers.py::_eval_probe  [added]
unreferenced (name appears nowhere else): src/flask/helpers.py::_eval_probe
```

This is **name matching, not a call graph** ([`context/references.py`](src/gita/context/references.py)),
and is labelled as such wherever it appears. It answers "is this dead code", which is the
first question asked of an addition; it does not answer "what does this affect", which needs
real caller edges (WS-2). Only additions are checked -- a modified function already had
callers. Lookups are capped and names shorter than three characters are skipped.

- 323 tests.

### Fixed - iteration 8: bulk test churn buried the answer

`got-new-option` regressed from -38% to **+111%** against plain git, taking 7.7 turns
instead of 3.3. Four distinct faults, found in the run artifacts:

- **Test churn crowded out the answer.** The commit adds one option and 159 test cases whose
  names all restate it. Listing each one filled the summary with 20 near-identical, truncated
  titles and pushed the real API change off the top, so the agent went back to reading files
  by hand. Test files now roll up to one line each --
  `test/hooks.ts  (58 tests: 57 added, 1 body_changed)` -- but only when tests are bulk *and*
  something else changed, because a test-only commit is about its tests. The summary now names
  `allowAbsoluteUrls` outright, and the answer fell from 5,273 to **3,969 tokens** against
  16,713 for `git diff`.
- **Every added test was reported as unreferenced.** A test is invoked by its runner, never by
  name, so the dead-code check raised 159 false alarms. Worse, it can only afford 25 lookups,
  so all of them were spent on tests before reaching a single source change. Tests are now
  excluded and the remaining lookups go to the highest-ranked changes first.
- **Telemetry counted the answer twice.** `render.write` retries after a failed encode, and the
  measuring tee recorded the attempt *and* the retry. Every measurement of the one task carrying
  non-ASCII text was inflated 2x, in iterations 6, 7 and 8. The tee now records only what
  actually left the process. Reported credits came from the model's own logs and are unaffected.
- **Non-ASCII source was corrupted.** The cp1252 fallback turned an arrow inside a quoted
  documentation line into `?`, which is a fact reported wrong. `render.write` now asks the
  stream for UTF-8 first and only replaces characters as a last resort.

### Changed - one parse per revision, not one per entity

`entity_diff` accepts a shared cache, so a file with many changed entities is read and parsed
once per revision instead of once per entity. The largest task in the corpus went from
**11.2s to 6.2s**. A ten-second call is one an agent works around.

- 335 tests.

### Fixed - iteration 9: a version bump reported as no change at all

Iteration 9 restored the headline to **-18.1%** with turns **3.33 -> 2.67**, and
`got-new-option` went **+111% -> -30%**. But recall fell to 98.3% -- the first time gita
has scored below plain git -- and chasing that one point found a far worse bug.

- **TOML and YAML value changes were invisible.** tree-sitter represents a TOML string as
  two quote children with the value sitting in the gap between them, owning no node of its
  own. `_own_tokens` collected only leaf tokens, so it saw `"` and `"` and dropped the
  value. `flask = "3.0"` and `flask = "3.1"` hashed identically, were classified
  `cosmetic`, and were filtered out as noise -- **gita answered "no material changes" for a
  dependency version bump**. Tokens are now collected gap-aware, so text that no child
  claims still belongs to its parent. Code languages were unaffected.
- **The same edit across sibling files is one fact, not N.** Four `examples/*/pyproject.toml`
  files gaining the same isort block produced 25 mentions of `pyproject.toml` against 2 of
  `uv.lock`, drowning out half the answer. Changes sharing an entity and kind across three
  or more files now collapse to one line naming them; two files is not a pattern.

### Fixed - errors that tell an agent what to do next

An audit of every failure path found six that did not meet that bar:

- **`resolve` was structurally broken.** `git rev-parse nosuchref` echoes the argument on
  stdout while exiting non-zero, so `resolve` returned a truthy value and every guard built
  on it passed. The failure surfaced four calls later as raw plumbing. Now `--verify --quiet`.
- **git's diagnostics leaked verbatim**, including our own flags and three lines of advice
  about `--` -- the exact low-signal noise gita exists to remove. Now one line.
- **"unknown revision: HEAD"** was reported for a directory that is not a git repository.
- **`show` said "not found"** for an entity that exists and simply did not change.
- **`--budget 0` exited zero with no output**, indistinguishable from "nothing changed".
  The budget bounds the answer; a diagnostic is not an answer.
- **Usage was printed twice**, once by argparse on stderr and once by us on stdout.

- 360 tests.

