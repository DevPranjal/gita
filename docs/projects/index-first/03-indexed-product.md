# S3: Indexed-first product, query, and lifecycle contracts

**Status:** Draft specification. **Priority:** Core unless explicitly optional.
**Increment implemented:** Ordinary history now defaults to published indexed
evidence; `--indexed` is a compatibility no-op. Legacy product entry points
require `GITA_ENABLE_LEGACY=1`, plus `--legacy` for reconstructing history.
The default browser is indexed-only. Unified query parity, independent
verification, lifecycle expansion and endpoint comparison remain unimplemented;
this is not completion of S3.

**Audience:** Product owner, API/CLI/MCP maintainers, UI and research developers.

**Companions:** [S1: Retirement](01-safe-retirement.md),
[S2: Index qualification](02-index-qualification.md), [delivery sequence](README.md).

## 1. Product definition and boundaries

**Gita is a local entity-evolution store derived from Git.** Tree-sitter supplies
structural evidence during indexing. Persistent identities let people and agents
follow supported code edits, renames and moves, inspect recorded transitions,
and retrieve exact historical source.

Git still stores files and commits. Gita is not a new VCS, a semantic compiler,
or an autonomous agent. Its primary promise is **queryable evidence with explicit
identity, scope, provenance and uncertainty**, not guaranteed lower model spend.

### Primary users and jobs

| Job | Workflow | Outcome |
| --- | --- | --- |
| Understand an entity | Resolve name -> select EID -> inspect state/history | Correct identity rather than file-name reconstruction. |
| Investigate a regression | Range events -> entity history -> exact earlier source | Evidence for the agent/developer to reason about; no automatic causal verdict. |
| Follow a refactor | Identity across supported rename/move -> occurrences | Explicit accepted links and gaps, not fuzzy path tracing. |
| Reuse investigation context | Keep scope/generation/reference across calls | Stable evidence while new commits are indexed. |
| Maintain local knowledge | Build/update -> inspect freshness/coverage -> explicit rebuild/retention | Predictable operational cost and safe lifecycle. |

### Initial non-goals

Dirty/staged/untracked entity indexing; automatic background indexing; embeddings
or natural-language search; call graphs; affected-test selection; automatic fixes;
merge safety; universal cross-repository IDs; all-parent merge lineage; model
training/inference; remote multi-tenant hosting; Git mutation.

## 2. Current capability versus target

| Capability | Current | Target |
| --- | --- | --- |
| Index/status/build/rebuild/GC | Implemented through indexed CLI/MCP. | Preserve, improve explicit lifecycle diagnostics. |
| Resolve and indexed entity history | Implemented; legacy history still coexists. | Only indexed behavior, explicit selectors, no engine flag required. |
| Range events | `events_in` and indexed range history implemented. | Dedicated `events` operation across transports. |
| State/source/file entities | Python/MCP implemented. | CLI parity and shared response/reference contract. |
| Event patches | Indexed history derives patches from exact occurrences. | Keep optional, with explicit patch availability and byte/source fidelity. |
| True indexed endpoint diff | Not implemented as a general query; compatibility adapter returns events. | Optional separately gated capability, not a first-release dependency. |
| Independent verification | Unavailable; current command refuses honestly. | Scope-specific verifier with evidence, not a rename of SQLite quick_check. |
| Viewer | Indexed catalogs/history plus legacy compare/show. | Fully indexed browser or omit from release. |
| Top-level Python API | Legacy-oriented exports. | Small stable indexed facade; internal record helpers remain private. |

