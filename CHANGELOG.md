# Changelog

## Unreleased

- Record pinned public Flask, Express and ripgrep indexing observations, retaining
  failures and partial coverage. Add reproducible public-corpus tooling and
  independently member-bound source rechecks; do not claim full-history scale.
- Specify indexed AACR/ReviewBench evaluation, harness audit, leakage/scoring
  safeguards, accounting and proposed success gates; no new model review run.
- Materialize and verify native grammars before CI measurements, and use an
  absolute runner-temp path for qualification artifact upload.

- Add manifest-driven offline index qualification with authored Git histories,
  independent source/query correctness gates, isolated build/update/no-op/query
  scenarios, failure receipts, resource sampling and baseline comparisons.
- Bound repeated candidate-version decoding with a transaction-local token cache.
  Identity rules and storage schema are unchanged, but the builder fingerprint
  changes: existing indexed scopes require an explicit rebuild.
- Add serial/parallel/incremental equivalence, publication recovery, reader/writer
  and adversarial oracle tests, plus dedicated Windows/Linux qualification CI.
  These gates do not implement product F4 verification or qualify arbitrary sizes.

- Begin legacy retirement with strict `GITA_ENABLE_LEGACY=1` opt-in. Unset/`0`
  disables the structural-review product; other values refuse clearly.
- Ordinary CLI/MCP history now reads the published index, including scope/order
  options. `--indexed` is a compatibility no-op; `--legacy` / `legacy=true`
  explicitly selects old reconstructing history after opt-in.
- Gate legacy diff/show/expand and aliases, direct reconstructing Python APIs,
  and the old `diff --indexed` event adapter. Disabled operations return migration
  guidance, never substitute events for an endpoint diff.
- Hide legacy MCP tools by default; keep indexed imports independent of the
  review pipeline. Preserve version access and shared Tree-sitter helpers.
- Default the browser to indexed entities/history/source; legacy compare/patch
  and CLI-playground routes refuse without opt-in. Legacy-dependent evaluation
  runners refuse before side effects unless explicitly opted in.
- This increment does not remove shipped legacy modules, alter parse/match
  policy fingerprints or storage, qualify the index, implement query parity,
  independent verification, or genuine indexed endpoint comparison.

- Prepare the `public` branch for version 1.0.0: remove generated evaluation
  reports, hackathon presentation and personal deployment scripts. Retain the
  evaluation harness, frozen input, protocol and one historical summary.
- Keep local creative work and generated experiments ignored; include evaluation
  documentation and the manifest in source distributions. Enable CI for `public`
  across the advertised Python versions and provision C#/C++ grammars.

- Normal and brief output count omitted entities using summary membership and
  existing hunk coverage, not formatted row counts. Collapsed summaries no longer
  trigger false warnings or evict already displayed code.
- Patch output distinguishes complete, partial and nothing-fits results, reporting
  omitted entities and effective/requested budgets. Plain omissions warn on stderr;
  nothing-fits exits 6. Empty rendered text no longer implies no material changes.
- One-for-one comment edits between uniquely matched neighboring entities render
  as atomic `changed comment` hunks. Ambiguous or relocated comments retain
  separate additions/removals; comment ownership and noise filtering are unchanged.
- Guarded rename-with-edit matching recognizes substantial callable edits while
  rejecting ambiguous candidates.
- Derived diffs match nested entities within corresponding parents, so inserting
  an earlier anonymous callback no longer invents descendant edits or moves.
- Structural patches compare CRLF and LF as the same line ending while parsing
  the original source bytes.
- Indexed entity and range history now include exact-source inline patches by
  default in CLI text, JSON and MCP responses. `--brief` skips source reads;
  per-event patch status makes budget and source omissions explicit while
  preserving references. Observed anchors are not rendered as proven additions.
- `history` accepts either one entity or two commits, including indexed range
  events with bounded JSON output and generation-pinned pagination. Existing
  entity/revision forms remain supported.
- MCP now exposes history, identity, explicit index management, state, exact
  source and file-entity queries, plus the existing diff/expand options.
  Read-only tools never build an index; source is never silently truncated.
- C# and C++ adapters for structural diffs and indexed history, including
  scoped namespaces, templates, overloads and native member names. C# accessor
  implementations and C++ template signatures retain distinct change evidence.
  Provision `csharp` and `cpp` grammars explicitly and rebuild existing indexed
  scopes for the updated parse policy.
- Entity summary lines carry the line range they occupy, e.g.
  `shop.py::farewell  [signature_changed]  L5-6`, so a named entity can be
  opened without searching for it. An entity that exists only before the change
  is marked `(before)`, since a bare range would point at whatever occupies
  those lines now.
- Hunk headers are now absolute file positions. `entity_diff` slices a file down
  to one entity before diffing, so difflib numbered hunks from 1 within the
  entity: one at line 18 reported `@@ -0,0 +1,2 @@` and sent a reader to the top
  of the file. Empty sides (`-0,0`) are left alone, because they are not a
  position.

## Team Foundation Preview - 2026-09-15

- F0: immutable evidence/lineage contracts and canonical identifiers.
- F1: byte-accurate parsing, isolated workers, offline committed Git access,
  schema-v2 storage, generation publication, snapshots and protected GC.
- F2: strict first-parent lineage construction, full-population copy/ambiguity
  guards, exact historical state, events, departures and coverage.
- F3: incremental prefix reuse, pinned read-only queries/cursors, historical
  names, exact source retrieval and indexed CLI with bounded JSON responses.
- Five-event CLI acceptance now passes through birth, edits, rename and move.
- Retained existing structural diff and stdio MCP diff tools.
- Clean team snapshot excludes research history, scratch work, benchmark runs,
  videos, third-party corpus checkouts, databases, caches and credentials.

F4 verifier, independent real-world accuracy/performance evaluation and release
qualification are not complete. Schema-v1 caches are preserved but unsupported;
there is no automatic physical-schema migration.