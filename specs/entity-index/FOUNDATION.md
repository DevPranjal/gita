---
goal: Establish an evidence-backed entity identity and historical state foundation for gita
version: 1.0
date_created: 2026-09-14
last_updated: 2026-09-14
owner: gita maintainers
tags: [architecture, identity, index, implementation-plan]
---

# Entity-Tracking Foundation

This is the authoritative implementation contract for the entity-tracking
foundation. It is a specification, not a claim that the proposed index exists.
Requirements, architecture, acceptance checks and delivery order are collected
here so implementers do not have to reconcile several competing contracts.

[First-principles](../../scratch/FIRST-PRINCIPLES.md) contains the broader product
vision: identity, structured investigation, evidence-backed comparison, graphs,
anchored memory, and coordination. It is not a complete executable design for
those capabilities. This foundation supplies reliable addressing, observations,
and history for future review, trace, and compare workflows. It does not implement
the entire vision or promise its later outcomes.

The motivating acceptance case is one function across five commits: birth, body
edit, rename, file move, body edit. The desired answer is one accepted lineage
and five events across process restarts. Equally important: indistinguishable
copies MUST NOT be joined merely to obtain a longer history.

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT, and MAY have their
RFC 2119 meanings. FR identifies functional requirements; NFR operational
requirements; AC acceptance criteria; TEST test cases; FM failure modes;
RD resolved decisions; EPIC and ITEM implementation work.

## 1. Goals and Non-Goals

### In Scope

- A local, deletable, SQLite index derived only from committed Git objects.
- Immutable versions, exact revision-specific occurrences, inspectable lineage
  links, and parent-edge change events, with policy-qualified EID metadata.
- Correct historical state even when an entity's bytes are unchanged but its
  position changes, or when only a container's descendants change.
- First-parent history, one selected scope per operation, with multiple isolated
  scopes in one database and explicit coverage boundaries.
- Explicit builds, incremental continuation, pinned queries, verification, and
  opt-in indexed history without breaking existing diff/history APIs.
- A test-first delivery sequence, operational checks, and measured release gates.

### Out of Scope (deferred)

- Semantic equivalence, behavioral safety, caller/test graphs, impact analysis,
  automatic conflict resolution, and identity across all merge parents.
- Accepted splits, many-to-one merges, resurrection links, manual identity
  overrides, cross-scope alias union, or identity repair after rebase.
- Working-tree or staged indexing, live fallback indexing during a read, notes,
  claims, general SQL/query language, cloud services, models, and NPU features.
- A blanket speedup for existing commands or indefinite identifier stability.
  This core MUST work without network access, model inference, or credentials.

## 2. Terminology

| Term | Meaning |
| --- | --- |
| Version | Immutable, fingerprinted parse representation, including exact slice digest and full normalized evidence; not a location or identity. |
| Definition | One entity extracted from a particular file parse, with byte bounds, containment, kind, and locator components. |
| Occurrence | A definition situated in an exact commit, raw Git path, blob, object type, and mode. It has an opaque occurrence ID. |
| Lineage link | A policy decision between source and destination occurrences on a validated first-parent edge, with evidence and status. |
| EID | An identifier for a policy-qualified accepted chain. It is not proof of semantic identity. |
| Event | An observed transition on a specific parent edge, with source/destination occurrences and independently computed flags. |
| Scope | A locally named first-parent timeline with an explicit anchor, interpretation context, and published generation. |
| Generation | One published, immutable scope view ending at a pinned commit. A later build publishes a new view. |
| Context | Canonical boundary, parser, matching, and coverage-policy provenance used in identity derivation. Excludes ref names and database allocation order. |
| Sequence | A scope-generation-local ordinal, starting at zero at the anchor. It is never a global commit clock. |
| Genesis | Earliest accepted seed under complete root traversal and the declared policy; still a structural, not semantic, claim. |
| Observed seed | First observation with incomplete predecessor evidence, including bounded/shallow starts, unresolved matching, or a parse gap. |
| Material event | Addition/removal, rename/move, or own signature/body/other-content change. Cosmetic and descendant-only changes remain inspectable but are nonmaterial. |
| Coverage | History membership, file extraction outcomes, object availability, matching omissions, and unsupported areas of an answer. |

## 3. Solution Architecture

### 3.1 Existing Versus Proposed

The following observations were checked against nearby source while drafting.
Proposed paths in this document are code-formatted names, not links to files
that do not exist. Links identify existing evidence, not promised implementation.

