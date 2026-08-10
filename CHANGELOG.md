# Changelog

All notable changes to gita are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are grouped by workstream (WS-*) as defined in [docs/SCOPE.md](docs/SCOPE.md).

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