Sources: [query methods](../../../src/gita/index/query.py),
[indexed CLI](../../../src/gita/cli/indexed.py),
[MCP declarations](../../../src/gita/mcp/server.py#L119),
[mixed viewer](../../../src/gita/web/__main__.py#L92),
[comparison adapter](../../../src/gita/index/compare.py#L22).

## 3. Product requirements

| ID | Requirement | Acceptance |
| --- | --- | --- |
| S3-R01 | Normal queries MUST use only a published compatible generation. | No implicit build, parser call, ancestry reconstruction, network or repair. |
| S3-R02 | Entity identity MUST be separate from name, path, version and occurrence. | Resolution returns EID plus occurrence/lifecycle; historical name reuse stays distinct. |
| S3-R03 | Every successful page MUST carry scope, generation, context, selected revision, coverage and continuation metadata. | Golden schemas and cross-transport parity tests. |
| S3-R04 | Defaults MUST be deterministic and visible. | Query defaults to indexed tip; explicit out-of-scope revision refuses, never clips to tip. |
| S3-R05 | Events MUST not be described as endpoint diffs. | Edit/revert and `(A,B]` tests, independent operation names. |
| S3-R06 | Exact source MUST preserve bytes or fail explicitly. | UTF-8/base64 encoding and byte_length; no truncated-source success. |
| S3-R07 | State MUST distinguish present/removed/absent/unknown; out-of-scope is an error. | Lifecycle fixture outcomes across transports. |
| S3-R08 | Errors MUST be actionable and machine-readable. | Typed code/message/retryability/recovery; no broad success-shaped defaults. |
| S3-R09 | Reads MUST NOT mutate Git or index evidence. | Ref/worktree/staging and logical store comparisons; SQLite's necessary read coordination is not an evidence write. |
| S3-R10 | Builds, GC, verification and source checking MUST be explicit lifecycle actions. | Default index action is status; no hidden build or expensive verify from a normal query. |
| S3-R11 | Discovery/help/docs MUST expose exactly the supported release surface. | Installed CLI/MCP schema tests; no unsupported worktree/diff features. |
| S3-R12 | Verification claims MUST name what was independently checked and what was not. | Complete/partial/unavailable distinctions and structured findings. |
| S3-R13 | Optional UI MUST use the same indexed semantics and provenance as APIs. | No private legacy routes or unpinned source following. |
| S3-R14 | Resource omission MUST not erase coverage/identity uncertainty. | Complete metadata before optional patches; explicit budget error if minimum answer cannot fit. |
| S3-R15 | Offline operation MUST use provisioned local objects and grammars. | Network-disabled build/read/source tests; missing artifacts reported. |

## 4. Identity and evidence semantics

### 4.1 Record model

Retain the current distinction:

- **Version:** extracted representation and exact source digest, independent of
  location.
- **Definition:** names, containment, discriminator, and byte/line span in a parse.
- **Occurrence:** definition at a commit/path/blob/mode.
- **EID:** a lineage seed and accepted continuations in an interpretation context.
- **Event:** recorded transition with independent flags and decision evidence.
- **Scope/generation:** selected first-parent history and immutable publication.

IDs are reproducible only within their defined inputs/policy/context. A content-
identical copy is not the same identity. A deleted then reintroduced function is
not automatically resurrected. `oeid1_` observed provenance is not proof of birth.
Keep these distinctions in text displays, not only internal JSON.

Evidence: [model](../../../src/gita/index/model.py),
[strict policy](../../../src/gita/index/matching.py#L155),
[state](../../../src/gita/index/query.py#L305).

### 4.2 Coverage

Responses distinguish at least:

1. Membership boundary: root/bounded/shallow/missing parent.
2. Extraction coverage: complete, unsupported, unavailable/failed evidence.
3. Matching coverage: accepted evidence versus ambiguous/candidate/limited work.
4. Source availability: whether exact Git bytes can currently be retrieved.

Do not replace stored raw coverage reasons with one boolean. An entity may have
valid recorded history even when some files are unsupported or old source is no
longer locally available. A partial answer is usable evidence, not completeness.

### 4.3 Matching

Keep the strict accepted-link policy. Similarity may supply diagnostics/candidates;
it must not silently establish continuity. Do not expose arbitrary user overrides
that merge EIDs in the first release. Correction/annotation workflows need their
own durable provenance design.

## 5. Shared public query contract

Create one small transport-neutral indexed facade around existing query/store
primitives. Typed requests/results and the response budget layer are shared.
CLI parses arguments, MCP declares tools, Python returns objects, and the UI
renders the same facts; none calls another adapter's parser.

### 5.1 Versioning and envelope

Proposed response contract version **2**, independent of the SQLite schema version
(currently also numbered 2). Version 2 indicates adapter/operation changes, not
permission to rewrite storage.

| Field | Meaning |
| --- | --- |
| `schema_version` | Public response contract version. |
| `command` | Canonical operation, e.g. `history` or `events`. |
| `status` | Operation-specific terminal outcome, not a transport success substitute. |
| `scope`, `generation`, `policy` | Scope name/id, generation, context and parser/matcher fingerprints. |
| `revision`, optional `range` | Requested and resolved/pinned commits, or explicit base/head OIDs. |
| `boundary`, `coverage` | Interpretation/membership boundaries and structured coverage. |
| `refs`, `freshness` | Requested freshness check and its result, or explicitly unchecked. |
| `results` | Typed operation payload. |
| `page`, `omitted` | Shown/total/continuation plus explicit truncation/omission metadata. |
| `error` | `null` or code, message, retryable, recovery. |

Reuse existing useful fields rather than inventing a parallel JSON dialect.
Unknown counts are `null` with known/unknown semantics, not zero. Minimum metadata
and errors must fit before optional content; otherwise return budget failure.

All structured output uses strict validation and deterministic ordering. Human
output may omit redundant fields visually but must show identity, generation,
coverage warnings, and whether source/patch content was omitted.

### 5.2 Selectors and revisions

- Default scope: `default`, visible in every response.
- Default query revision: selected generation's indexed tip, **not live HEAD**.
- Default generation: current publication, pinned once when the operation opens.
- `id` accepts exact names, qualified locators, or supported typed ID prefixes.
  Preserve current literal lookup precedence and ambiguity.
- `history` accepts an entity selector, but resolves once and returns ambiguity
  rather than selecting a fitting result. Clients SHOULD use full returned EIDs.
- `state` and optional entity comparison require full EIDs.
- A revision must be a member of the chosen generation. No nearest-commit fallback.
- A cursor fixes operation, selector, generation, scope/context, revision/range,
  ordering and position. Caller modifications are rejected.
- Missing/collected/incompatible generations give distinct errors; never redirect
  a cursor to today's generation.

The current implementation already supplies most of this in
[`resolve`](../../../src/gita/index/query.py#L542) and
[cursor handling](../../../src/gita/index/query.py#L394).

## 6. Proposed command and tool surface

These examples are **target syntax, not currently available commands**. Paths in
the PowerShell examples are illustrative. Global repository/JSON flags remain
accepted consistently; placeholders must be replaced.

```powershell
gita -C C:\repo index --scope project
gita -C C:\repo index --scope project --status
gita -C C:\repo id YourFunction --scope project --json
gita -C C:\repo history FULL_EID --scope project --order oldest --json
gita -C C:\repo events BASE_SHA HEAD_SHA --scope project --json
gita -C C:\repo state FULL_EID --at COMMIT_SHA --scope project --json
gita -C C:\repo entities src\module.py --at COMMIT_SHA --scope project --json
gita -C C:\repo source --reference-file C:\references\occurrence.json --json
```

Canonical stored paths remain repository-relative raw bytes/display POSIX paths;
CLI normalization must not reinterpret a real backslash in a Git path on a
platform that permits it. JSON reference input is the lossless route for
non-displayable paths.

| Operation | Python service | MCP | Contract |
| --- | --- | --- | --- |
| Index status/build/rebuild/GC | Lifecycle operations | `gita_index` | Explicit action; status default; ref/since/workers/deadline semantics preserved. |
| Identity lookup | `resolve` | `gita_id` | Paginated candidates; no ambiguity guessing. |
| Entity history | `history` | `gita_history` | Entity only; newest default; at inclusive; optional event patches. |
| Range events | `events` wrapping `events_in` | Proposed `gita_events` | Two explicit endpoints, `(base, head]`, oldest first. |
| Historical state | `state` wrapping `state_at` | `gita_state` | Present/removed/absent/unknown and validated occurrence reference. |
| Exact source | `source` | `gita_source` | Complete bytes via encoded structured content; reference input required. |
| File entities | `entities` wrapping `entities_in` | `gita_entities` | Path/revision scoped, paginated exact occurrences. |

The facade MUST reuse existing storage/query logic. Public method names are not
an instruction to rename every internal method.

### Source reference file

Proposed portable input:

```json
{
  "scope": "project",
  "generation": "RETURNED_GENERATION",
  "occurrence": {
    "...": "complete unchanged occurrence object returned by a query"
  }
}
```

The inner object is explanatory, not a valid occurrence fixture. Real input MUST
contain all required fields unchanged, including its generation and byte path.
The wrapper and occurrence generation must agree. `--reference-file -` MAY read
one UTF-8 JSON object from stdin; enforce a 1 MiB input cap, strict types and no
automatic schema conversion. References are trusted as selectors only after
membership/digest validation; they are not authentication tokens.

Source output preserves current `encoding` (`utf-8` or `base64`) and `byte_length`.
Output budgets never silently trim source. No raw-output bypass is required for
the initial release.

### Event patches

Keep a metadata-only mode (`--brief`/`brief=true`). With patches requested/defaulted
on history, prioritize event metadata; include only complete patches that fit.
Preserve `patch_status`: included, unchanged, not_applicable, omitted_budget,
or explicit unavailable reason. Source/destination references remain retrievable.
Do not call absence of a displayed patch "no change."

Range events use the same patch option as entity history. Source hunks are views
of extracted byte spans and need not be independently applicable whole-file patches.

### Errors and exits

Preserve the useful indexed classes:

| CLI exit | Meaning |
| --- | --- |
| 0 | Successful operation, including explicit partial coverage; inspect status. |
| 1 | Unresolved/ambiguous/not-live selector when the command requires one live identity. |
| 2 | Invalid command/options/selector/cursor/revision or retired command. |
| 3 | Missing/incompatible/gone index or unavailable verifier. |
| 4 | Resource/I/O/source/permission/cleanup failure. |
| 5 | Corrupt record/integrity failure. |
| 6 | Response budget too small. |

A successful `state` query reporting removed/absent/unknown is not a failed
lookup: the state is the answer. MCP uses typed `error`/status, never CLI stderr.
Python exposes typed result/error objects with the same codes. The transport may
enforce its own resource constraints but cannot change evidence semantics.

## 7. Index lifecycle and UX

### 7.1 Explicit build/update

- Resolve/pin the requested ref; preserve first-parent membership and boundary.
- Default new build covers locally available first-parent history.
- `--since` explicitly bounds the anchor. A current-tree scope is legitimate,
  but is an observed snapshot, not established birth/full history.
- Compatible repeat builds extend the suffix; identical tips are no-ops.
- Divergence, incompatible policy/boundary, shortened timeline require explicit
  rebuild; no auto-delete and restart.
- Progress goes to stderr or a separate opt-in structured event stream, not into
  JSON stdout or the MCP protocol. Include phase, processed/known total, parse
  statuses, elapsed and cancellation state; unknown totals remain unknown.
- Cancellation rolls back the invocation. A progress count is not a durable
  checkpoint; resumability means restarting from the last published generation.

### 7.2 Status and freshness

Status MUST report indexed tip, boundary, policies, coverage, generation, store
schema compatibility, and whether a live-ref comparison was requested.

Proposed freshness values: `current`, `behind`, `diverged`, `ref_unavailable`,
`unchecked`, with the actual compared ref/OID and reason. Only claim `behind`
after checking first-parent continuation; different tips alone do not establish
that relationship. Freshness inspection is an explicit status/lifecycle operation
and MAY inspect ancestry; normal pinned evidence reads do not.

If freshness cannot be checked, preserve the error/reason instead of masking it.
Current [`_metadata`](../../../src/gita/cli/indexed.py#L304) supplies limited
freshness behavior; the richer classification above is proposed work.

Normal queries MAY operate on an older pinned generation without auto-update.
They always display its tip; they do not claim it equals the live repository.

### 7.3 Storage and retention

- Retain common-Git-directory storage for linked worktrees; scopes/generations
  are required to avoid pretending all worktrees share one current branch.
- Keep exclusive creation, WAL readers, transactional writes and resource limits.
- GC remains explicit by exact generation; published/predecessor guards apply.
- Collection does not promise physical file shrink or reclamation of all historical
  generations; storage reporting explains retained dependencies.
- No automatic TTL, background vacuum, remote sync, or backup subsystem in the
  first release.
- Incompatible old generations may be retained yet unreadable by the new policy;
  distinguish retention from compatibility and follow S1 rollback instructions.

Evidence: [`IndexStore.locate`](../../../src/gita/index/store.py#L267),
[publication/GC](../../../src/gita/index/store.py#L1343).

## 8. Independent verification

### Objective

Establish which recorded facts can be independently checked against Git and the
declared policy. Verification is an **explicit diagnostic lifecycle operation**,
not a normal query and never triggered automatically by one.

Proposed `index --verify` levels:

| Level | Work | Claim permitted |
| --- | --- | --- |
| V0 storage | SQLite integrity/FKs and canonical record/link/member invariants. | Store structure checked, not extraction/lineage completeness. |
| V1 source | Validate selected/all occurrence spans/digests against local Git objects. | Specified source references match exact bytes. |
| V2 inventory | Independently enumerate declared Git membership/tree entries and compare covered/unsupported/unknown states and bindings. | Inventory/coverage for the verified scope checked. |
| V3 policy evidence | Independently check accepted-link predicates, population uniqueness/copy guards, event flags, and provenance under the declared policy. | Specified structural policy checks passed; not semantic equivalence. |

The first implementation MAY ship only V0/V1, but MUST NOT call that complete
F4 verification. Unknown requested levels produce unavailable status. To advertise
independent foundation verification, all declared V0-V3 checks and their
independence review must pass. Performance/real-world accuracy remain separate
qualification, even after these pass.

Rules:

- Verifier expected results MUST NOT come from calling the production builder or
  matcher and comparing it with itself.
- Shared decoding/canonicalization primitives MAY be used, with independent fixed
  test vectors; core inventory/decision predicates need independently reviewed
  checks and adversarial corrupted fixtures.
- Optional re-extraction, if needed, belongs only to this explicit verifier job.
  It must report parser dependence and must not repair or overwrite evidence.
- Offline and bounded operation; missing objects, unsupported verification cases,
  cancellation and sampling remain explicit.
- Full verification reports total scope and every check/outcome. Sampling can
  only claim sampled verification with seed/population/count, never full coverage.
- Findings include code, affected scope/generation/record/commit and reason.
  `verified` only if all requested checks completed and passed; otherwise partial,
  failed, unavailable, or cancelled.
- Provide negative tests: tampered spans/digests, missing members, false accepted
  links, concealed unsupported entries, incorrect event flags, invalid anchors.

Current [`publish_generation`](../../../src/gita/index/store.py#L1343) explicitly
does not prove inventory/F4 source-policy completeness;
[`check_integrity`](../../../src/gita/index/store.py#L1535) is SQLite-level only.

**Release slicing:** declaring supported levels and preserving unavailable
responses is mandatory for the first indexed-only release. Completing V0-V3 is
a separately gated follow-up; shipping the retirement does not authorize a full
verification claim. S3-E4 remains open until that implementation and independence
review are complete.

## 9. Optional genuine endpoint comparison

**Not a core-release dependency.** Do not retain the old engine while waiting.

### Entity comparison

Within one compatible context/generation, select a full EID and two member
revisions. Read `state_at` for each; retrieve exact source only for requested
patches. Distinguish:

- Both present, source/location unchanged.
- Both present, source and/or location changed.
- Proven absent/removed at one endpoint and present at the other.
- Unknown endpoint evidence: return uncertainty, not an invented add/remove.

A full source revert has equal endpoint bytes even if history contains events.
Identical bytes after a move may still have a changed location. Report source,
own/signature/body/subtree representation and location differences separately;
normalized structural equality is not behavioral equivalence.

### Repository comparison

Requires a new indexed endpoint-population enumerator, not a Git diff parse:

1. Enumerate active bindings and file coverage at A and B.
2. Join by EID in the same interpretation.
3. Classify net state/source/location differences.
4. Preserve incomparable/unknown states and coverage with explicit counts.
5. Stable paginate the result by declared keys; keep source patch work optional.

No cross-context/EID guessing, no pairing by name or similarity, no worktree
mode, and no manufacturing a net answer by merely listing events. Counts are
unknown where coverage prevents an exact total. Explicit filters apply to the
indexed results and never erase coverage limitations.

**Gate:** endpoint comparison is enabled only after edit/revert, move-with-equal-
bytes, copies, deletion/reintroduction, missing source, ambiguity/gaps, and
pagination tests pass, plus S2 resource measurements. Until then `diff` is retired.

## 10. Optional indexed browser

Recommended minimum: scope/generation/tip header, file entity catalog, EID
resolution/candidates, timeline, state/source panels and visible coverage.
Pin the generation for the browsing session. Refresh is explicit; a source click
must not follow a newly resolved live name into another identity.

Remove old compare/patch routes and command playground unless implemented through
the same indexed service. No mutation endpoints; bind to loopback by default.
Do not let an unavailable browser block a correct CLI/Python/MCP release.

## 11. Testing and implementation epics

Cross-transport golden tests compare canonical fields, not formatting. Existing
[query](../../../tests/test_index_query.py), [CLI](../../../tests/test_cli.py),
[MCP](../../../tests/test_mcp.py), and [web](../../../tests/test_web.py) tests supply
starting points; S1 classifies retired versus retained behavior.

| Epic | Work items | Dependencies | Exit |
| --- | --- | --- | --- |
| S3-E1: Contract/facade | Typed operation requests/results, envelope v2, neutral budget/source presentation, version policy; remove MCP-to-CLI dispatch coupling. | S1-E1; coordinate S1-E2 | Direct Python operations validated; no legacy imports or transport recursion. |
| S3-E2: Query parity | Indexed-only history, events, state/source/entities CLI, reference validation, selectors/cursors/errors and MCP discovery. | S3-E1 | CLI/Python/MCP canonical agreement; no parsing on queries; explicit missing-index behavior. |
| S3-E3: Lifecycle UX | Status/freshness, explicit build/update/rebuild/GC, safe progress, cancellation and linked-worktree cases. | S3-E1 | No silent mutation or false freshness; S2 failure cases and S1 compatibility matrix pass. |
| S3-E4: Verifier | V0/V1, then independent V2/V3, structured findings and corruption tests; preserve unavailable claims for unfinished levels. | S2-E1/E2; S3-E1 | Exact declared levels work independently; failed/partial runs cannot report full verification. |
| S3-E5: Product polish | Getting-started workflow, contracts/examples, migration guide, optional browser conversion, research-result labeling. | S3-E2/E3, S1-E3 | New user can build/resolve/history/source with no old-product route; optional UI either passes or is omitted. |
| S3-E6: Endpoint diff | Indexed entity and repository snapshot comparison, stable pagination, source patches, dedicated tests/measurements. | S3-E1/E2, S2-E2/E4 | Honest endpoint semantics and declared coverage; separately releasable capability. |

### Acceptance scenarios

1. Fresh clone with provisioned grammars: status says not built; history does not
   create storage. Explicit build then id/history/state/source works offline.
2. Rename/move sequence: full EID resolves to correct occurrences; historical
   names do not see future names, and duplicates return candidates.
3. New commit without reindex: queries show the old pinned tip; explicit status
   distinguishes stale/diverged/unavailable, update is user-controlled.
4. Reader during update: cursor/source reference remains attached to old generation;
   incompatible or collected cases refuse explicitly.
5. Dirty worktree: exact historical source is unchanged; no worktree entity query
   is silently offered.
6. Partial parsing: file and identity uncertainty shown; `unknown` is not zero,
   absent, unchanged, or a semantic verdict.
7. Small response budget: complete minimum metadata or explicit failure; omitted
   patches retain references; exact source never truncated.
8. Missing/pruned blob: recorded history may remain inspectable; source request
   returns unavailable rather than current file content.
9. Verifier unfinished: command reports exact available/requested levels; no
   "all verified" from a SQLite quick_check or an honest-refusal test.
10. Optional diff: edit/revert produces events but no net source change;
    moved same-source entity reports location change, not a new identity.

### Definition of done

S3-R01 through S3-R15 have reviewed contracts and executable evidence. Every
advertised operation is available in the installed package with correct
provenance; unsupported operations are not disguised as successful empty answers.
The release documents verification/performance limits and does not claim that
index-first architecture alone proves agent quality, offline hardware suitability,
or research novelty.
