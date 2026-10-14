# S1: Safe retirement of the legacy structural-review product

**Status:** Draft specification. **Priority:** Core release blocker.
**Increment implemented:** Transitional strict `GITA_ENABLE_LEGACY=1` gates,
default indexed history and lazy review imports only. Legacy modules still ship.
S1 retirement, packaging/migration qualification and all release gates are not
complete; shared parser/matcher policy source and storage remain unchanged.

**Audience:** Product owner, maintainers, API/client developers.

**Companions:** [S2: Index qualification](02-index-qualification.md),
[S3: Product contracts](03-indexed-product.md), [delivery sequence](README.md).

## 1. Purpose and success

Replace two competing entity engines with one persistent indexed product.
Preserve supported index semantics and user data while deliberately removing
query-time entity reconstruction. "Without breaking anything" means **no
unintended indexed regressions, silent semantic substitutions, or data loss**.
It cannot mean keeping every old API working after choosing to retire it.

### Users and outcomes

| User | Required outcome |
| --- | --- |
| Developer with an existing index | Understand whether it is compatible, what rebuild is needed, and what happens to IDs/cursors. |
| Agent/client author | Discover one indexed tool vocabulary and receive typed errors instead of a legacy fallback. |
| Maintainer | Delete the old engine without deleting shared AST classification or weakening policy. |
| Researcher | Identify the exact product version/tool configuration behind old and new results. |

**Release success:** all retained paths use indexed evidence; accepted lineage
behavior passes the fixture suite; existing incompatible storage is untouched;
old commands either give migration diagnostics or are absent; installed packages
work without importing or shipping the retired engine.

## 2. Current implementation and coupling