| Existing surface | Verified behavior | Required foundation change and owner |
| --- | --- | --- |
| [Entity model](../../src/gita/entities/model.py#L34) | `Entity.id` is a location string; hashes are BLAKE2b-64 over decoded/normalized text; some module fields are empty. | Evidence owner adds bounded byte-accurate records without redefining legacy IDs or hashes. |
| [Extractor](../../src/gita/entities/extractor.py#L207) | `_make_entity` exposes lines, not byte bounds; displayed signature is truncated. Error roots still traverse children. | Evidence adapter obtains exact offsets, full representations, and authoritative parse outcomes. Display text MUST NOT become matching evidence. |
| [Own-token extraction](../../src/gita/entities/extractor.py#L141) | Descendant entities are excluded from container-owned material. | Store own and complete-subtree evidence separately. |
| [TreeStore](../../src/gita/entities/store.py#L26) | In-memory LRU parse cache, not persisted lineage. | Retain for legacy paths; persistent definitions and coverage belong to the new index. |
| [Differ](../../src/gita/diff/differ.py#L123) | Duplicate siblings use raw equality, then locator and positional fallback; fuzzy selection is greedy with no tie rejection. | Builder MUST NOT persist these decisions as accepted identity. Add a separate strict policy. |
| [Move reconciliation](../../src/gita/diff/differ.py#L83) | Hash uniqueness is checked among unmatched additions/removals, not retained populations. | Count candidates against complete eligible populations before accepting a relocation. |
| [History](../../src/gita/history.py#L126) | Present-name path pruning and exact location-ID filtering lose durable history. | Add opt-in query routing; preserve legacy defaults and return types. |
| [Git reader](../../src/gita/vcs/git.py#L115) | Blob reads already batch; empty bytes collapse to missing. `walk` is newest-first and parses name-status text. | Add commit-only, byte-safe enumeration/object APIs; distinguish empty from missing. |
| [WS-1](WS-1-enumeration.md) | Implemented bulk enumeration and merge fallback optimization. | Reuse capability knowledge; do not assume its textual framing is safe for index ingestion. |
| [CLI](../../src/gita/cli/__init__.py#L439) | Existing history command, renderer, budgets, and errors. | Query/CLI owner adds new commands and explicit `--indexed`; no implicit behavior substitution. |

The [previous design](../../scratch/SPEC-ENTITY-INDEX.md),
[store plan](WS-2-store.md), [builder plan](WS-3-builder.md),
[query plan](WS-4-query.md), and [verification plan](WS-5-verification.md)
are historical inputs. Their events-as-state, global ordering, alias union,
matcher reuse, destructive recovery, and transparent fallback assumptions MUST
NOT be copied into this implementation. This contract governs conflicts.

### 3.2 Data Flow

1. Resolve and pin committed input OIDs and the repository object format.
2. Validate the selected scope boundary and first-parent membership.
3. Enumerate tree entries and changed paths using byte-safe Git protocols.
4. Obtain cached parse definitions or parse the entire changed file.
5. Build source/destination populations, then compute strict lineage decisions.
6. Write file-state intervals, binding sets, occurrences, decisions, and events
   inside one writer transaction; verify before atomic publication.
7. Query a published generation using indexed state, not an event replay or a
   new history walk. Fetch original source from Git only when requested.

SQLite is the sole persistent implementation dependency; use Python's built-in
`sqlite3`, not an ORM. Existing tree-sitter and token-budget dependencies remain.
The parser is an evidence producer, the matcher is a decision policy, and the
store enforces records and constraints. None silently substitutes for another.

### 3.3 Canonical Encoding and Identifiers

All persistent content identifiers use `H(domain, fields)`: BLAKE2b with a
32-byte digest over a canonical byte encoding. The encoding MUST begin with
the length-prefixed ASCII domain and a format-version integer. Each field has
an explicit type tag; byte/text fields use an unsigned 64-bit big-endian length
followed by bytes; integers use unsigned 64-bit big-endian; optional fields use
an explicit absent/present tag; lists include a count and preserve specified
order. Text is strict UTF-8 with no Unicode normalization. Enum values are
stable ASCII names, never Python enum ordinals. Fields MUST NOT be concatenated
with delimiter-only encoding, locale conversions, or Python `repr`.

**F0 wire format, frozen 2026-09-14.** The header is unsigned-64-bit domain
byte length, ASCII domain bytes, then unsigned-64-bit format version `1`.
The remaining payload is one encoded tuple of fields. `None` is byte `00`;
every present value starts with `01`, then one ASCII tag: `b` for bytes,
`t` for UTF-8 text, `u` for unsigned integer, `l` for tuple. Bytes/text add
an unsigned-64-bit byte length; integers add eight big-endian bytes; tuples
add an unsigned-64-bit element count followed by encoded members. All lengths
and integers are big-endian. The outer tuple counts toward a 64-tuple nesting
limit. Booleans, mutable collections, floats and implicit text coercions are
rejected. Record metadata flags use explicit integer `0`/`1` when serialized.

The current Python contract is in
[index/model.py](../../src/gita/index/model.py); independent wire vectors and
record payload checks are in
[test_index_store.py](../../tests/test_index_store.py). These cover pure F0
contracts, not an implemented store. Normalized representations are immutable
string-token tuples or `None`; `None` is unavailable, while `()` is known empty.
Evidence records can use the more general canonical value vocabulary above.

Definitions retain display lines and the synthetic-root flag as checked metadata,
not additional definition-ID fields. `ParseResult.key_fields()` supplies its
parse ID inputs; `ParseResult.fields()` retains the full outcome, diagnostics and
definition metadata, including display lines and the root flag. F1 MUST compare
full payloads on an existing parse key, not confuse key equality with result
equality. Complete parses can be empty; incomplete parses cannot publish child
definitions and require diagnostic reasons. `PageCounts` represents either an
exact `total = shown + omitted` or jointly unknown total/omitted counts.

An OID is encoded as `(object_format, raw_digest)`, accepting `sha1`/20 bytes
and `sha256`/32 bytes. Git object names and source slice digests are distinct.
Path keys are raw repository-relative Git bytes, `/` separated, case-sensitive,
with no case folding or replacement decoding. A name is an ordered list of
literal qualified-name components, not a reparsed `path::qualname` string.

| Public type | Canonical payload after its domain/version |
| --- | --- |
| `par1_<64hex>` | Parse fingerprint, object-format-qualified blob OID, raw path bytes. Path is initially retained because it selects language and appears in extraction. |
| `ver1_<64hex>` | Parse fingerprint, language, kind, exact source-slice digest, full own/signature/body/subtree normalized representations, explicit availability tags. No path, commit, lines, ordinal, or EID. |
| `def1_<64hex>` | Parse ID, version ID, byte start/end, kind, literal name components, enclosing definition, local discriminator. |
| `occ1_<64hex>` | Commit OID, raw path, blob OID, object type, mode, definition ID. |
| `ctx1_<64hex>` | Anchor OID, boundary kind, ordered actual anchor-parent OIDs, parse fingerprint, match fingerprint, deterministic coverage-limit policy. |
| `eid1_<64hex>` | Context ID, genesis commit OID, seed occurrence ID, seed reason `root` or `observed_addition_with_complete_predecessor`. |
| `oeid1_<64hex>` | Context ID, observation commit OID, seed occurrence ID, boundary/gap/ambiguity reason and its canonical evidence digest. |

The local discriminator distinguishes definitions with equal bounds/names using
the parser's stable node-role path and sibling position. It is only a locator
within a parse and seed input, never continuation evidence. Duplicate `#N`
display suffixes MUST NOT serve as durable identity.

`scp1_<32hex>` and `gen1_<32hex>` are opaque randomly allocated local handles,
not content identity. A local scope name resolves to a scope handle. Neither
handle, scope/ref name, current tip, timestamp, database row ID, nor iteration
order enters an EID. Internal integer keys MAY optimize joins but MUST NOT be
exposed as IDs. Persistent decision/event IDs use their own domain and canonical
edge/endpoints/policy/flags payloads, with the same 32-byte digest convention.

An event payload also includes its EID and context. A binding-set payload includes
its parse ID and the definition/EID pairs sorted by full definition ID. No map
iteration order is an implicit part of either identifier.

Insertions MUST compare canonical payloads on an existing digest key. Different
payloads with the same digest are `digest_collision`, an integrity error, not
permission to coalesce. F0 MUST freeze independently checked encoding vectors,
including empty, absent, Unicode, ambiguous concatenations, and both OID formats.

Identity stability is promised only for the same committed first-parent history,
boundary, available evidence, parser fingerprint, and matching policy. Rebase,
amend, changed parser/policy, deeper boundaries, or repaired gaps can produce
different EIDs. `oeid1_` MUST never silently become, alias to, or be returned as an
`eid1_`. Rebuilds preserve old pinned generations until explicit GC.

### 3.4 Evidence Adapter and Versions

The adapter MUST return a `ParseResult` containing parse ID, fingerprint,
language, status, diagnostics, definition records, and source byte length.
Statuses are `complete`, `unsupported_language`, `unsupported_encoding`,
`parse_error`, `oversized`, `definition_limit`, `representation_limit`,
all determined from available bytes and the parse fingerprint. `missing_object`
and `non_source_object` are file coverage outcomes with no parse-result reference:
object retrieval/eligibility happens before parsing. A temporarily missing object
MUST NOT poison the immutable parse cache when its bytes later become available.
An empty complete file is not missing. No path or language may disappear from
enumeration without an outcome record.

For complete supported files, each definition MUST carry:

- Half-open original byte bounds `[start_byte, end_byte)` with
  `0 <= start <= end <= blob_length`, 1-based display lines, enclosing
  definition, literal name/qualifier components, kind, and synthetic-root flag.
- Digest of the exact original byte slice, without decoding or normalization.
- Full own, signature, body, and complete-subtree normalized representations,
  their digests and token counts, with `unavailable` different from empty.
- Normalization names tied to the parse fingerprint. Container own material
  excludes descendants; complete-subtree material includes them recursively.

Bytes remain in Git; the index stores derived representations, not a second
authoritative source archive. A byte-slice lookup reads the exact blob OID and
verifies bounds/digest. CRLF, BOM, multibyte characters, and missing final newline
MUST be tested. Display lines are derived from original LF byte positions:
start is `1 + count(LF before start)`; nonempty end is
`1 + count(LF before end - 1)`; an empty span ends on its start line.

Initially accept strict UTF-8 source, with a UTF-8 BOM handled without shifting
original offsets. Other encodings receive `unsupported_encoding`; do not match
replacement-decoded text. The adapter MUST NOT publish the current extractor's
salvaged children when the parse root has errors. A coarse file observation is
still retained, but its child population is unknown, not empty. New byte/token
evidence access is an additive extractor change; legacy `Entity` consumers keep
their current representation and behavior.

The parse fingerprint covers adapter/normalization versions, supported extension
mapping, actual grammar artifact identities and versions, extraction rules,
kind vocabulary, encoding policy, and deterministic parse limits. A declaration
or code/artifact digest MUST make edits to extraction rules invalidate it.
The matching fingerprint independently covers tier order, eligibility rules,
duplicate checks, `MIN_BODY_TOKENS`, shingle/threshold rules, link/event vocabulary,
and deterministic candidate limits. A matching-only rebuild SHOULD reuse
compatible parse definitions; it MUST recompute all identity decisions. A parser
change invalidates both derived definitions and dependent decisions. No migration
may preserve rows under a fingerprint whose meaning changed.

### 3.5 Declarative Storage Contract

These are declarative schemas, not executable SQL. F1 MUST supply and test the
DDL, constraints, indexes, and row round-trips before builder implementation.
`OID` always includes object format. `ID` denotes a full typed identifier above.
All joins denoted as references MUST have enforced foreign keys when the target
is an indexed record. A real parent OID may lack a commit row at a boundary;
that is explicit missing provenance, not a broken foreign key.

| Relation | Key and fields | Constraints and principal indexes |
| --- | --- | --- |
| `metadata` | schema/encoding versions; application diagnostics | `user_version` agrees with declared schema. Diagnostic times do not affect identity. |
| `interpretation` | fingerprint ID, category parse/match, canonical manifest | Immutable; payload verified on collision. |
| `commit_object` | OID; tree OID; ordered parent OIDs; optional author/date/subject | Full OID unique; metadata never orders identity. Parents stored in Git order, including octopus parents. |
| `scope` | scope ID, unique local name, published generation reference | One selected published generation; local naming is not provenance. |
| `generation` | generation ID, scope reference, context, tip OID, anchor, boundary, predecessor generation, build state, coverage summary | Published immutable; queryable only after publication. Pin exact tip and fingerprints. |
| `membership` | generation reference, local sequence, commit reference, first-parent OID | Unique `(generation, sequence)` and `(generation, OID)`; consecutive members are actual first-parent neighbors. |
| `parse_result` | parse ID, blob/path/fingerprint, status, byte size, diagnostics | Unique canonical parse key; incomplete statuses have no authoritative child definitions. |
| `version` | version ID, canonical representations/digests/availability | Immutable; no location or lineage dependency. |
| `definition` | definition ID, parse/version references, enclosing definition, byte/line bounds, kind, names, discriminator | Bounds validated; enclosing definition belongs to same parse; index `(parse, name_components)`. |
| `lineage` | EID, context, seed occurrence reference, seed kind/reason/evidence | One immutable seed. No `alias_of`. Accepted chain membership is scoped, not global. |
| `binding_set` | binding-set ID, parse reference, digest of sorted definition/EID members | Immutable; the same parse can have different identity bindings in different contexts. |
| `binding` | binding-set reference, definition reference, EID reference | Unique definition per set and EID per set; reverse index `(EID, binding_set)`. |
| `file_state` | generation, raw path BLOB, `from_seq`, exclusive `to_seq`, entry status, blob/type/mode, parse reference, binding-set reference | Disjoint intervals per `(generation, path)`; index `(generation, path, from_seq)` and reverse binding-set index. Deleted paths have tombstones. |
| `frontier` | staging generation, raw path, active file-state/binding reference | Latest checkpoint map, copied from prior published frontier; never reconstructed by replaying all events. |
| `occurrence` | occurrence ID, commit/definition references, raw path, blob/type/mode | Materialized only for referenced seeds, links, events, or departures. Exact other occurrences are synthesized from membership and file state. |
| `lineage_link` | decision ID, context, parent/commit OIDs, old/new occurrence references, status, method, reason, evidence record | Status accepted/candidate/rejected/ambiguous. Partial unique indexes enforce at most one accepted outgoing/incoming endpoint on an edge within context. |
| `generation_link` | generation, decision reference | Associates reusable decisions only with generations containing the exact edge. |
| `event` | event ID, EID reference, parent/commit OIDs, nullable source/destination occurrence references, flag set, material bool, optional accepted decision | Source is at actual parent, destination at commit; at least one endpoint. Index `(EID, commit OID)`. |
| `generation_event` | generation, sequence, event reference | Range index `(generation, sequence, event)`; history index via EID join. |
| `departure` | generation, sequence, EID, old occurrence, status removed/unknown, reason | Terminal in that generation for an EID; no later present binding of the same EID. Index `(generation, EID, sequence)`. |
| `coverage` | generation, sequence/path or edge selector, category, reason, omitted count/count-known | Indexed by timeline/path; records extraction gaps, limits, unavailable objects, and matching omissions. |

Source paths use BLOB equality on every OS. A synthetic module definition has no
enclosing definition; other containment references are resolved parent-first
within a completed parse. In each builder transaction, materialize seed
occurrences before their lineage rows, then bindings, links and events so foreign
keys need not be disabled to create the initial state.

Name resolution uses indexed full
literal names plus separately indexed terminal names; presentation escaping MUST
NOT alter stored keys. An index on current frontier name/EID associations or an
equivalent indexed join MUST support point resolution without scanning history.
Historical name lookup joins definition/binding/file-state membership, not only
events. Local acceleration tables are rebuildable and verified against these
relations; they MUST NOT define independent identity truth.

Published generations retain their own membership and file-state rows. New
generations initially copy the prior scope rows with bounded `INSERT SELECT`
operations, then extend/close intervals. Versions, parses, binding sets, links,
and events are shared immutable records. This deliberately simple copy-on-build
strategy costs retained-generation storage, not a per-entity-per-commit snapshot.
Report that cost; do not imply it is constant. Explicit GC controls retention.

For one generation, expected logical growth is proportional to commits, initial
tree size, changed-file states and their entity bindings, stored candidate edges,
and events. Editing a large file may rewrite all of its bindings. The plan makes
no fixed bytes-per-commit promise. A raw-unchanged function in a changed file gets
new location state even when no material event is written.

### 3.6 Scope, Boundary, and State Algorithms

**Scope creation.** `--scope NAME` selects a local scope; `--ref REV` is a build
input, not identity. The default name is `default`. A scope name is explicit
metadata and MUST NOT be inferred from the currently checked-out branch after
creation. The default build ref is `HEAD`, resolved once to a committed OID.

1. Reject working-tree/staged pseudo revisions, trees, blobs, unresolved names,
   and an unborn `HEAD`. Resolve annotated tags to commits with a commit guard.
2. Walk actual first-parent edges backwards from the pinned tip, without dates,
   ref traversal order, or all-DAG topological numbering. Reverse that chain for
   forward building. Store every actual parent OID independently.
3. Without `--since`, stop at the actual root or a declared shallow/missing-parent
   boundary. Check Git shallow metadata; lack of an available parent is not proof
   of a root. With `--since COMMIT`, that exact commit MUST be on the selected
   first-parent chain and is an inclusive observed anchor, not a date filter.
4. Enumerate the anchor's complete tree in bounded pages before exposing state.
   At a true root with complete extraction, seed `eid1_` plus `added` events
   against an object-format-correct virtual empty tree. A bounded/shallow/missing
   anchor seeds `oeid1_` and `observed` events, never invented genesis events.
5. Assign sequence zero to the anchor and persist contiguous validated membership.
   File coverage can be partial; an omitted timeline interval cannot be hidden.

An explicitly requested older boundary that cannot be reached is
`boundary_unavailable`, not permission to start elsewhere. Git replacements and
grafts are outside this contract: index object access MUST disable replacements
and reject active graft interpretation, rather than silently changing parentage.

**Continuation.** Let the previous published tip be `P` and requested tip `T`.
A continuation exists only if walking first parents from `T` reaches `P` without
a gap and with the same anchor/context. `merge-base --is-ancestor P T` alone is
insufficient: `P` may be reachable only through a second parent. Load the saved
frontier from the published checkpoint, then process only the suffix after `P`.
The initial anchor is not reparsed or rematched. An identical request is a no-op
with zero parsing and zero new semantic rows.

Different scope names MAY follow branches with a common prefix. They share EIDs
only where canonical anchor, policies, provenance, and accepted prefix are
identical. They remain independent timelines. Build order and shared deduplication
MUST NOT make one branch's death or rename visible on another. A noncontinuation,
shorter requested history, anchor change, or interpretation change requires an
explicit `--rebuild`; no truncation of the previous published generation.

**Commit transition.** For each validated edge `P -> C`:

1. Enumerate raw changed tree entries; renames can be represented as delete/add.
   Git rename hints are optional evidence, never the authority for EIDs.
2. Copy unchanged path/blob/type/mode state by interval extension. For any changed
   blob, parse the whole file, reusing a compatible `(blob, path, parse_fp)` result
   when available. A mode-only change reuses definitions but changes occurrences.
3. Form complete eligible source/destination populations, including retained
   entities in unchanged files, through persisted frontier/bucket lookups. Do not
   load an entire repository's parsed trees into memory to count duplicates.
4. Apply the strict policy below, produce a new binding set for every changed
   complete file, and close/open file intervals. Absence of a path creates a
   tombstone. Parse/encoding/object failures create unknown file intervals.
5. Write seeds, decisions, departures, and independently classified events.
   Persist frontier, coverage, and membership together; no row may describe a
   commit beyond the checkpoint that owns its state.

**State lookup.** First resolve an exact OID to `(generation, seq)` in the selected
scope. If it is not a member, return `outside_scope`, even if another scope knows
that OID. For a file use the sole interval with `from_seq <= seq < to_seq`, or an
open upper bound. For an EID, join its bindings to active file intervals. A
present definition yields an occurrence for the requested OID, including the
current blob and original byte bounds, even when no event exists at that OID.

| State | Required interpretation |
| --- | --- |
| `present` | Exactly one active binding in the selected generation at the queried OID. |
| `removed` | No binding and a proved structural removal departure at/before the query. It is not a missing-parse inference. |
| `absent` | Complete scoped evidence establishes no binding, including before a known genesis. No removal is implied. |
| `unknown` | Coverage gap, unresolved predecessor, observed-boundary limitation, or unavailable evidence prevents a negative conclusion. Include reason and last known occurrence. |

An EID from another context is `identity_outside_scope`, not `absent`. Before an
observed seed, state is `unknown`. A parse gap closes the old chain with unknown
departure; later parse recovery can seed a new observed chain but MUST NOT imply
deletion and rebirth. Returning to identical bytes after a known removal creates
a new EID. Historical aliases are search entries, not lineage reconnection.

At a merge, compare only first parent to merge commit. Source/destination events
show the aggregate first-parent effect, including octopus merges. Side-parent
commits and their identity chains MUST NOT be invented or imported. New merge
entities lacking first-parent predecessors are observed introductions with
`side_parent_origin_unresolved`; this is not proof they were born at the merge.

### 3.7 Strict Identity Policy

The initial policy is `strict-v1`. It is distinct from the legacy diff matcher;
existing `diff_trees` and `reconcile_moves` remain available for display. The
builder consumes evidence records, not their single `ChangeKind` conclusions.

Every explicit link stores exact edge/endpoints, status, method, reason, policy,
compared representation digests, duplicate counts, eligible population scope,
coverage restrictions, and any omitted candidates. Hash equality is a lookup
accelerator; compare full canonical representations before accepting equality.
There is no numeric probabilistic confidence field. A Jaccard ratio, when
present, is a similarity measurement with numerator/denominator, not confidence.

Evaluate tiers in the order below. Freeze each tier's source and destination
candidate graph before assigning anything. Accept only edges satisfying that
tier's guards and one-to-one uniqueness; no greedy first-best tie breaker. Sort
by canonical bytes only for reproducible reporting, never to break an identity tie.

| Tier | Acceptance contract | Mandatory refusal |
| --- | --- | --- |
| 0: unchanged file | Same raw path, blob, type, mode, parser, and prior bindings carry forward. State intervals provide implicit `unchanged_file` evidence; no per-commit links needed. | Prior unknown population or mismatched interpretation. |
| 1: structural locator | Same kind, language, raw path, literal name, and already accepted enclosing identity; sibling group cardinality exactly one on each side. The synthetic module at the same path can continue after a complete parse. | Duplicate names/ordinals, kind change, uncertain parent, or copy/swap evidence. This is declared structural continuity, not semantic proof of a rewrite. |
| 2: exact distinctive representation | Unmatched source no longer occupies its old locator; destination is unmatched; same language/kind/role. Full own equality, then full body equality for leaves, guarded below. Unique full subtree equality is required for nonmodule containers. | Incomplete eligible population, retained/copy source, duplicate equivalent representation on either full side, stub body, competing eligible edge, or unavailable full representation. |
| 3: suggestions only | Remaining same-kind candidates use full normalized token shingles of width 3 and Jaccard threshold 0.6, stored as exact rational measurements. | Never accepted in v1, even with a unique highest score. Ties/competing alternatives are `ambiguous`; other eligible suggestions are `candidate`. |

`MIN_BODY_TOKENS` is initially 3 nontrivia body tokens. It guards every tier-2
leaf relocation, including the own-equality path, not merely the body-hash path.
It is a conservative heuristic, not a distinctiveness guarantee. Tier-2
containers require at least 3 nontrivia full-subtree tokens and complete
subtree evidence; container own hashes cannot establish a subtree match.

Same-named sibling groups bypass tier 1 entirely. Tier 2 MAY continue a uniquely
distinguishable member by complete evidence despite ordinal insertion/reordering;
identical siblings stay ambiguous. There is no positional pairing or fallback
to equal `#N` slots. Tier 1 MUST refuse when the old distinctive representation
also appears at another destination locator, or the new representation appears
at another retained source locator, indicating a possible copy/swap. All such
guards operate on full before/after populations, not pools after prior matches.

The synthetic module is file-bound. A moved module can continue only under a
unique exact full-blob delete/add pair with matching mode/type and complete
coverage; the source path MUST be absent and the blob unique in both full file
populations. Git's rename score alone cannot establish it. When this guard fails,
module lineage is unresolved; independently distinctive leaf relocation may
still be accepted with explicit enclosing-location change evidence.

Accepted relocation implies one old occurrence and one new occurrence; one EID
MUST occupy at most one location at any scoped revision. One-to-many copies and
many-to-one merges MUST NOT yield accepted links. A removed source plus two
identical additions is ambiguous, as is a retained source plus an identical
new stub. Kind changes are `ambiguous(kind_changed)` candidates, not continuation.

An unmatched destination receives a new seed. It is `eid1_` only when root-based
context and complete predecessor evidence establish a new structural addition
with no unresolved competing links; otherwise it is `oeid1_`. An unmatched old
binding is `removed` only with complete relevant before/after coverage, no
unresolved matching candidate, and no merge-side uncertainty; otherwise its
departure is `unknown(identity_unresolved_or_coverage_gap)`. This can miss true
continuations; it MUST expose that limitation instead of claiming certain death.

Unsupported languages are outside the eligible population and remain explicit
coverage exclusions. A parse failure in a supported compatible language makes
relocation uniqueness unprovable and disables that acceptance tier on the edge.
Already established unchanged/same-locator continuations in other complete files
need not be discarded. Report the guard's population and exclusions; never claim
global uniqueness across unsupported languages or logical programs.

`rejected` stores a bounded considered pair with a concrete guard failure; it
does not mean every possible pair was enumerated. Limits emit omitted counts or
`count_known=false`. No candidate is promoted by a query or by editing a database
row. Future promotion requires a new explicit policy/override provenance design
and a newly verified generation. There is no promotion command in this release.

### 3.8 Event Classification

For each accepted transition compare fields independently. `renamed` compares
literal local names; `moved` compares path or enclosing identity;
`signature_changed` compares full signature representations and `body_changed`
compares full owned-body representations, not displayed text. Own material not
covered by either representation uses an explicit `own_content_changed` flag;
it MUST NOT disappear merely because both narrower comparisons were unchanged.
`descendant_changed` compares complete subtree material while own material is
unchanged. `mode_changed` compares Git modes. `cosmetic` requires equal available
own/signature/body/subtree representations but different exact slice bytes.
If a required representation is unavailable, emit `representation_unknown`, not
equality. Structural differences do not establish caller breakage or safety.

Coordinate-only shifts produce new occurrences/file bindings and no change event
for the shifted entity. Changes only to a container's descendants update its
version/occurrence and MAY produce a nonmaterial `descendant_changed` event;
the initial implementation MUST emit that event consistently. Do not relabel it
as a body change or count it as an extra material change to the container.

Seeds use `added` for established genesis or `observed` for limited evidence;
departures use `removed` or `lineage_gap`. A gap is not deletion. A transition can
carry rename, move, and body/signature flags simultaneously. An optional legacy
presentation kind is derived with precedence `added`, `removed`, `renamed`,
`moved`, `signature_changed`, `body_changed`, `cosmetic`; it is never the stored
truth and is absent if only new nonlegacy flags apply. Legacy APIs are unchanged.

### 3.9 Lifecycle, Recovery, and Resource Bounds

Locate storage under `<git-common-dir>/gita/index.sqlite`. Prefer a supported
`rev-parse --path-format=absolute --git-common-dir`. Otherwise resolve the result
against the actual working directory used for that Git invocation, not blindly
against a toplevel directory. Linked worktrees share this storage; repository
subdirectory invocation and bare repositories MUST work. No path is joined to a
working-tree filename to read indexed source.

Use NUL-safe raw path enumeration and length-framed commit/object parsing.
Read blobs by OID through the existing batching abstraction extended additively,
not `rev:path` newline requests. A commit message can contain old record markers;
a Git path can contain tabs, newlines, quotes, backslashes, and invalid UTF-8.
Preserve path bytes, with base64 plus optional strict display decoding in JSON.
Symlinks and gitlinks are recorded by blob/commit OID, type, and mode and are
never dereferenced. Git errors MUST NOT be translated into empty file lists.
Local builds MUST NOT fetch missing objects automatically, including partial
clone lazy fetch; disable that behavior for indexed object access.

**Writer lifecycle.** Configure each connection in autocommit mode, set
`foreign_keys=ON` outside any transaction, and read back `1`. Configure WAL on
initial creation, verify it on write-open, use `synchronous=FULL`, and bound the
busy wait to 2 seconds. Readers use read-only open and a read transaction; they
MUST NOT create an absent database or run journal-mode-changing pragmas.

Use one SQLite `BEGIN IMMEDIATE` transaction for an entire build invocation,
acquired before reading the checkpoint that will be extended. This is the sole
writer lock, across all scopes. Stream bounded processing batches inside that
transaction; batches do not commit. No PID lock file, stale-PID deletion, or
unlocked interval between checkpoint read and publication is allowed.

Stage a new generation, write bounded batches, run relational/invariant checks,
and atomically switch the scope's published generation at commit. An exception,
cancellation, process death, or failed verification rolls back the invocation.
The saved checkpoint is the previous published generation, not an uncommitted
256-commit batch. A restart reloads that frontier and repeats only the uncommitted
suffix. An interrupted first build has no published generation. This deliberately
trades finer-grained crash resume for a simpler, auditable writer protocol.

WAL readers retain consistent snapshots; this is not a promise of zero blocking.
Checkpointing, schema operations, filesystem locks, and a second writer may
return typed busy outcomes. A ref that moves during a build does not change the
pinned target: finish against that OID, compare the ref at the end, and report
`ref_moved` with observed start/end OIDs. Never silently continue to the new tip.

**Limits.** Initial defaults below are engineering bounds, not measured capacity
claims. Deterministic evidence limits belong in fingerprints. Operational limits
abort without publishing semantics that depend on machine speed.

| Bound | Initial value and behavior |
| --- | --- |
| Source blob size | 5 MiB; larger file receives `oversized`, byte count, and unknown child population. |
| Definitions per file | 50,000; exceeding yields whole-file `definition_limit`, not a misleading prefix of authoritative children. |
| Derived normalized representation per file | 32 MiB; exceeding yields `representation_limit`; source remains in Git. |
| Parse working set | One active file, one bounded result, at most 32 MiB reusable parse LRU; no repository-wide in-memory EntityTree map. |
| Tree/frontier/database page | At most 1,000 rows; write batch at most 256 commits or 16 MiB derived records, whichever arrives first. One oversized bounded record is handled alone. |
| Candidate work | At most 64 suggestions per destination and 10,000 considered fuzzy pairs per edge, in canonical order. Exact population counts are indexed aggregates; duplicate buckets need not be expanded. |
| Query page | Default 20, maximum 1,000 records, reduced by token budget; never fetch all events before truncating. |
| Busy wait | 2 seconds; return `busy`, never corruption or exit-zero success for a refused writer. |
| Parse deadline | 5 seconds per file in a bounded worker; terminate/reap on timeout and abort invocation with `resource_limit`. A timeout MUST NOT mint a different identity. |
| Active cache storage | 10 GiB database plus WAL operational ceiling, checked between bounded batches; abort/rollback with observed size and GC guidance. Retained generations count toward it. |

The parse worker exists for cancellation/deadline isolation, not a parallel
throughput claim. File byte/definition limits do not prove a bound on native
parser RSS: measure peak process-tree memory and gate runaway cases in F4.
Changing deterministic limits requires a new interpretation and rebuild.
Changing page size or busy timeout MUST NOT affect logical output. If accepted
matching cannot be decided completely within its bound, refuse that link and
report matching coverage; do not use truncated counts as uniqueness evidence.

**Read and recovery states.** Status distinguishes `not_built`, `ready`,
`partial`, `behind`, `ref_moved`, `schema_mismatch`, `parse_mismatch`,
`match_mismatch`, `busy`, `permission_denied`, `io_error`, and `corrupt`.
`ready` concerns validity; coverage and freshness remain separate fields.
Only validated SQLite corruption classes/check failures mean `corrupt`.
Missing tables under a different declared schema are not auto-classified as
corruption; busy, disk-full, read-only, and permissions are separate errors.

Run `quick_check` plus `foreign_key_check` on explicit verification, initial
creation validation, and before publication after abnormal recovery. Do not
infer integrity from file mtime versus an internal timestamp. Full invariant
verification is separately defined below. No read silently rebuilds or deletes
data. For corruption, preserve files and report explicit offline repair/rebuild
steps after all handles close. Never delete/replace an active DB, WAL, or SHM.

`--rebuild` on a readable current schema stages a new generation in the same
database. Incompatible physical schemas or corrupt files require a separately
built store and an explicitly offline replacement procedure; no online swap is
claimed in v1. Keep the old store quarantined, not automatically erased. Explicit
generation GC uses a writer transaction, removes only requested nonpublished
generation rows, and then unreachable immutable rows. Existing WAL readers can
finish their snapshots; a later cursor targeting collected data returns
`generation_gone`. GC never touches Git objects or source files.

### 3.10 I/O and CLI Contracts

All commands in this section are PROPOSED. They are not currently available.
Indexed APIs require a selected scope and return typed results, not `None` for
every error. Query-open is read-only. No ID or history read can trigger indexing.

| Proposed API | Result and exact scope |
| --- | --- |
| `build(repo, scope, ref, since=None, rebuild=False, limits=...)` | Build result with pinned tip, generation, fingerprints, boundary, coverage, counters, ref movement, and typed errors. One ref/tip per call. |
| `open_index(repo, scope, generation=None)` | Read session pinned to a published generation or typed failure. |
| `resolve(token, at=None, historical=False, cursor=None, limit=20)` | Candidate EIDs and occurrence/lifecycle metadata at exact scope member; no selection by rank. |
| `state_at(eid, oid)` | Present/removed/absent/unknown plus evidence; `outside_scope` is an error. |
| `history(eid, at=None, cursor=None, limit=20, order="newest")` | Events through requested scope member, with decisions and coverage gaps. |
| `events_in(base_oid, head_oid, cursor=None, limit=20)` | Events on `(base, head]`, both members of selected scope, ordered by local sequence; reject reversed ranges. |
| `entities_in(raw_path, oid, cursor=None, limit=20)` | Exact occurrence page plus file coverage, not a guessed empty list on failure. |
| `source(occurrence_ref)` | Validate a returned occurrence record against its ID, definition and scope membership; return exact original bytes or `source_unavailable`, never current worktree text. |
| `verify(generation, source_check=False)` | Bounded invariant findings with IDs/edge/path and pass/fail/unverified-source status. |

`occurrence_ref` contains the full occurrence ID, generation, commit OID, raw
path, blob/type/mode and definition ID returned by a query. Byte bounds are
verified against that definition. The requested commit/path/blob/type/mode and
definition MUST also agree with the active file state in the pinned generation;
a self-consistent digest alone does not prove that this file existed at that
commit. This explicit record is required for a
synthesized occurrence: its digest alone cannot be inverted and readers MUST
NOT persist new rows just to make source lookup work. Stored seed/event/link
occurrences MAY also be resolved by ID through the pinned read session.

Proposed command grammar:

```text
gita index [--scope NAME] [--ref COMMITISH] [--since COMMITISH] [--rebuild] [--json] [--budget N]
gita index --status [--scope NAME] [--json] [--budget N]
gita index --verify [--scope NAME] [--generation ID] [--source-check] [--json] [--budget N]
gita index --gc --generation ID [--scope NAME] [--json] [--budget N]
gita id TOKEN [--scope NAME] [--at COMMITISH] [--historical] [--generation ID] [--cursor TOKEN] [--limit N] [--json] [--budget N]
gita history TOKEN --indexed [--scope NAME] [--at COMMITISH] [--generation ID] [--cursor TOKEN] [--limit N] [--order newest|oldest] [--json] [--budget N]
```

Action flags for `index` are mutually exclusive; build is the default action.
Build-only flags MUST be rejected on status/verify/GC. `--source-check` requires
verify; GC requires one explicit nonpublished generation. Reuse existing global
repository selection options; do not introduce a second repository locator.
Without `--at`, indexed queries target the selected generation tip, not current
working-tree `HEAD`. Explicit `--at HEAD` resolves HEAD once and checks membership.
Any existing history revision arguments MUST either map unambiguously to these
semantics under `--indexed` or be rejected as incompatible options, never ignored.

Resolution order is typed EID prefix, exact path-qualified locator, then exact
live terminal/qualified name. With `--historical`, the locator/name tier being
queried is the union of live and historical bindings observed at/before the
selected OID, deduplicated by EID; a reused name can therefore return both the
retired and the new lineage. A historical-only match is marked as such and
includes its current lifecycle state; it does not resurrect that lineage. At
each tier, more than one candidate is ambiguity, not permission to try a lower
tier. Full raw
path lookups are available in the typed API; CLI locators use strict UTF-8 and
report `unsupported_path_input` when they cannot express an exact byte path.
Such occurrences remain addressable by full opaque ID.

EID input requires the `eid1_` or `oeid1_` prefix. Abbreviations contain at least
8 hex characters and are checked against the entire selected generation's
identity population, including removed/observed chains. Default output and JSON
use full IDs. Ambiguous prefixes return candidates with full IDs and never pick
the first. Prefixes are convenience handles, not stable references across builds.

The five-commit acceptance freezes the following event shape for F3:
`results[]` entries have `eid`, `material`, `flags`, `commit`, `parent`,
`source` and `destination`. Git OIDs are objects with `object_format` and a full
lowercase `hex` digest. Root parent/source are `null`. Each nonnull occurrence
includes `id`, `commit`, `path_bytes_b64`, `names` as literal components, and
`span` with start/end byte and start/end line fields. Remaining source-reference
fields required below are additive. Function byte spans exclude trailing file
newlines outside the parsed node; source bytes, not file-length guesses, decide
the bounds. A commit hash in unrelated metadata does not satisfy this contract.

Every successful JSON response uses an envelope with these required fields:

| Field | Contract |
| --- | --- |
| `schema_version`, `command`, `status` | Versioned response schema, exact action, typed status. |
| `scope`, `generation` | Scope local name/opaque ID, first-parent mode, published generation ID. Nullable only when unavailable. |
| `revision` | Full requested/resolved/pinned commit OIDs with object format; no abbreviated committed references. |
| `boundary`, `policy` | Anchor, boundary kind, missing/omitted ancestry; parse/match/context fingerprints. |
| `refs`, `freshness` | Input ref diagnostic, pinned OID, optional current resolved OID, comparison status or `unchecked`. Ref checks must not change results. |
| `coverage` | Commit range, file outcome counts, excluded languages/types, object/parse gaps, candidate omissions, working tree `out_of_scope`. |
| `results` | EID, occurrence/source/destination, lifecycle, flags, and matching status/reason/evidence as applicable. Paths have `path_bytes_b64` and nullable display string. |
| `page`, `omitted` | Count returned, next cursor, truncation reason, omitted count or explicit unknown count. |
| `error` | Null or typed code, concise message, retryability, and recovery action; never a success-shaped empty answer for a failed query. |

Error envelopes carry the same fields with explicit null/unknown values where
unavailable. No claim of complete coverage may be omitted for space. Budget the
serialized final output with the existing exact token counter, reserving metadata
and omission notice first. Never truncate serialized JSON or drop an ambiguous
status to make one candidate fit. If even the minimal complete error envelope
cannot fit, write zero stdout bytes, emit a concise stderr diagnostic, and return
code 6. This is the documented tiny-budget exception to JSON envelopes.

| Exit | Proposed new-command/indexed meaning |
| --- | --- |
| 0 | Completed answer or diagnostic status, including a truthfully labeled partial build/status. Status `not_built` is a successful diagnostic. |
| 1 | No match, ambiguous resolution/prefix, or a resolved absent/removed identity where the requested operation requires a live match. |
| 2 | Invalid syntax/options/revision, pseudo revision, outside-scope OID, incompatible cursor/range, or boundary unavailable. |
| 3 | Index required/not built, stale interpretation/schema, rebuild required, or generation collected. |
| 4 | Busy/resource limit, permissions, I/O, or required source unavailable; `retryable` distinguishes temporary conditions. |
| 5 | Corruption, digest collision, or verification invariant failure. |
| 6 | Output budget too small to report a valid answer. |

These additions MUST NOT renumber legacy command exit codes. Progress belongs
on stderr, results on stdout. Query APIs preserve distinctions even where CLI
exit codes are grouped. Unknown/partial successful answers remain explicit in
`status` and `coverage`, never silently equivalent to complete absence.

Cursor encoding is versioned base64url canonical data containing generation,
scope/context, queried OID/range, action, EID/filter, order, and last full sort
key. Validate all fields, size (maximum 4 KiB), and membership before use. History
sorts by `(sequence, event ID)` in the requested direction; entities/matches use
raw path, byte start, kind, full ID. A page captures one read transaction. A later
page uses the same immutable generation after builds, or `generation_gone`
after explicit GC, never silently switches to the new tip. No offsets dependent
on mutable row order and no SQL supplied by cursors.

## 4. Requirements

Every row is normative and traced by an acceptance row in section 7.

| ID | Requirement |
| --- | --- |
| FR-001 | Core builds and queries MUST use local committed Git objects only; dirty/staged/untracked state MUST be excluded and declared. |
| FR-002 | Versions, definitions, occurrences, decisions, and events MUST retain the distinct semantics and exact byte/source evidence in section 3. |
| FR-003 | IDs MUST use typed, domain-separated canonical encoding and collision checks; stability MUST exclude ref/time/allocation order and include documented provenance. |
| FR-004 | Root genesis and observed seeds MUST use different namespaces; missing history or parse evidence MUST NOT become fabricated birth, deletion, or aliases. |
| FR-005 | Scope membership MUST follow actual first-parent edges, isolate branches, and reject nonmember OIDs. |
| FR-006 | Historical state MUST resolve current blob/location through file intervals and bindings, independently of material events. |
| FR-007 | Acceptance MUST use the strict tiered one-to-one policy, including retained-population duplicate/copy guards and no ordinal/greedy fallback. |
| FR-008 | Candidate, ambiguous, rejected, and accepted links MUST expose methods, reasons, policy, and omissions; fuzzy candidates MUST NOT be promoted automatically. |
| FR-009 | Events MUST preserve independent flags and exact parent-edge endpoints, with separate cosmetic/descendant/gap classifications. |
| FR-010 | Known removal, absence, unresolved lineage, and later reintroduction MUST remain distinguishable. |
| FR-011 | Initial checkpoints MUST inventory every tree entry with explicit coverage; unsupported/error/empty/missing results MUST remain distinct. |
| FR-012 | Incremental builds MUST reload the saved frontier and validate first-parent continuation; repeated and incremental builds MUST equal full logical exports. |
| FR-013 | Builds MUST pin inputs and atomically publish generations; readers/cursors MUST not drift with ref movement or rebuilds. |
| FR-014 | Separate parse and match fingerprints MUST invalidate affected records without retaining wrong semantics or silently changing old IDs. |
| FR-015 | APIs/CLI MUST implement commit/scope guards, live versus historical resolution, full IDs, prefix ambiguity, and explicit not-built outcomes. |
| FR-016 | Responses MUST carry coverage/provenance/lifecycle and bounded pagination, including tiny-budget and omitted-detail behavior. |
| FR-017 | Existing diff, default history, legacy Entity IDs/hashes, and public return/exit contracts MUST remain compatible; indexed history MUST be opt-in. |
| FR-018 | Verification MUST detect relational, identity, temporal, source, and publication violations with actionable record identifiers. |
| NFR-001 | SQLite MUST enforce foreign keys on every connection and one bounded-wait writer; snapshots and atomic publication MUST survive interruption. |
| NFR-002 | Corrupt/busy/permission/I/O/schema/interpretation conditions MUST be typed; automatic destructive recovery is forbidden. |
| NFR-003 | Memory, per-file work, candidate growth, output, and storage MUST be bounded or measured with truthful partial/failure outcomes. |
| NFR-004 | Paths, SHA-1/SHA-256, bare/subdirectory/linked-worktree layouts, symlink/gitlink modes, and hostile Git records MUST be handled without lossy identity. |
| NFR-005 | Identity gates MUST measure precision and recall on independently labeled real refactors separately from synthetic fixtures. |
| NFR-006 | Query latency, build throughput, storage, and peak memory MUST be reported by a reproducible protocol; unmeasured targets MUST remain unverified. |
| NFR-007 | Delivery MUST be test-first with discriminating local failures, independent verification, and no unrelated production refactoring. |

## 5. Risk Classification

**Risk: HIGH.** A plausible false continuation contaminates every later history
answer. Deterministic wrongness is still wrong; performance cannot compensate.

| ID | Failure or assumption | Mitigation and acceptance consequence |
| --- | --- | --- |
| FM-001 | Hash/locator equality fuses copies or unrelated rewrites. | Expose structural policy; whole-population guards; hard negative fixtures; empirical precision gate. Same-locator replacement cannot be universally distinguished. |
| FM-002 | Missing parse/history appears as deletion or genesis. | Explicit unknown intervals and observed namespace; reject inferred certainty; verify boundary provenance. |
| FM-003 | Global sequence or second-parent ancestry contaminates branch state. | Generation-local membership and actual first-parent continuation tests. |
| FM-004 | Shifted lines or descendant edits leave stale locations/versions. | Changed-file full parse and separate occurrence/state storage. |
| FM-005 | Crash, second writer, or ref movement mixes generations. | Full writer transaction, pinned OIDs, read snapshots, atomic pointer update. |
| FM-006 | Busy/disk-full misclassified as corruption causes data deletion. | Error-class mapping and preservation; offline explicit repair. |
| FM-007 | Native parser or large changed files exceed working-set assumptions. | Per-file deadlines/caps, bounded worker, process-tree memory measurement; no capacity claims before results. |
| FM-008 | Conservative matching misses rename-plus-edit and realistic refactors. | Keep candidate evidence; measure recall honestly; narrow the release claim if the gate fails. |
| FM-009 | Retained generation copies or long writer transactions grow WAL/storage. | Storage ceiling, explicit GC, writer busy contract, measured retained-generation costs. |
| FM-010 | Test-generated fixtures are labeled as real evidence. | Freeze a separate real-refactor corpus and independent labels before scoring. |
| ASM-001 | First-parent structural history is a useful first release. | Explicit limitation in every answer; no claim of imported side-parent identity. |
| ASM-002 | SQLite WAL and reliable local filesystem locking are available. | Validate filesystem behavior; shared/network filesystem operation is unsupported until qualified. |
| ASM-003 | Grammar evidence can be extended without changing legacy output. | Additive adapter tests and legacy regression fixtures are release gates. |

## 6. Dependencies

| ID | Dependency | Contract |
| --- | --- | --- |
| DEP-001 | [Python/tool dependencies](../../pyproject.toml) | Python >=3.11, built-in SQLite, tree-sitter/language pack, and existing exact tokenizer; no model/network requirement. |
| DEP-002 | Git executable and object database | Capability-check absolute common-dir lookup and SHA-256 support; safe fallback for older Git. NUL-safe enumeration and original object access are mandatory. |
| DEP-003 | WS-1 enumeration | Preserve implemented legacy behavior; new index ingestion can use additional byte-safe plumbing without reverting WS-1. |
| DEP-004 | F0 frozen records and vectors | Store and evidence work share agreed canonical types, statuses, keys, and test vectors before parallel implementation. |
| DEP-005 | Existing test infrastructure | Reuse [fixtures](../../tests/conftest.py), [enumeration tests](../../tests/test_enumeration.py), and existing store/extractor/CLI tests for touched legacy behavior. |
| DEP-006 | Independent corpus and benchmark | Pinned local repositories, labeled actual refactors, recorded permissions/provenance, and a >=10,000-first-parent-commit benchmark. Missing fixtures mean unverified gates. |

No new mandatory package is specified until a concrete implementation need is
demonstrated.

## 7. Quality & Testing

### 7.1 Test-First Rules

F0 first adds `refactor_repo` to the existing fixture home and the five-event
acceptance in the proposed builder test module. It is initially red with a clear
not-implemented failure, not marked skipped/xfail. That broad failure is a delivery
marker; each subsequent unit cycle MUST fail for the particular missing rule it
introduces, not merely the absence of the entire index package.

Write one narrow failing test, implement the owning slice, rerun that test, then
run touched compatibility tests. Record expected failure reasons. Existing
regression guards may start green and MUST be identified as substitution guards,
not falsely counted as new red tests. Build actual Git fixtures under `tmp_path`;
simulate failures at explicit boundaries, not with sleeps or timing races.
Keep proposed test groups in four new index modules rather than a file per case.

The sequence below specifies the full delivery test matrix. Implemented F0
tests and their observed results are recorded in section 14; later foundation
behavior remains unimplemented even when its acceptance test has been written.

### 7.2 Ordered Test Matrix

| ID | Phase and proposed test home | Given / action / required result; discriminating failure |
| --- | --- | --- |
| TEST-001 | F0/F2, `tests/test_index_builder.py` | `refactor_repo` has five committed function mutations with a distinctive body. Build from true root, close/reopen, query final function: exactly one EID and five material events oldest-first, original birth path/name and final move/edit. Initially not implemented; later fails on broken continuation, not module-event counts. |
| TEST-002 | F0, `tests/test_index_store.py` | Independent canonical vectors for every ID domain, null/empty, lengths, UTF-8, SHA formats. Perturb each identity input; ref/time/row order changes do not alter EIDs. Deliberate digest collision injection fails closed. |
| TEST-003 | F1, `tests/test_index_store.py` | Create/open/reopen round-trip; absent read does not create a file; FK pragma readback is 1 before transactions and orphan insertion fails on reopened/read connections where applicable. |
| TEST-004 | F1, `tests/test_index_store.py` | DDL rejects duplicate membership, overlapping intervals through writer validation, invalid references, wrong byte bounds, conflicting bindings and accepted endpoints; valid rows round-trip with BLOB byte equality. |
| TEST-005 | F1, existing extractor tests plus `tests/test_index_builder.py` | CRLF/BOM/UTF-8, nested entities, final/no-final newline and empty file: exact byte slices/digests/lines, full untruncated tokens, own versus complete subtree. Replacement decoding cannot pass; a synthesized occurrence reference fetches verified source without read-side writes. |
| TEST-006 | F1/F2, `tests/test_index_builder.py` | Broken syntax, unsupported language/encoding, large file, excessive definitions/representation and missing blob each yield the named coverage outcome; no salvaged child certainty or silent omission. |
| TEST-007 | F2, `tests/test_index_builder.py` | Insert lines before an unchanged function: same version/EID, different blob/bounds and occurrence, no material event; historical lookup before/after returns exact spans. |
| TEST-008 | F2, `tests/test_index_builder.py` | Edit only a method: class/module own hashes stay equal but complete subtree/version changes; descendant-only events are nonmaterial and no false cosmetic/body assertion. Change own material outside signature/body and assert `own_content_changed` is retained as material. |
| TEST-009 | F2, `tests/test_index_builder.py` | Unique same-locator body/signature edits continue structurally. Rename, move, and rename-plus-move with distinctive exact evidence continue; flags are independent. Mutation without the tier guard makes this fail. |
| TEST-010 | F2, `tests/test_index_builder.py` | Identical stubs, retained-source copy, deleted source/two additions, swapped copies, and ambiguous same-name methods never fuse. Duplicate counts include unchanged files and MIN_BODY applies to both exact tiers. |
| TEST-011 | F2, `tests/test_index_builder.py` | Insert/reorder duplicate-name siblings: uniquely distinguishable members can follow evidence, identical members cannot follow ordinals or position. Parent uncertainty blocks unsupported locator inheritance. |
| TEST-012 | F2, `tests/test_index_builder.py` | Equal-score and unique-score fuzzy proposals remain ambiguous/candidate; kind change, many-to-one and split cases never produce accepted links. Persist reason, rational score, and truncation metadata. |
| TEST-013 | F2/F3, `tests/test_index_query.py` | Proven deletion then identical reintroduction creates distinct EIDs; state is removed for old lineage, absent before known birth, and present only for new lineage. Historical names show both without alias union. |
| TEST-014 | F2/F3, `tests/test_index_query.py` | Complete -> parse gap -> complete produces unknown departure and new observed chain. Never claim removed/reborn; unavailable source after Git pruning differs from absent state. |
| TEST-015 | F2, `tests/test_index_builder.py` | True root, inclusive `--since`, shallow boundary, missing-parent boundary and later deepening: namespaces/provenance differ, original EIDs remain addressable in old generation, no aliases. |
| TEST-016 | F2/F3, `tests/test_index_builder.py` | Root/ordinary merge/octopus fixtures: exact first-parent edge effects, all parent OIDs retained, side-parent identity unsupported and nonmember OID rejected. Empty-tree handling uses correct object format. |
| TEST-017 | F2/F3, `tests/test_index_builder.py` | Two branches built in reverse order yield same logical shared-prefix EIDs and isolated deletions/aliases; equal/backdated timestamps do not order state. A global `latest <= seq` implementation fails. |
| TEST-018 | F3, `tests/test_index_builder.py` | Old tip is ancestor only through second parent: continuation refused until explicit rebuild. Linear continuation reloads checkpoint frontier with no prefix parsing/event replay. |
| TEST-019 | F3, `tests/test_index_verify.py` | Two fresh builds and every chosen incremental partition compare equal canonical logical exports, excluding local handles/times/row layout. Include boundaries and fixed coverage failures, not only clean linear fixtures. |
| TEST-020 | F1/F3, `tests/test_index_store.py` | Inject failure before/after logical batches and before publication: invocation rolls back, previous checkpoint survives, readers see old snapshot; restart equals clean build. No uncommitted batch is treated as saved. |
| TEST-021 | F1/F3, `tests/test_index_store.py` | Hold writer with a synchronization barrier: second writer returns typed busy within configured bound; reader sees old published generation. Cancel/error releases lock, no PID cleanup or WAL deletion. |
| TEST-022 | F3, `tests/test_index_query.py` | Ref moves mid-build and after first page: result pins original OID; later pages stay on original generation. Rebuild retains old cursor; explicit GC makes a fresh later page return generation_gone. |
| TEST-023 | F1/F3, `tests/test_index_store.py` | Grammar/encoding/rule change invalidates parses; match-only change reuses parses but recomputes lineage. Old generation never masquerades as current; changing runtime page size changes no logical export. |
| TEST-024 | F1/F3, `tests/test_index_store.py` | Inject malformed DB, wrong schema, lock, permission, full disk and I/O classes: exact typed status, files preserved, no autocreate/auto-delete/rebuild. Integrity uses explicit checks, not timestamp comparison. |
| TEST-025 | F1/F4, existing Git tests plus `tests/test_index_builder.py` | Main/linked worktrees invoked from nested directories and bare repo locate same correct common-dir where applicable; relative results resolve against invocation cwd. SHA-1/SHA-256, case-distinct Git paths, mode changes, symlink/gitlink are correct. |
| TEST-026 | F1/F4, existing Git tests plus `tests/test_index_builder.py` | Tabs/newlines/quotes/backslashes/invalid UTF-8 paths, record-like commit messages, empty blob versus missing object, replacement refs and missing promised objects cannot corrupt enumeration or trigger fetching. Missing/non-source outcomes never populate a parse key; later available bytes parse without a negative-cache collision. |
| TEST-027 | F3, `tests/test_index_query.py` | Current versus historical names, duplicate names, full typed IDs and colliding 8-hex prefixes: deterministic candidate sets and ambiguity; future aliases not visible at older OIDs. |
| TEST-028 | F3, `tests/test_index_query.py` | Queries at unchanged intermediate commits synthesize correct occurrence OIDs; ranges use `(base, head]`; absent/removed/unknown and out-of-scope errors are distinct. No Git history walk from point queries. |
| TEST-029 | F3, existing CLI tests plus `tests/test_index_query.py` | Proposed envelopes/exits, no-index id, commit guards, action conflicts, historical ambiguity, paging/truncation and tiny budgets. JSON is valid or stdout empty under documented code 6. Dirty state ignored without mutation. |
| TEST-030 | F3, existing CLI/history/diff tests | Default history/diff output and public return/exit behavior remain unchanged. Indexed path is explicit and does not fall back to legacy results on mismatch. Existing guards start green. |
| TEST-031 | F4, `tests/test_index_verify.py` | Inject one violation of each invariant below into isolated fixtures: checker identifies precise invariant/record/edge; healthy complete and honestly partial indexes pass structural verification. |
| TEST-032 | F4, `tests/test_index_verify.py` | Validate synthetic mutation labels and score perfect/null/known-false matchers; separately label actual refactors. Undefined denominators stay undefined; false positives/counts/CIs and failing thresholds are not rounded away. |
| TEST-033 | F4, `tests/test_index_verify.py` | Benchmark harness has frozen fixture/hardware/tool versions and 100-query warm distribution; report cold/startup separately. Missing benchmark means unverified, not pass. |
| TEST-034 | F1/F4, `tests/test_index_store.py` | Storage/page/candidate/deadline limits exercise refusal and omissions; timeout aborts publication, native child is reaped; GC cannot target active generation and never unlinks DB/WAL/SHM under readers. |

### Acceptance Criteria

| ID | Criterion | Verification | Traces To |
| --- | --- | --- | --- |
| AC-001 | Core operates offline against committed inputs with unchanged dirty/staged/untracked files and declared exclusion. | TEST-026, TEST-029; manual M-006 | FR-001 |
| AC-002 | Every complete definition's source slice, byte bounds, representation, and occurrence are exact and independently recoverable. | TEST-005, TEST-007, TEST-008 | FR-002, FM-004 |
| AC-003 | Canonical IDs reproduce across rebuild/order changes; collision injection fails and original provenance is recoverable. | TEST-002, TEST-017, TEST-019 | FR-003 |
| AC-004 | All incomplete genesis/gap cases remain observed/unknown and never alias into rooted identity. | TEST-014, TEST-015 | FR-004, FM-002 |
| AC-005 | First-parent members, merge effects and nonmember errors are exact; no branch contamination. | TEST-016, TEST-017, TEST-018 | FR-005, FM-003 |
| AC-006 | Coordinate-only changes and unchanged historical commits return exact current state without material-event snapshots. | TEST-007, TEST-008, TEST-028 | FR-006 |
| AC-007 | Five-event lineage passes and every copy/ordinal/kind/split negative has zero false accepted links. | TEST-001, TEST-009, TEST-010, TEST-011, TEST-012 | FR-007, FM-001 |
| AC-008 | Candidate/ambiguous/rejected evidence is inspectable and no fuzzy proposal is accepted. | TEST-010, TEST-012, TEST-034 | FR-008 |
| AC-009 | Independent flags and source/target edge OIDs classify self, descendant, cosmetic, and gap changes correctly. | TEST-008, TEST-009, TEST-014, TEST-016 | FR-009 |
| AC-010 | Removed/absent/unknown/new-observation states and reintroduction identities remain distinct. | TEST-013, TEST-014, TEST-028 | FR-010 |
| AC-011 | Every tree entry is accounted for; zero silent unsupported-language or failed-parse omissions. | TEST-006, TEST-015, TEST-026 | FR-011 |
| AC-012 | No-op uses zero parses; incremental restart reads frontier and exact suffix; logical exports equal full builds. | TEST-018, TEST-019, TEST-020 | FR-012 |
| AC-013 | Readers and cursors retain pinned state through interruption, ref movement, rebuild and explicit GC. | TEST-020, TEST-021, TEST-022 | FR-013, FM-005 |
| AC-014 | Parser changes and matching changes invalidate only compatible reuse, never preserve old semantics as current. | TEST-023 | FR-014 |
| AC-015 | Indexed resolution requires a usable index, exact scope, deterministic ambiguity handling, and full typed IDs. | TEST-027, TEST-028, TEST-029 | FR-015 |
| AC-016 | Output is bounded, valid and explicit about provenance, gaps, omissions and generation pinning. | TEST-022, TEST-029 | FR-016 |
| AC-017 | Existing default APIs/CLI are unchanged; opt-in failures never become a legacy answer claiming indexed semantics. | TEST-030 | FR-017 |
| AC-018 | Every specified invariant has a detection test and precise finding; missing source is reported as unverified. | TEST-031, TEST-014 | FR-018 |
| AC-019 | FK, writer locking, snapshots, rollback, and publication pass mechanism tests, not latency-only assertions. | TEST-003, TEST-004, TEST-020, TEST-021 | NFR-001 |
| AC-020 | Corrupt/error recovery preserves existing DB/WAL/SHM and returns distinct actionable classes. | TEST-024, TEST-034 | NFR-002, FM-006 |
| AC-021 | Every resource cap has an exercised failure/omission path; memory/storage/throughput measurements expose native-parser and retention costs. | TEST-006, TEST-012, TEST-033, TEST-034 | NFR-003, FM-007, FM-009 |
| AC-022 | Qualified platform matrix passes byte paths, object formats, layouts, modes and hostile records without lossy identity. | TEST-025, TEST-026 | NFR-004 |
| AC-023 | Real-refactor precision >=0.99 and recall >=0.90 are measured separately from synthetic results with counts and uncertainty. | TEST-032; gate G-003 | NFR-005, FM-008, FM-010 |
| AC-024 | Warm 10k-commit query target and full operational measurements use the declared protocol; absent evidence is unverified. | TEST-033; gate G-004 | NFR-006 |
| AC-025 | Every implementation item has a prior discriminating test and owner; compatibility and independent verification gates are completed. | TEST-001 through TEST-034; implementation evidence | NFR-007 |

### 7.3 Verification Invariants

`--verify` MUST stream findings and a summary, naming invariant IDs and affected
EIDs, occurrences, paths, generations, and edges. Verification is read-only.

| ID | Invariant |
| --- | --- |
| INV-001 | Foreign keys, schema manifests, canonical IDs/payloads and encoding lengths agree; no dangling binding/version/definition/occurrence links. |
| INV-002 | Membership is unique, contiguous and actual first-parent-connected within its explicit boundary; recorded full parents agree with available commit objects. |
| INV-003 | File intervals do not overlap; frontier equals tip state; bindings reference the interval's parse and each present EID has exactly one occurrence. |
| INV-004 | Accepted links are one-to-one on their edge, preserve context/kind policy, and have complete guard evidence; candidates cannot confer an EID. |
| INV-005 | Each lineage has one recomputable rooted/observed seed and no present binding before it or after terminal departure in that generation. No aliases exist. |
| INV-006 | Event endpoints are at the exact parent/commit, flags match stored representations, and events belong to their generation's membership. |
| INV-007 | Parse failures/boundaries/omissions have coverage records and cannot yield certain child disappearance or root genesis. |
| INV-008 | Only committed verified generations are published; scope pointer, tip, checkpoint frontier, manifests and coverage summary agree. |
| INV-009 | With source checking, blob type/length and exact definition slices/digests agree with Git; unavailable objects give unverified-source findings, not success. |

Relational verification can succeed on an honestly partial index. That success
does not turn coverage complete or certify semantic identity. Source checking
requires original objects and may be unavailable after pruning; report that
separately from stored-data corruption.

Canonical logical export MUST sort by context, full commit OID/membership,
raw path and interval start, full content IDs, edge endpoints and event IDs.
Include coverage, binding intervals, accepted/candidate decisions, reasons and
fingerprints. Exclude local scope/generation allocation handles, diagnostics times,
row IDs, physical B-tree layout, query caches and retained generations outside
the compared snapshot. Compare equivalently scoped published snapshots, not
SQLite file bytes. Build partition/order and operational page size MUST not
alter an export; differing boundaries/available evidence are not comparable runs.

### 7.4 Manual Acceptance

These steps are available only after the proposed commands are implemented.
Use a disposable fixture created by the acceptance harness; do not alter this
working repository, create branches here, or run these during spec authoring.
Harness output provides actual commit IDs; metavariables below denote those IDs.

| ID | Operator action | Required observation |
| --- | --- | --- |
| M-001 | In `refactor_repo`, run `gita index --scope demo --ref HEAD --json`, then `gita id check_token --scope demo --json`. | Complete root-based scope, full EID, final path/bytes, explicit policy/coverage. |
| M-002 | Exit process; run `gita history EID --indexed --scope demo --order oldest --limit 20 --json`. | Exactly five material function events: added, edit, rename, move, edit; same EID and exact endpoint OIDs. |
| M-003 | Use API/state query or `id --at` at fixture commits before/after a line insertion; inspect source slices. | Same EID/version with shifted correct occurrence, no material event for the shift. |
| M-004 | Build duplicate-method and parse-gap fixtures; inspect matches, states and history. | No false fusion; ambiguous evidence and observed gap chains remain visible. |
| M-005 | Build two scopes on divergent fixture tips, request second-parent-only OID in first scope, then rebuild a moved ref. | Nonmember error, isolated branch state, old cursor pinned; new generation published atomically. |
| M-006 | With staged/dirty/untracked fixture changes, run status/build/id and compare file bytes plus Git status before/after. | Only committed OIDs indexed; no stash/reset/checkout/write; working tree explicitly out of scope. |
| M-007 | Run `gita index --verify --scope demo --source-check --json`; exercise absent-index and injected-corrupt disposable stores. | Clean fixture passes; missing index reports not-built; corrupt data remains untouched with recovery guidance. |
| M-008 | Repeat from a linked-worktree subdirectory, bare repo, and supported SHA-256 fixture. | Correct common-dir location, bytes/modes/OID formats, and qualified support status. |

### 7.5 Measurable Gates

| ID | Gate | Pass condition |
| --- | --- | --- |
| G-001 | Contract correctness | AC-001 through AC-022 and AC-025 pass; every INV has a negative detection test; no skipped core acceptance or compatibility failures. |
| G-002 | Reproducibility | Canonical export equality for rebuild/incremental/reverse-scope-order cases, including restarts and fixed partial evidence. |
| G-003 | Identity quality | Precision >=0.99 and recall >=0.90 on a frozen corpus of at least 100 actual refactor instances, with the protocol below. Synthetic negative fixtures have zero false accepted links. |
| G-004 | Interactive lookup | Unverified target: warm p95 <100 ms for EID/name resolution plus first 20 history events on a declared >=10,000-first-parent-commit repository. |
| G-005 | Operational qualification | Bounds/failure tests, crash/concurrency and platform matrix pass; build throughput, storage, retained-generation growth and peak process-tree memory are reported. No unsupported fixed capacity claim. |
| G-006 | Workflow acceptance | M-001 through M-008 pass on qualified fixtures; any unavailable platform/corpus is explicitly unverified and limits release claims. |

**Identity evaluation.** Freeze repositories, commit OIDs, language/operation
distribution, inclusion/exclusion rules, and labels before evaluating the policy.
Use independently inspected real changes and contextual/source evidence, not the
matcher's body similarity as its own answer key. Git file rename detection is a
useful lead, not sufficient ground truth for entity identity. Include retained
copies, mixed edit/refactor commits, duplicate methods, and difficult negatives.
Two reviewers SHOULD label uncertain cases; record disagreements as uncertain
and report their count separately, not as convenient positives or negatives.

Define TP as correctly accepted continuation links, FP as accepted incorrect
links, FN as labeled true continuation links not accepted. Precision is
`TP / (TP + FP)`; recall is `TP / (TP + FN)`. Report raw counts, undefined
denominators, operation/language strata, candidate/ambiguous rates, and 95%
Wilson intervals with the dependency limitation for links sharing a refactor.
Report a refactor-cluster sensitivity summary when multiple links share a case.
One hundred refactors do not statistically certify 99% precision. Synthetic
rename fixtures and mined narrow file-move cases get separate tables, never a
single blended headline. No post hoc threshold tuning on this held-out corpus;
failure requires narrower claims, an explicit policy revision, and fresh evaluation.

**Latency and scale protocol.** Record pinned repository tip, first-parent commit
count, files/entities, language mix, total/distinct blob bytes, changed-file
touches, accepted/candidate edges, event/state row counts, and coverage exclusions.
Record hardware, RAM, disk/filesystem, OS, Python, Git, SQLite, grammar and gita
versions, fingerprints, index/WAL size, and retained-generation count.

For G-004 use one read session and one reader, an already built compatible index,
one warm-up pass, then 100 predetermined EID/name lookups sampled across history
length/frequency strata. Each sample resolves ID plus retrieves 20 events or all
available if fewer; include those result lengths. Measure API p50/p95/min/max,
with p95 nearest-rank over the 100 samples. Include scope/coverage joins, exclude
build and Git source fetch. Point reads MUST not initialize a parser. Report CLI
fresh-process end-to-end timings separately, including interpreter imports,
tokenizer initialization, open/ref checks and rendering. Record truly cold OS
cache measurements only when the cache state is controlled; otherwise call them
fresh-process, not cold. Report parser initialization in full-build measurements.

Measure full build and a pinned 100-commit extension: wall time, commits/second,
blob MiB/second, parse-cache hit count, peak process-tree RSS, SQLite/WAL bytes,
candidate counts and retention overhead. Prior 5-minute/5-second/50-MB estimates
are not release guarantees; no measured 7-MB or 4x promise is carried forward.
Until the declared benchmark/corpus exists and is run, G-003/G-004 are unverified,
not inferred from unit tests or this specification's completeness.

## 8. Security Considerations

- Repository source and names can be sensitive. Keep the cache local with the
  repository's permission boundary; do not upload evidence or source contents.
- Git paths/messages and parser input are untrusted. Use argv, validated full
  OIDs, NUL/length framing, parameterized SQL, and bounded diagnostics/output.
  No shell interpolation, path dereferencing, external diff/textconv, hooks, or
  worktree mutations are required for indexing.
- Invalid UTF-8 MUST NOT become replacement-based identity. JSON escapes display
  controls and includes exact path bytes separately. Never treat a Git path as
  a safe destination filesystem path.
- Cursors are untrusted selectors, not executable queries or authorization
  credentials. Validate their bounds, generation and full filter contract.
- Corrupt stores are not permission to remove user data. Repair is explicit and
  offline; GC is scoped to derived rows and never prunes Git objects.
- No secrets, model endpoint, service identity, cloud deployment, or external
  telemetry is required by this foundation.

## 9. Deployment & Rollback

Ship the index behind explicit new commands and `history --indexed`. Existing
diff/history paths remain the default. Publish schema/encoding and interpretation
versions in status and every indexed answer. A usable partial index is allowed
only with explicit coverage; readiness does not imply complete repository support.

Build/rebuild is explicit. Matching upgrades regenerate accepted-chain metadata
while reusing only compatible parse evidence. Parser upgrades require reparsing.
An incompatible schema is a visible rebuild/offline-replacement requirement, not
a best-effort migration that preserves unverifiable EIDs. Previous published
generations remain available for explicit pinned queries with their fingerprints;
they MUST NOT be silently served as current-policy results.

Rollback of feature use is stopping indexed calls; legacy commands still work.
Rollback of a rebuilt scope can select a retained verified generation explicitly;
it does not alter refs. Cache removal can be performed only after closing all
users/handles and explicit user action. Rebuilding the same scope/context from
the same available Git objects MUST recover the same logical records. It need
not recover old random generation handles or answer instantly without a cache.

Missing index on `id` or indexed history returns not-built, not legacy history.
A future live implementation may share this builder/evidence contract, but it
is not part of this release and MUST NOT be simulated by returning the current
name-filtered history while claiming identical indexed semantics.

## 10. Resolved Decisions

| ID | Decision | Rationale |
| --- | --- | --- |
| RD-001 | Four core concepts plus lineage metadata; versions are separate from occurrences and events. | Material events alone cannot answer shifted-coordinate or complete-subtree state. |
| RD-002 | First-parent scopes with local sequences and exact OID membership. | Full-DAG identity is not established; global chronological IDs give wrong branch state. |
| RD-003 | File-state intervals and immutable binding sets, with lazy occurrence synthesis. | Exact historical locations without a per-entity/per-commit Cartesian snapshot. |
| RD-004 | Explicit observed namespace and provenance-qualified genesis. | A bounded/shallow/gapped scan cannot establish original birth or silently change previously issued identifiers. |
| RD-005 | Strict matcher independent of the legacy differ. | Greedy/ordinal display choices and unmatched-only uniqueness are unsuitable durable facts. |
| RD-006 | Full source/normalized evidence, separate parse and match manifests. | Exact byte retrieval and inspectable decisions; matching changes need not reparse unchanged grammar evidence. |
| RD-007 | One full-invocation SQLite writer transaction; bounded logical batches only. | Removes inter-batch locking gaps and unreliable stale-PID policies; crash resume uses last published checkpoint. |
| RD-008 | Immutable published generations and explicit GC. | Queries remain pinned through rebuild and ref movement; old IDs are not silently overwritten. |
| RD-009 | Explicit build and opt-in indexed history, no live fallback. | Preserves compatibility without falsely claiming old history answers equal new lineage answers. |
| RD-010 | Declarative schema here, executable DDL tested in F1. | Freeze semantic invariants without presenting unexecuted SQL as verified implementation. |
| RD-011 | Precision is the binding quality concern; all measured gates report failures/unknowns. | Incorrect fused history is more damaging than a visible unresolved continuation. |

## 11. Alternatives Considered

| Alternative | Benefit | Problem | Decision |
| --- | --- | --- | --- |
| Persist existing diff results verbatim | Minimal code | Greedy fuzzy ties, positional sibling fallback, retained-copy hazard; no durable evidence contract. | Rejected. |
| Event rows double as versions/state | Small schema | Loses location shifts and descendant-only state. | Rejected. |
| Global dense commit ID with latest-event lookup | Simple query | Cross-branch/merge results are wrong; time/ref order leaks into identity. | Rejected. |
| Full entity snapshot every commit | Simple historical query | Unbounded entity-by-commit duplication despite unchanged files. | Rejected. |
| Parent-relative event replay for every query | Small storage | Reintroduces history walks and makes incremental restart/query cost history-dependent. | Rejected. |
| Full-DAG identity and alias union | Wider history | Conflicting branch genesis and split/merge semantics are unresolved; silent union corrupts meaning. | Deferred without a placeholder alias column. |
| Hashes prove identity or semantic safety | Easy marketing and automatic decisions | Distinct entities share representations; normalizers omit behaviorally relevant information. | Rejected. |
| Commit each batch with custom PID lock | Finer resume | Adds lock-lifetime, stale ownership, and crash-publication complexity. | Rejected for initial foundation; reconsider only after measured full-transaction limits. |
| Automatic discard/rebuild on any DB error | Simple caller path | Destroys evidence on busy/permissions/I/O and unsafe with readers. | Rejected. |
| Transparent indexed/default history substitution | Fewer flags | Current live algorithm cannot meet the new identity contract. | Rejected until equivalent implementation is independently validated. |
| Parallel parsing by default | Possible throughput | More memory/cancellation/order complexity before measurement. | Deferred; a single isolated parse worker provides deadline control only. |

## 12. Files

This is the Files Affected inventory for the foundation, not a claim that every
listed responsibility is implemented. Section 14 records the delivered subsets;
paths marked proposed do not yet exist.

| ID | Existing or proposed path | Owner and bounded responsibility |
| --- | --- | --- |
| FILE-001 | [Contract model](../../src/gita/index/model.py) | Contract owner: immutable records, statuses, canonical IDs/fingerprints and pure codecs; no Git or SQL orchestration. |
| FILE-002 | [Store](../../src/gita/index/store.py) | Store owner: DDL, lookup/write primitives, transactions, generation publication and GC; currently evidence-cache subset only. |
| FILE-003 | [Evidence](../../src/gita/index/evidence.py), [worker](../../src/gita/index/worker.py); [extractor](../../src/gita/entities/extractor.py) | Evidence owner: exact byte/full-representation adapter and isolated operational deadlines; legacy output unchanged. |
| FILE-004 | [Git plumbing](../../src/gita/vcs/git.py) | Evidence owner: additive commit-only, byte-safe paths/object enumeration and correct common-dir handling. |
| FILE-005 | Proposed `src/gita/index/matching.py` | Lineage owner: strict tiers, guard evidence, candidate limits, multi-flag classification. |
| FILE-006 | Proposed `src/gita/index/builder.py` | Lineage owner: scope walk, complete checkpoint, persisted frontier, transitions, coverage and bounded staging. |
| FILE-007 | Proposed `src/gita/index/query.py`; [history](../../src/gita/history.py) | Query owner: pinned resolution/state/ranges/history; additive routing only in existing history surface. |
| FILE-008 | [CLI](../../src/gita/cli/__init__.py); [renderer](../../src/gita/cli/render.py) | Query owner: proposed commands, envelopes/exits/cursors and bounded rendering; retain legacy behavior. |
| FILE-009 | Proposed `src/gita/index/verify.py` | Verification owner: invariant/source checks and canonical logical export independent of builder matching decisions. |
| FILE-010 | [Fixture home](../../tests/conftest.py) | Contract owner first, then coordinated edits: real refactor, duplicate/gap, branch, crash fixtures; reuse existing Git helpers. |
| FILE-011 | [Store tests](../../tests/test_index_store.py), [builder tests](../../tests/test_index_builder.py); proposed `tests/test_index_query.py`, `tests/test_index_verify.py` | Corresponding owners; focused homes, not a new file per acceptance case. |
| FILE-012 | [Extractor tests](../../tests/test_extractor.py), [object tests](../../tests/test_objects.py), [enumeration tests](../../tests/test_enumeration.py), [CLI tests](../../tests/test_cli.py), [history tests](../../tests/test_history.py) | Owning touched surface: byte/Git additive coverage and unchanged legacy behavior. |

The existing [index package](../../src/gita/index/__init__.py) MUST NOT become a
second public API or an orchestration module.
Existing [diff matcher](../../src/gita/diff/differ.py),
[model](../../src/gita/entities/model.py), and
[TreeStore](../../src/gita/entities/store.py) are evidence/reference boundaries,
not invitations to rewrite legacy semantics.

## 13. Simplicity Rationale

The extra occurrence/state layer is necessary: a material-event-only design is
falsified by one unchanged function shifted down by an import. Scope-local
membership is necessary: two branches with the same timestamps falsify a global
latest-event lookup. A separate strict policy is necessary because the existing
matcher deliberately makes display choices unsuitable as durable identity.

New abstractions are plain immutable records, one evidence adapter, one strict
policy, one SQLite store, one builder, one read session, and one verifier. There
is no plugin/strategy registry, distributed service, ORM, worker pool, migration
framework, or generic graph engine. A single parse worker provides enforceable
cancellation only. Store and query stay separate because transaction ownership
and immutable read snapshots have different contracts, not to add framework layers.

Configuration is limited to explicit scope/ref/boundary selection, rebuild,
indexed opt-in, pagination/order/budget, verify/source-check/GC, and versioned
resource limits. Each serves a requirement above. Limits that change evidence
must be fingerprinted; operational knobs must not change identity. No hidden
automatic indexing or ref-driven cross-scope behavior is allowed.

Every epic below traces to requirements. F0 is an enabling epic because records,
independent ID vectors and the failing end-to-end fixture must be agreed before
store and evidence owners can work without incompatible assumptions. F4 is
independent measurement/verification, not extra feature work. The simplest
rejected alternative is retaining the legacy matcher's events in SQLite: it
would be smaller but demonstrably cannot meet precise state, branch isolation,
and uncertainty requirements.

## 14. Implementation Plan

F0 is implemented and F1 has a partial evidence-cache checkpoint as recorded
below. F2-F4 remain Not Started, irrespective of WS-1's implemented status.
Phase labels F0-F4 are delivery checkpoints, not time estimates.
Each item is test-first and bounded by ownership; an epic may contain sequential
subitems but does not authorize simultaneous editing of the same path.

### F0 Implementation Checkpoint (2026-09-14)

- Pure frozen records, explicit statuses, typed Git OIDs, source spans, canonical
  identifiers, payload-collision checks, parse completeness and page-count
  invariants are implemented in `src/gita/index/model.py`.
- `refactor_repo` creates five real, deterministic Git commits with an
  independent source/name/path manifest. The end-to-end test invokes new CLI
  processes so a future in-memory-only implementation cannot satisfy persistence.
- Observed RED: imports failed before implementation; later parse/count/token
  checks failed before their implementation; synthetic-root checks failed before
  the missing field was added. Observed GREEN: 208 contract tests and 13
  fixture/assertion-calibration tests pass. No tests are skipped or marked xfail.
- Full regression run through Unit Test MCP: **795 passed, 1 failed** in 279.3s.
  The sole failure was separately confirmed by exact selection as
  `test_foundation_tracks_five_events_after_restart`: `gita index` is not yet
  implemented. The full suite is deliberately NOT green and MUST NOT be
  represented as a releasable persistent-index implementation. After correcting
  the fixture's trailing-newline span expectation, its narrow rerun remained
  13 passed and this same one failed; no production code changed after the full run.
- The negative controls reject wrong commits, source/destination swaps, wrong
  names/spans/materiality and hashes present only in unrelated diagnostics.
  Encoding is independently pinned for all eleven currently allocated domains.
- Canonical contract checks are runnable from the repo root with
  `.venv\Scripts\python.exe -m pytest tests/test_index_store.py -q`.
  Run the deliberately red acceptance with
  `.venv\Scripts\python.exe -m pytest tests/test_index_builder.py::test_foundation_tracks_five_events_after_restart -q`.
  Expected current failure is exit `2` from the child CLI's unknown `index`
  command, surfaced as a failed assertion, not a successful foundation check.
- At this F0 checkpoint, storage, matching policy execution, scope traversal, CLI indexed commands,
  real-refactor precision/recall and performance gates remain unimplemented or
  unverified. Existing diff/history semantics are unchanged.

### F1 Evidence Checkpoint (2026-09-14)

This is partial EPIC-002 delivery, not completion of F1 or a usable entity index.

- [store.py](../../src/gita/index/store.py) implements a four-table evidence cache:
  metadata, versions, parse results and definitions. It validates canonical
  payloads on write/read, compares collision payloads, preserves exact bytes and
  token tuples, and checks definition/version fingerprint, kind and language.
- Explicit creation and read-only opening are separate. WAL, foreign keys,
  `synchronous=FULL`, initial integrity checking, caller-owned writer
  transactions, atomic parse bundles, pinned read snapshots and typed storage
  errors are exercised. Failed writes roll back; existing files are not deleted
  as recovery. Common-dir location is tested for nested, linked and bare repos.
- [evidence.py](../../src/gita/index/evidence.py) adds synchronous parsing of
  original bytes with exact spans/digests, full literal names, own/signature/body
  and subtree tokens, parser/artifact fingerprints and resource-limit outcomes.
  Tests cover BOM, CRLF, multibyte text, newline boundaries, moved line positions,
  duplicate siblings, meaningful literal whitespace and parse errors. SQL and
  unsupported text remain explicitly unsupported in this adapter; legacy SQL
  behavior is unchanged.
- Observed RED preceded store, adapter and harness implementations. Review-led
  regressions caught missing create-time integrity checks, skipped supplied-version
  validation on duplicate parses, language mismatches, literal whitespace loss,
  TOML value-field assumptions and trailing-newline span errors before repairs.
  Harness tests first reported 266 passed / 8 failed, then **274 passed**.
- Latest full Unit Test MCP regression: **921 passed, 1 failed**, no skips, in
  301.5s. Exact selection confirmed the sole failure is
  `test_foundation_tracks_five_events_after_restart`, still failing because
  `gita index` is not implemented. Coverage percentages were not measured.
- Manual command: `.venv\Scripts\python.exe verify\check.py --stage f1-evidence`.
  Five checks passed: independent Python AST spans and BLAKE2b-256 digests,
  close/reopen round trip, line-shift versus literal evidence, persisted syntax
  errors, and rollback/read-only guards. Fixtures are retained under a unique
  temporary path printed by the command. Round-trip checks share production
  serialization code; they are not an independent proof of the full storage model.
  Missing, empty or failing checkpoint runners cannot report a successful F1 run.
- Remaining F1 work includes the rest of the relational schema, immutable
  generations/publication, generation GC, the bounded parse worker, and byte-safe
  offline Git enumeration/source retrieval. Matching-only source changes can
  still invalidate parsing because the fingerprint includes the whole contract
  module; full parse/match invalidation separation is not yet established.
  No lineage builder, scope state, indexed commands or performance gates are done.

### F1 Worker Checkpoint (2026-09-14)

This advances ITEM-006/TEST-034 without changing the synchronous adapter or store.
The remaining-work list in the preceding checkpoint is historical; the isolated
worker below is now implemented, while the other F1 boundaries remain open.

- [worker.py](../../src/gita/index/worker.py) lazily starts one persistent spawn
  child and serializes parse requests. A deadline-bounded I/O thread covers both
  blocked sends and incomplete responses; there is no worker pool or database
  connection in the child. Default deadline is five seconds, including startup
  and transport. Library callers use the usual multiprocessing main guard.
- Successful results equal the direct adapter's full bundle and identifiers.
  Deadlines are operational, excluded from fingerprints. Timeout raises
  `resource_limit`, not cached partial evidence. Crashes and malformed replies
  raise `worker_failed`; failed or closed workers refuse reuse. Contract errors
  retain their code, and parent cancellation propagates after cleanup.
- Cleanup terminates/reaps the child and joins I/O, with kill fallback when
  termination fails. Original parse errors survive cleanup failures; unreleased
  resources stay owned for a later close retry. Response completion timestamps
  reject replies arriving after the deadline. OS startup/reaping cannot provide
  hard real-time guarantees; native process-tree RSS is not yet measured.
- Observed RED: 40 missing-API failures, then GREEN. Independent review added two
  failing regressions for termination failure and late success; repairs yielded
  **42 passing worker cases**. A test-only shared Event barrier hung when signalled
  after its waiting child was killed; replacing it with a semaphore fixed the
  failure-injection cleanup without weakening timeout assertions.
- The five new manual-harness cases were observed RED then GREEN. Full Unit Test
  MCP regression: **968 passed, 1 failed**, no skips, in 316.6s. The known five-event
  CLI acceptance remains red because `gita index` is absent; no coverage percentage
  or release-ready foundation claim follows from these counts.
- Manual command: `.venv\Scripts\python.exe verify\check.py --stage f1-worker`.
  Four checks passed: reused-worker/direct equality, terminal deadline refusal,
  persisted-cache rollback retaining prior evidence, and an identical fresh-worker
  retry explicitly committed and reopened. Fixtures use a unique retained temp
  directory. Its immediate deadline includes startup, not a parse-speed benchmark;
  actual blocked-send/receive and cancellation controls are in the worker tests.
- Generation publication, the remaining relational schema and GC, byte-safe
  offline Git retrieval, and complete parse/match fingerprint separation are still
  pending before F1 completion. F2 lineage and F3 indexed commands are not started.

### EPIC-001: F0 Contracts and Failing Acceptance

Dependencies: none beyond existing fixtures. Traces: FR-002, FR-003, FR-004,
FR-007, NFR-007. Owner: contract lead. Exit: frozen records/vectors and a visible
five-event red acceptance; no indexing success claim yet.

| Task | Description | Status | Relevant Files |
| --- | --- | --- | --- |
| ITEM-001 | Add distinctive five-commit `refactor_repo`, independently record expected endpoints, and add TEST-001 red acceptance with explicit not-implemented behavior. Do not retrofit legacy history to fake a pass. | Done: acceptance written and observed red; product behavior pending | FILE-010, FILE-011 builder tests |
| ITEM-002 | Specify and implement immutable contract records and canonical codec behind TEST-002; freeze byte vectors, seed namespaces, exact statuses, and context inputs for store/evidence owners. | Done: 208 contract tests pass | FILE-001, FILE-011 store tests |
| ITEM-003 | Add pure invariants/fixture helpers for complete/unknown state and typed errors; review fixture labels separately from matcher outputs. Keep acceptance red until builder/query behavior exists. | Done: pure state/count guards and 13 fixture/assertion checks pass | FILE-001, FILE-010, FILE-011 |

### EPIC-002: F1 Persistent Store and Evidence

Dependencies: F0 records. Traces: FR-001, FR-002, FR-003, FR-011, FR-014,
NFR-001, NFR-002, NFR-003, NFR-004. Owners: store and evidence leads on disjoint
paths. Exit: executable DDL/constraints, safe lifecycle, exact byte evidence and
complete coverage outcomes; no accepted lineage policy required yet.

| Task | Description | Status | Relevant Files |
| --- | --- | --- | --- |
| ITEM-004 | Write failing store/schema/FK tests TEST-003/004, implement read-only open and declared relations/indexes, enforce immutable records, interval checks and generation references. | Partial: four-table evidence cache and immutable record checks; remaining relations, intervals and generations pending | FILE-002, FILE-011 store tests |
| ITEM-005 | Write TEST-020/021/024/034 failure-boundary cases, implement one-writer transactions, rollback/publish, typed errors and non-destructive generation GC. | Partial: cache transactions, rollback, read snapshots and typed errors; publication, generation GC and remaining failure gates pending | FILE-002, FILE-011 store tests |
| ITEM-006 | Write TEST-005/006/023 for byte/full-token evidence and parse fingerprints, implement the additive evidence adapter plus bounded parse worker; legacy extractor regression stays green. | Partial: byte adapter, deterministic limits and isolated deadline worker tested; complete parse/match invalidation separation pending | FILE-003, FILE-011 builder tests, FILE-012 extractor tests |
| ITEM-007 | Write TEST-025/026, extend Git object/commit/path access with safe framing, empty/missing distinction and common-dir resolution; preserve WS-1 behavior. | Partial: tested common-dir location only; byte-safe offline Git access pending | FILE-004, FILE-012 Git tests |

Store/evidence development MAY run in parallel only after ITEM-002 records are
agreed. The contract owner serializes shared fixture edits. Neither owner changes
the other's model fields unilaterally; failing record-contract tests arbitrate.

### EPIC-003: F2 Strict Lineage Builder

Dependencies: F1 store/evidence. Traces: FR-004 through FR-011, FR-013,
NFR-003, NFR-007. Owner: lineage lead. Exit: five-event acceptance green through
the builder/read primitives, negative identity cases green, bounded initial build.

| Task | Description | Status | Relevant Files |
| --- | --- | --- | --- |
| ITEM-008 | Write TEST-015/016/017 boundary/merge/branch tests, implement scope-local membership and full anchor checkpoint with rooted versus observed seeds and all-parent provenance. | Not Started | FILE-006, FILE-011 builder tests |
| ITEM-009 | Write TEST-007/008/009/010/011/012, implement strict frozen-tier matching, retained-population guards and independent event flags. Close TEST-001 without changing the legacy differ. | Not Started | FILE-005, FILE-006, FILE-011 builder tests |
| ITEM-010 | Write TEST-013/014 coverage/departure tests, persist changed-file intervals, bindings, exact endpoint occurrences, tombstones and unknown gaps; resource-limited evidence stays partial. | Not Started | FILE-006, FILE-011 builder/query tests |

### EPIC-004: F3 Queries and Incremental Publication

Dependencies: F2 and F1 publication primitives. Traces: FR-005, FR-006,
FR-010, FR-012 through FR-017, NFR-001, NFR-002. Owners: lineage lead for
incremental builder; query lead for reads/CLI. Exit: restart/incremental equality,
usable proposed commands, pinned pagination, and legacy compatibility.

| Task | Description | Status | Relevant Files |
| --- | --- | --- | --- |
| ITEM-011 | Write TEST-018/019/020/022, extend only validated first-parent suffixes from persisted frontier, publish immutable generations, and require rebuild for noncontinuation/context changes. | Not Started | FILE-006, FILE-011 builder/store tests |
| ITEM-012 | Write TEST-027/028, implement read sessions, current/historical name lookup, EID prefixes, exact state/ranges and lazy occurrence synthesis; reads never walk history or create an index. | Not Started | FILE-007, FILE-011 query tests |
| ITEM-013 | Write TEST-022/029/030, add opt-in commands, envelopes/exits, token-bounded cursor pages and ref-movement reporting while preserving all default legacy behavior. | Not Started | FILE-008, FILE-011 query tests, FILE-012 CLI/history tests |

### EPIC-005: F4 Verification and Manual Acceptance

Dependencies: F3 public contract; verifier design and independent labels can
start after F0. Traces: FR-018, NFR-003 through NFR-007. Owner: independent
verification lead, not a second implementation of the builder's assumptions.
Exit: explicit pass/fail/unverified gate report, not necessarily a passing release.

| Task | Description | Status | Relevant Files |
| --- | --- | --- | --- |
| ITEM-014 | Write TEST-031 by injecting one violation per invariant; implement bounded verifier/source checks and canonical export; rerun TEST-019 determinism comparisons. | Not Started | FILE-009, FILE-011 verify tests |
| ITEM-015 | Write TEST-032 scorer/label checks; evaluate frozen real-refactor and synthetic keys separately, with counts, uncertainty and no test-set tuning. Keep evidence local and follow repository evaluation conventions when later authorized. | Not Started | FILE-011 verify tests |
| ITEM-016 | Write TEST-033/034 harness checks, measure G-004/G-005 with recorded environment and coverage, then perform M-001 through M-008. Record every unavailable gate explicitly. | Not Started | FILE-011 verify/store tests, FILE-012 platform tests |

Handoffs MUST include failing/passing test names, record-contract changes, and
coverage limitations. Verification can challenge acceptance assumptions; it MUST
not tune expected values until the builder passes. No phase claims a generic
99% test-coverage percentage as a substitute for these behavioral gates.

## 15. Change Log

2026-09-14: Initial foundation contract. Authoring validation covers document
structure, local link targets, and requirement/acceptance/test traceability only.
No implementation, executable DDL, unit tests, real-refactor scores, or performance
claims were validated by authoring alone.

2026-09-14: F0 implemented test-first; wire framing, parse metadata and acceptance
JSON shapes frozen. See section 14 for measured test results and the deliberately
red end-to-end acceptance. No persistent index or new indexed commands exist yet.

2026-09-14: Partial F1 evidence cache, byte adapter and scoped manual checkpoint
implemented test-first. Section 14 records 921 passing tests, the one deliberately
red CLI acceptance, five manual passes and the remaining F1 boundaries.

2026-09-14: F1 isolated worker implemented and independently reviewed. Section 14
records 42 worker cases, five new harness cases, four manual passes and the latest
full regression of 968 passed / one deliberately failed CLI acceptance.