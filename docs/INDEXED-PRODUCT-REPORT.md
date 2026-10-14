# Gita as a persistent entity store: keep, retire, and complete

**Date:** 2026-10-14

**Status:** Decision report; first transitional flag increment implemented.
**Implementation boundary:** Strict `GITA_ENABLE_LEGACY=1` now gates the old
product and ordinary history defaults to indexed reads. Legacy code still ships.
The recommendations below describe the target, not a completed S1/S2/S3 release.
Query parity, index qualification, independent verification and endpoint
comparison are not implemented; parse/match policy and storage are unchanged.

**Source baseline:** `public` at `8858639`. The published
[`DevPranjal/gita` snapshot](https://github.com/DevPranjal/gita/commit/3bfca49a749fe2346aedef2c3ea189e5aaadf246)
has the same product source and tests; its README and repository links differ.

## 1. Product decision

**Gita should be a local, persistent entity-history layer over Git.**
Tree-sitter extracts structural evidence during explicit indexing. Gita records
entity versions, occurrences, identity decisions, and transitions. People and
agents query that evidence instead of repeatedly reconstructing function history
from file patches.

"Git that tracks entities instead of lines" is the product shorthand, not a claim
that Gita replaces Git's object database or proves semantic identity. Git remains
the source of truth for commits and exact source. Names and paths locate entities;
they are not the entity's identity.

**Recommended invariant:** entity extraction and lineage matching happen during
index builds/updates only. Normal reads use a published index generation. A
missing, incompatible, or insufficient index produces an explicit refusal or
coverage result, never a fallback to on-demand extraction.

Keep the index as the product; retire the older structural-review engine rather
than leaving two identity systems behind an `--indexed` switch.

### A correction to the old-engine description

The current legacy engine does not primarily extract entities from textual patch
output. It reads before/after source blobs, extracts entity trees, matches them,
and then renders changes. The behavior to retire is **query-time structural
reconstruction and heuristic tracing**, regardless of the input representation.
See [`diff_revisions`](../src/gita/revisions.py#L31).

## 2. Features that should stay

All rows below describe existing capabilities to preserve unless marked otherwise.

| Capability | Recommended contract | Evidence |
| --- | --- | --- |
| Explicit index build | Index committed source with local Tree-sitter grammars; do not require an LLM or cloud service. | [`parse_evidence`](../src/gita/index/evidence.py#L413), [`build`](../src/gita/index/builder.py#L302) |
| Incremental update and no-op | Resume from the persisted first-parent frontier; reuse cached evidence and return the existing generation when the tip is unchanged. | [`build`](../src/gita/index/builder.py#L302) |
| Named scopes and bounded history | Preserve explicit anchor, tip, and root/bounded/shallow/missing-parent boundaries. A current-tree anchor must not imply earlier history was indexed. | [`_walk`](../src/gita/index/builder.py#L204) |
| Persistent entity identity | Continue an EID only under the accepted evidence policy. Names, locations, and content versions remain distinct concepts. IDs are interpretation-context-qualified, not universal across unrelated rebuild policies or repositories. | [record contracts](../src/gita/index/model.py), [matching policy](../src/gita/index/matching.py#L155) |
| Supported edits, renames, and moves | Preserve accepted continuity and the evidence behind it; do not promise continuity through arbitrary refactors. | [builder matching](../src/gita/index/builder.py#L810), [`classify_transition`](../src/gita/index/matching.py#L62) |
| Copy and ambiguity safeguards | Copies do not inherit an identity merely because their content matches. Ambiguous/candidate/rejected links remain distinct from accepted continuity. | [matching manifest](../src/gita/index/matching.py#L155), [exact-match guards](../src/gita/index/builder.py#L810) |
| Name, locator, and EID resolution | Resolve at a revision, optionally including historical names; return ambiguity instead of selecting a plausible match. | [`resolve`](../src/gita/index/query.py#L542) |
| Entity history | Return the selected identity's recorded events, source/destination occurrences, flags, and accepted decision evidence. | [`history`](../src/gita/index/query.py#L699), [`_event_json`](../src/gita/index/query.py#L678) |
| Range event history | Return transitions in `(base, head]`, including an edit and its revert as separate events. Call this events/history, not a net diff. | [`events_in`](../src/gita/index/query.py#L722) |
| Historical state | Distinguish present, removed, absent, and unknown. An observed anchor is not proof of an entity's birth. | [`_state` / `state_at`](../src/gita/index/query.py#L305) |
| Exact historical source | Validate occurrence references and retrieve exact Git byte spans, checking the stored source digest. The index is not a self-contained source archive. | [`source`](../src/gita/index/query.py#L346) |
| File entity listings | Enumerate recorded occurrences in a file at a revision without reparsing that file. Preserve unknown versus absent. | [`entities_in`](../src/gita/index/query.py#L631) |
| Event patches | Keep optional patches derived from the recorded before/after occurrence bytes. They explain an indexed transition; they do not discover identity. | [`_event_patch`](../src/gita/cli/indexed.py#L337) |
| Immutable generations and pinned reads | Keep a stable publication while new generations are built; bind cursors to the generation, scope, query, revision, and order. | [`IndexSession`](../src/gita/index/query.py#L67), [cursor handling](../src/gita/index/query.py#L394) |
| Coverage and provenance | Preserve extraction gaps, scope boundaries, policy fingerprints, freshness, and explicit unknown results. Never equate unsupported with unchanged. | [query coverage](../src/gita/index/query.py#L489), [CLI envelope](../src/gita/cli/indexed.py#L61) |
| Transactional publication and safe retention | Publish only validated checkpoints; retain previous evidence; require explicit rebuild/collection and refuse in-use generation collection. | [`publish_generation`](../src/gita/index/store.py#L1343), [`collect_generation`](../src/gita/index/store.py#L1469) |
| Bounded parser execution | Preserve isolated reusable workers, deadlines, input/representation limits, and build failure on execution errors. | [`EvidenceWorker`](../src/gita/index/worker.py#L60), [`BuildLimits`](../src/gita/index/builder.py#L37) |
| CLI, Python API, and MCP | Keep these as adapters over one indexed query service, not separate engines with different semantics. | [indexed CLI](../src/gita/cli/indexed.py), [MCP indexed adapters](../src/gita/mcp/tools.py#L211) |

### Keep budgets, but change their role

Pagination, limits, and explicit omissions belong in an entity store. They keep
large queries usable; they are not a reason to retain a token-saving diff engine.
Do not silently truncate exact source or discard coverage to fit an answer.

The current budget counter uses `cl100k_base` with an estimate fallback. That is
not Gemma's tokenizer and is not a measurement of a local model's actual spend.
Preserve bounded responses, disclose the accounting method, and measure model
usage separately in experiments. See [token accounting](../src/gita/context/tokens.py).

## 3. Features to say goodbye to

| Retire | Why | Replacement / consequence |
| --- | --- | --- |
| No-index structural `diff` engine | Reconstructs entities and pairings for each comparison; creates a second truth alongside persistent lineage. | Indexed events now; a genuine indexed endpoint comparison only when implemented. |
| Legacy name/path-anchored history | Walks commits and reruns entity comparisons rather than following persisted identity. | Indexed identity history becomes the only history implementation. |
| `diff -> expand -> show` discovery ladder | Designed around summarizing and drilling into a freshly derived ChangeSet. | Resolve/list -> history/events -> state/source. Retain the questions, not the old implementation. |
| Similarity accepted as identity in the old differ | Conflicts with the conservative indexed policy. | Preserve indexed candidate evidence, but never promote similarity alone into continuity. |
| Live working-tree, staged, and untracked entity analysis | These are not members of a committed index generation. Keeping them would retain an alternate parsing path. | Explicitly out of scope in the first indexed-only release; ordinary Git can inspect pending edits. |
| `diff --indexed` compatibility adapter | Repackages range events as legacy changes; it is not a true net endpoint comparison. | Expose range events honestly. Remove the misleading route rather than changing its meaning silently. |
| Headline/rollup/clustering and relevance scoring | Exists to prioritize compressed review output, not retrieve durable entity facts. | Structured pages, precise selectors, optional event patches. |
| `--intent` and old `--interface-only` promises | Ranking hints and potential-impact heuristics are not indexed identity contracts. | Explicit event flags such as signature/body changes; do not imply caller breakage. |
| Noise-filtered/material-only review presentation as the default truth | A persistent store should not hide cosmetic, descendant, observed, or lineage-gap events. | Keep classifications and let clients explicitly filter without erasing provenance. |
| Legacy `--patch` mode and raw-diff-based token savings narrative | Coupled to the on-demand review product and its output comparisons. | Optional exact-source event patches; evaluate correctness, reuse, and query/build costs. |
| Legacy public Python exports | The package root currently advertises `diff_files`, `diff_trees`, `diff_revisions`, and legacy entity records. | Define a small indexed public API and document the breaking change. |
| Novelty aliases and implicit engine selection | Increase the surface without strengthening the entity-store model. | One conventional command vocabulary; indexed behavior is the default and only behavior. |
| Existing web compare/patch routes | The viewer currently mixes indexed entity browsing with non-indexed diff/show calls. | Keep an entity browser only after removing those routes or replacing them with indexed operations. |
| Old evaluation/dashboard product integration | The paired study targets diff/show/expand, not indexed history or reuse. Its dashboard cannot qualify the new product. | Archive historical evidence separately; keep/rework research tooling outside the runtime API. |

Evidence: [legacy history](../src/gita/history.py#L85),
[legacy CLI dispatch](../src/gita/cli/__init__.py#L362),
[legacy MCP tools](../src/gita/mcp/tools.py#L96),
[range-event compatibility adapter](../src/gita/index/compare.py#L22),
[package exports](../src/gita/__init__.py),
[mixed web routes](../src/gita/web/__main__.py#L92),
[paired evaluation tool set](../src/gita/eval/paired/evidence.py#L17),
and [evaluation dashboard imports](../src/gita/dashboard.py#L16).

Do not claim the historical AACR savings validate the indexed-only product.
The historical experiment can remain cited as a different product configuration,
not as a current-release headline.

## 4. What "diff" should mean in the indexed product

**Keep entity change inspection; retire diff-time entity discovery.**
Neither source patches nor Tree-sitter should disappear from the product.
Their responsibilities change:

| Operation | Recommended behavior | Current status |
| --- | --- | --- |
| Entity event patch | Read a recorded event's before/after source spans and render their textual difference. No extraction or rematching. | Implemented in indexed history. |
| Range events | List recorded changes throughout `(A, B]`; a revert remains visible. | Implemented. |
| Entity endpoint comparison | Resolve one EID's state at A and B and compare exact recorded occurrences. An edit followed by a full revert yields equal endpoint source. | State/source primitives exist; dedicated public comparison contract still needed. |
| Repository endpoint comparison | Compare endpoint entity populations by persisted EID and report net additions/removals/state changes, including partial coverage. | Not a shipped general query; requires implementation and tests. |

The simplest indexed-only release can ship **history/events/state/source without
a `diff` command**. If `diff` is retained, first implement a genuine endpoint
query. Do not route it through the existing event-to-ChangeSet adapter.

Line numbers and unified patches can remain presentation and source-location
tools. "Entities instead of lines" means the unit of identity and history changes;
it does not mean deleting exact source positions or refusing to display lines.

## 5. Proposed public interface

This is a **proposed vocabulary**, not a list of commands available today.
Reuse existing query primitives; do not add a second service layer per transport.

| Interface | Purpose | Work required |
| --- | --- | --- |
| `gita index` / `index --status` | Build/update or inspect a named committed scope. | Keep; simplify options and help around the sole engine. |
| `gita id <name-or-locator-or-EID>` | Resolve identity at a revision. | Keep; preserve historical-name and ambiguity behavior. |
| `gita history <EID>` | Timeline for one identity, optionally with event patches. | Make indexed history unconditional; remove the legacy path and `--indexed`. |
| `gita events A B` | Recorded transitions across a range. | New CLI/MCP name over existing `events_in`; make range semantics explicit. |
| `gita state <EID> --at REV` | Historical presence/location/occurrence. | Existing Python/MCP functionality needs a CLI surface. |
| `gita source <occurrence-reference>` | Exact source for a generation-qualified occurrence. | Define CLI reference input; keep validation, encoding, and no-truncation behavior. |
| `gita entities <path> --at REV` | Recorded file contents as entity occurrences. | Existing Python/MCP functionality needs a CLI surface. |
| Explicit generation collection | Retention management without altering Git. | Keep refusal of published/predecessor generations; do not promise automatic storage reclamation. |

MCP should expose equivalent indexed operations. Drop `gita_expand` and the old
`gita_diff`/`gita_show` implementations; add an indexed comparison tool only after
its contract exists. A familiar verb may be reused, but never with hidden legacy
fallbacks.

**Secondary UI recommendation:** keep a small local browser for entity catalogs,
identity timelines, source, and coverage. Do not make a PR-review simulator,
embedded CLI playground, or GitHub clone part of the core product requirement.

## 6. Code boundaries: remove carefully, not by folder name

| Area | Recommendation | Dependency / risk |
| --- | --- | --- |
| `index/model`, `schema`, `store`, `builder`, `matching`, `query`, `worker` | Preserve the foundation. | Main durable records, policy, publication, and read contracts. |
| `index/evidence` | Preserve and decouple from legacy extraction records. | It calls private helpers in `entities/extractor` and uses `entities/languages`. |
| `entities/languages` and AST classification helpers | Preserve or move to a neutral parser adapter module. | Deleting the old extractor wholesale would break index builds. |
| `entities/model`, `extractor`, `store`, `sql` | Retire legacy tree/hash/cache/SQL behavior only after shared helper dependencies are removed. | The extractor imports legacy records and SQL; preserve the index's supported adapters without accidentally advertising SQL support. |
| `diff`, `revisions`, legacy `history` | Remove legacy public engine after callers move. | Rewrite CLI/MCP/web/package exports and dependent tests first. |
| `index/compare` | Retire the compatibility adapter. | It converts indexed events back into legacy entity records and ChangeSets. |
| `context` | Retire review composition; extract only genuinely shared utility behavior. | Indexed CLI currently imports `count_tokens` via `context` and `_absolute` from `context.patch`; package imports also eagerly load old modules. |
| `vcs/git` | Keep committed-object access and repository-location helpers; trim only proven legacy-only methods. | `CommittedRepo` powers builds and exact source; `Repo` still locates shared storage. |
| `cli`, `mcp` | Rewrite as thin indexed adapters. | Indexed MCP calls currently route through CLI parsing/dispatch; consolidate response handling without making MCP depend on legacy CLI behavior. |
| `web` | Keep only if converted fully to indexed reads. | `DemoView.compare` and `patch` currently use the legacy engine even though catalogs/history use the index. |
| `eval`, dashboard, telemetry | Separate research support from product facts. | Retire old drill-depth/savings instrumentation; retain only useful opt-in build/query diagnostics if maintained. |
| Package metadata, docs, CI, verification | Update alongside the breaking API change. | Remove misleading commands/results, preserve grammar setup and core foundation coverage. |

Sources: [evidence imports and fingerprinting](../src/gita/index/evidence.py#L22),
[AST helper use](../src/gita/index/evidence.py#L256),
[legacy extractor imports](../src/gita/entities/extractor.py#L10),
[indexed presentation dependencies](../src/gita/cli/indexed.py#L15),
[eager context imports](../src/gita/context/__init__.py),
[MCP-to-CLI bridge](../src/gita/mcp/tools.py#L192), and
[legacy telemetry layers](../src/gita/telemetry/events.py#L32).

### Policy fingerprints make this a storage migration concern

The evidence fingerprint hashes adapter/extractor/language artifacts, and the
matching fingerprint includes builder/matching code. Moving shared helpers or
changing these modules can invalidate existing interpretations even if the
intended behavior is unchanged. The builder correctly requires an explicit rebuild.

Preserve old indexes/generations and explain the rebuild requirement. Do not
delete databases or promise EIDs survive a changed interpretation. See
[artifact fingerprints](../src/gita/index/evidence.py#L120) and
[build compatibility checks](../src/gita/index/builder.py#L302).

## 7. Important gaps, not features to pretend already exist

1. **Independent verification:** publication checks and SQLite integrity checks
   exist, but independent F4 source/policy/inventory verification does not.
   Preserve the honest refusal; completing verification is a high-value follow-up.
2. **True indexed endpoint comparison:** define and implement separately from events.
3. **Uniform API access:** state/source/entities exist in Python/MCP but need CLI
   parity; a range-events primitive exists without a dedicated `events` command.
4. **Identity stability documentation:** explain scope/context, observed versus
   proven birth, copy ambiguity, and rebuild consequences.
5. **Indexed-product evaluation:** measure lineage correctness, cold build cost,
   incremental updates, warm query cost, storage, and history-dependent agent
   outcomes. Old diff-compression results answer a different question.

Evidence: [verification refusal](../src/gita/cli/indexed.py#L503),
[publication limits](../src/gita/index/store.py#L1343),
[SQLite integrity checks](../src/gita/index/store.py#L1535),
[MCP state/entities/source](../src/gita/mcp/tools.py#L312), and
[existing range events](../src/gita/index/query.py#L722).

## 8. Explicit non-goals for the first indexed-only release

- Replacing Git commits, branches, merges, staging, or its object database.
- Tracking dirty/staged/untracked entities through a second live parse engine.
- Universal identity across repositories, arbitrary rewrites, or policy changes.
- Complete ancestry/merge-parent lineage; current indexing is first-parent.
- Call graphs, dependency/impact analysis, semantic search, or embeddings.
- Semantic-equivalence proofs, automatic bug localization, test selection,
  autonomous fixes, or merge-safety verdicts.
- A built-in model or automatic language-grammar downloads during reads/builds.

These are boundaries, not declarations that none could ever be built. Avoid
adding them merely to broaden the paper or preserve old marketing.

## 9. Migration acceptance gates

This is a breaking product simplification, not a file cleanup.

1. **One engine:** retained CLI/MCP/Python/web routes use persisted indexed
   identity; no invocation of legacy `diff_revisions`, `diff_trees`, or legacy
   `entity_history`.
2. **No hidden parsing on reads:** tests forbid parser/worker/build invocation
   during resolve, history, events, state, source, and file entity queries.
   Ref resolution and exact Git source access remain allowed where needed.
3. **No silent fallback:** missing indexes, stale policy, out-of-scope revisions,
   unknown coverage, and unavailable source produce explicit results.
4. **Foundation regression coverage stays:** preserve and adapt lineage, copy,
   gap, byte-span, transactional, worker, storage, incremental, and cursor tests.
   Tests of retired products can be removed; tests of shared parser rules cannot
   be deleted just because they live under legacy filenames.
5. **Correct comparison semantics:** events retain edit/revert transitions;
   any new endpoint comparison returns no net source change for a full revert.
6. **No destructive migration:** read old generations or refuse with rebuild
   guidance; do not unlink active database/WAL/SHM files.
7. **Consistent public story:** examples, package exports, tool schemas, viewer,
   benchmark adapters, and release notes describe the same indexed-only product.

Relevant existing tests include
[no implicit build](../tests/test_index_query.py#L155),
[read-only query behavior](../tests/test_index_query.py#L650),
[history across refactors](../tests/test_index_query.py#L457),
[exact committed bytes](../tests/test_index_query.py#L554),
[generation-pinned continuation](../tests/test_index_query.py#L816),
and [incremental builder tests](../tests/test_index_builder.py#L3466).
They were inspected as evidence; this report did not execute a new test run.

## 10. Recommended release boundary

**Keep now:** indexing, identities, history/events, state, exact source, file
entity listings, event patches, honest coverage, pinned generations, and explicit
storage lifecycle through CLI/Python/MCP.

**Retire now:** the on-demand structural-review engine, legacy tracing, compressed
diff ladders, mixed-engine defaults, compatibility range-as-diff rendering, and
their product-facing claims.

**Complete next:** CLI parity and a clean indexed API; independent verification;
then genuine indexed endpoint comparison if needed. Retain a UI only when it
obeys the same indexed contract.

The resulting product is not "shorter diffs." It is **a durable, queryable record
of supported entities and their evolution, grounded in Git and Tree-sitter**.

### Confidence and analysis boundary

**High confidence** in the current-path inventory: source was traced through
index build/evidence/matching/publication, indexed queries, CLI/MCP adapters,
legacy reconstruction, and the mixed viewer. **Recommendations are design
choices**, not existing guarantees. This is not a full dead-code inventory,
performance qualification, or security audit. Exact deletion lists and the
complete test/import closure must be established during implementation.