| Current path | Behavior and migration implication |
| --- | --- |
| [`revisions.diff_revisions`](../../../src/gita/revisions.py#L31) | Reads before/after blobs, extracts cached entity trees, matches, and reconciles moves. Retire this pipeline, not just its help text. |
| [`history.series` / `entity_history`](../../../src/gita/history.py#L85) | Reconstruct per-commit entity changes. Replace with persisted history/events. |
| [`cli._dispatch` / `_cmd_diff`](../../../src/gita/cli/__init__.py#L362) | Chooses indexed or derived paths; requires an indexed-only rewrite. |
| [`index.compare`](../../../src/gita/index/compare.py#L22) | Converts range events to legacy ChangeSets. Remove; it does not implement net endpoints. |
| [`index.evidence`](../../../src/gita/index/evidence.py#L22) | Depends on legacy extractor AST helpers and language specifications. Extract shared rules before deleting legacy modules. |
| [`cli.indexed`](../../../src/gita/cli/indexed.py#L15) | Imports token counting through the eager context package and patch line-offset helper from legacy patch code. Decouple. |
| [`mcp.tools._command_result`](../../../src/gita/mcp/tools.py#L192) | Calls CLI parser/dispatch for indexed operations. Replace with a transport-neutral indexed service. |
| [`DemoView.compare` / `patch`](../../../src/gita/web/__main__.py#L92) | Legacy diff/show inside an otherwise indexed viewer. Remove/rewrite or omit viewer from candidate. |
| [Package root](../../../src/gita/__init__.py) | Eager legacy exports mean even importing an indexed submodule traverses old code. Remove legacy root imports. |

Parser/matcher fingerprints include source artifacts. A source-only refactor can
make existing generations unreadable under the new installation. Query opening
also checks the installed policy: preserving an old generation is not a promise
the new binary can read it. See
[`_artifacts`](../../../src/gita/index/evidence.py#L120),
[`build`](../../../src/gita/index/builder.py#L302), and
[`IndexSession._check_policy`](../../../src/gita/index/query.py#L115).

## 3. Scope and non-goals

**In scope:** imports, APIs, CLI/MCP schemas, old engine callers, shared parser
rules, tests, packaging, documentation, demo routes, research/runtime separation,
index compatibility diagnostics, upgrade/rollback rehearsal.

**Not in scope:** Git-history rewriting, automatic schema conversion, universal
identity migration, worktree indexing, new matching heuristics, semantic search,
new agent/model, endpoint comparison implementation, or performance shortcuts.

## 4. Requirements

| ID | Requirement | Acceptance |
| --- | --- | --- |
| S1-R01 | Normal indexed queries MUST not invoke extraction, workers, build, legacy reconstruction, or matching. | Runtime spies across CLI/Python/MCP and retained UI; explicit lifecycle builds and separately requested diagnostic verification excluded. |
| S1-R02 | Shared AST naming/kind/body/namespace/language rules MUST be preserved before old modules are removed. | Rule-specific fixtures compare definitions, source bounds, representations, and explicit failure outcomes. |
| S1-R03 | Retired behaviors MUST not silently acquire new semantics. | Old diff/show/expand invocations fail with migration guidance; no event-as-diff success result. |
| S1-R04 | Missing/incompatible storage MUST not trigger build, delete, repair, fetch, or fallback. | Metadata unchanged and no created DB for read attempts. File-access time changes are not treated as corruption. |
| S1-R05 | Changes to interpretation MUST be surfaced. | Parse/match incompatibility code, scope, and actionable rebuild guidance; no assertion of ID continuity across policy changes. |
| S1-R06 | Build/publication MUST retain transactionality and protected collection. | Injected failures leave previous publication usable under its compatible environment. |
| S1-R07 | Installed wheel/sdist MUST expose only supported product contracts. | Fresh install, import-boundary checks, CLI/MCP contract suite, and archive inspection. |
| S1-R08 | Tests MUST distinguish retired behavior from shared invariants. | Every deleted test classified; lineage/span/coverage safeguards retained or replaced. |
| S1-R09 | Research tooling MUST not make runtime imports depend on retired tools. | Core imports work without evaluation extras; legacy study can remain only as an explicitly separate historical asset. |
| S1-R10 | Public docs MUST describe the same release as discovery/help schemas. | All examples exercised or syntax-tested; no stale default-engine or token-savings claims. |
| S1-R11 | Migration MUST not modify user source, refs, staging, or creative folders. | Fixture worktree/index/refs manifests identical before/after index operations. |
| S1-R12 | Operational changes MUST not loosen accepted identity rules. | Any intentional matching-rule change goes through a separate policy change, fixture update and qualification review. |

## 5. Target boundaries and dependency rules

Proposed logical architecture, not a requirement to create a file per box:

```text
CLI / MCP / optional browser
          |
indexed application operations and response contracts
          |
query + index lifecycle + event-source presentation
          |
model / store / build / matching / evidence / worker
          |
shared Tree-sitter rules + committed Git object access
```

- Query operations MAY resolve a requested Git revision and read exact source;
  they MUST NOT traverse ancestry to manufacture missing query results.
- The build side owns extraction and matching. It can inventory Git trees and
  compare persisted evidence; banning all Git change APIs would be incorrect.
- API adapters MUST NOT call other adapters' argument parsers.
- Shared utilities MUST not import a legacy package initializer.
- Research adapters MAY call the public indexed API, not private CLI dispatch.
- Keep the version constant importable without importing all runtime engines.

### Shared-code extraction inventory

Before deletion, record the complete call/import closure and disposition:

| Existing area | Disposition |
| --- | --- |
| Language specs and extractor helpers used by evidence | Move/reuse in a neutral parser-rules module; preserve rule behavior and AST vocabulary. |
| Legacy Entity/EntityTree/hash caches | Remove only after all retained runtime references disappear. |
| Token accounting | Move to a neutral bounded-response utility; disclose method; preserve budget/error tests. |
| Absolute-line patch adjustment | Move only needed presentation logic; keep source span/newline tests. |
| CommittedRepo and shared Git location helpers | Keep; trim legacy-only functions by actual dependency evidence, not filename. |
| Event metadata/source rendering | Keep indexed records; do not recreate legacy ChangeSet records. |
| Telemetry | Retain minimal opt-in diagnostics if useful; remove obsolete L1/L2 ladder/savings product dependencies. |

## 6. Compatibility policy

### Command/API migration

| Old surface | Target action |
| --- | --- |
| `diff`, `show`, `expand`, old aliases | Breaking-release removal. A tombstone MAY print an explicit replacement workflow and exit 2; it performs no query. |
| `history NAME --indexed` | `history NAME` is indexed-only. Accept `--indexed` as a temporary no-op flag for one breaking release, with text warning to stderr; never switch engine. |
| `history A B --indexed` | Temporary alias to explicit range events only when exactly two revisions and no entity selector are supplied; document deprecation. Remove ambiguous positional inference. |
| `diff --indexed` | No compatibility alias to events. Refuse and explain endpoint versus event semantics. |
| Legacy Python imports | Remove from documented exports and provide migration notes; do not keep fake functions that return empty answers. |
| MCP diff/show/expand schemas | Remove old implementations. Clients restart discovery; no dual engine under the same names. |

Deprecated successful aliases MUST identify canonical operation and API version
in structured responses, not merely emit invisible server logs. Removed commands
MUST return `retired_command` in JSON mode with `error`, recovery, and exit 2.
These are proposed adapter diagnostics, not new storage statuses.

### Store compatibility matrix

| Existing state | New binary behavior |
| --- | --- |
| No store | Reads refuse; explicit build creates it. |
| Compatible schema and policies | Read/build normally; old cursors remain bound to their original compatible generations. |
| Compatible schema, changed parse/match policy | Refuse old-policy queries; explicit rebuild may publish a new context while retaining old generations. Old IDs/cursors are not redirected. |
| Incompatible schema | Refuse without mutation. No automatic destructive migration. |
| Diverged/shortened history or changed boundary | Explicit rebuild required; do not splice unrelated lineages. |
| Corrupt store | Preserve for diagnosis; no success-shaped rebuild fallback. |

For an incompatible schema, export/backup under an exclusive maintenance window
using SQLite-aware mechanisms, then use a separate store/location **only through
an explicit supported migration design**. Do not invent a manual rename of live
database/WAL/SHM files. Supporting that migration is a separate gate; refusal is
acceptable for this release.

### Rollback

1. Pin old package, grammar artifacts, source revision, and environment manifest.
2. Save a consistent pre-upgrade backup via SQLite backup or a fully quiesced
   store. Copying only a live main database file is not a valid backup.
3. Rehearse the update on an isolated repository/store copy first.
4. Roll back by restoring the compatible old environment and a consistent
   isolated backup under quiescence. Never overwrite active reader/writer files.
5. Do not run the old binary against an upgraded schema unless compatibility
   has been explicitly tested. Git source remains unchanged throughout.

## 7. Implementation epics

| Epic | Work items | Dependencies | Exit evidence |
| --- | --- | --- | --- |
| S1-E1: Inventory and freeze | Enumerate imports and entry points, classify tests, freeze logical baseline fixtures and package surface. Capture policy/store manifest. | None | Inventory names each caller and disposition; S2 fixture contract agreed. |
| S1-E2: Decouple foundation | Extract shared AST/presentation utilities; remove eager legacy root imports; separate application operations from CLI parsing. Keep matching rules unchanged. | S1-E1; coordinate S3-E1 | Indexed suite passes with legacy modules blocked at import/runtime; rebuild implications documented. |
| S1-E3: Switch surfaces | Make history indexed-only; add parity from S3; remove/tombstone retired routes; replace or omit mixed viewer. | S1-E2, S3-E2/E3 | Golden CLI/MCP/Python results agree; removed commands cannot invoke legacy code. |
| S1-E4: Remove dead product | Delete only proven unused engine/caches/review code; adapt tests, packaging, docs and research imports; rehearse compatibility matrix. | S1-E3 | Full retained suite, package tests and rollback rehearsal pass. |
| S1-E5: Release candidate | Breaking release notes, clean install, offline smoke, published benchmark reference, installed MCP discovery; prepare but do not automatically publish. | S1-E4, S2 qualification gates, S3-E5 | Checklist signed; old results clearly historical; no unavailable capability advertised. |

Expected affected areas: package root, entities helpers, index evidence/imports,
CLI, MCP, context/revisions/diff/history, viewer, evaluation adapters, tests,
verification scripts, README/quickstart/technical/foundation docs, build metadata,
and CI. Exact removals MUST come from S1-E1, not a bulk folder deletion.

## 8. Test and release matrix

| Test | Required observations |
| --- | --- |
| Parser-preservation fixtures | Same names/kinds/parents/byte spans/tokens for supported rules; no SQL or unsupported-language expansion by accident. |
| No-reparse read matrix | Resolve/history/events/state/entities/source with missing/ready/partial indexes; parser/build entry points forbidden. |
| Clean-process import | Indexed imports succeed when retired modules are unavailable; no benchmark/dev extras needed. |
| Installed adapters | Wheel and wheel-built-from-sdist; Python 3.11/3.12/3.13, Windows/Linux under the existing support matrix. |
| Migration refusal | Schema/policy divergence, shallow boundaries, changed refs, corrupt records; no fallback/mutation. |
| Cross-generation behavior | Reader held during update; old cursor either resumes compatible generation or gives explicit incompatibility/gone result. |
| Store lifecycle | Same-tip no-op, append update, rollback on cancel/crash, in-use GC refusal. |
| Source fidelity | LF/CRLF, multibyte text, no trailing newline, moved path, deleted entity, invalid/tampered references. |
| Package/docs | No legacy exports, broken links, stale schemas, or runnable retired examples. |

Use existing foundation tests as invariants, not just overall pass counts:
[builder](../../../tests/test_index_builder.py),
[query](../../../tests/test_index_query.py),
[store](../../../tests/test_index_store.py),
[committed Git objects](../../../tests/test_objects.py).
Tests dedicated to the old adapter in
[indexed compare](../../../tests/test_indexed_compare.py) must be reassigned to
event/query semantics or removed with explicit rationale.

### Definition of done

- S1-R01 through S1-R12 have executable evidence and owner-reviewed outcomes.
- Every runtime entry point is assigned to the indexed contract or retired.
- No unexplained change in source fidelity, lineage decisions, coverage, or IDs
  within the same interpretation.
- Cross-interpretation differences are explicit; no false byte-identical promise
  across fingerprint-changing refactors.
- Store backups, refusals and recovery instructions have been rehearsed.
- No automatic push, publication, or user-data cleanup occurs as a side effect.
