# Changelog

All notable changes to gita are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are grouped by workstream (WS-*) as defined in [docs/SCOPE.md](docs/SCOPE.md).

## [Unreleased]

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
- 63 tests, including integration over real history from five corpus repositories.

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
