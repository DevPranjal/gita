# gita entity index — Year 1 specification

Status: draft for implementation. Branch `first-principles`, 2026-09-04.
Implements Year 1 of [FIRST-PRINCIPLES.md](FIRST-PRINCIPLES.md): durable
identity and the persistent index.

---

## 1. Scope

Ship a persistent, incremental, content-addressed index at
`$GIT_COMMON_DIR/gita/` holding every entity, every version, and every event
across a repository's history, with **durable identity assigned once at write
time** rather than recomputed and discarded on every query.

Two user-visible commands: `gita index` and `gita id`. Every existing command
gets faster; `gita history` gets correct.

### 1.1 Why this first

`gita history` finds 2 of 5 events on a function that was renamed and moved,
where `git log --follow` finds 5 (FIRST-PRINCIPLES §1.1). The engine detects
both the rename and the move — it is `entity_history` that cannot carry the
thread, because today an entity's identity **is** its location:

```python
# entities/model.py
id = "src/flask/app.py::Flask::dispatch_request"
```

Rename the function and that string changes. Move the file and it changes. The
identity is the one thing that must not move.

### 1.2 Non-goals for Year 1

- No caller graph, no impact analysis (Year 3).
- No notes or memory (Year 4). §4.6 constrains the design so Year 4 stays
  possible.
- No merge classification (Year 2), though §5 gives it the identity it needs.
- No new output formats. Existing renderers, budgets and noise filtering are
  unchanged.

---

## 2. The model

Three stored types. Two more (`edge`, `note`) are named in FIRST-PRINCIPLES and
deliberately absent here.

**Entity** — a named thing with a durable identity. Created once, never
destroyed, never renamed. `entity` holds only what is true for the whole
lifetime: the identity, the kind, and where it was born.

**Version** — an entity as it stood at one commit: path, name, line span, four
hashes. Versions are not stored independently; each event carries the version
it produced.

**Event** — a typed transition, one row per (entity, commit) where something
happened. The vocabulary is the existing `ChangeKind`, unchanged:
`added`, `removed`, `renamed`, `moved`, `signature_changed`, `body_changed`,
`cosmetic`. `unchanged` is never stored — absence means unchanged.

An entity's history is its events ordered by commit. Its state at any commit is
the most recent event at or before that commit. Nothing else needs storing.

---

## 3. Identity

### 3.1 The identifier

```
eid = BLAKE2b-128( genesis_commit_sha_binary ‖ 0x00 ‖ path ‖ 0x00 ‖ qualname )
```

- `genesis_commit_sha_binary` — 20 raw bytes (32 under SHA-256 repositories)
- `path` — repository-relative, UTF-8, forward slashes, no leading `./`
- `qualname` — the existing dotted-with-`::` path within the file, including
  the `#2` ordinal suffix that already disambiguates same-named siblings
- output — 16 bytes, rendered as 32 lowercase hex characters

Content-addressed, like a git object: derived from what the thing is, never
allocated by a counter. No central registry, no coordination, and two machines
indexing the same history produce the same identifiers.

`qualname` is unique within a file at a commit, so the triple cannot collide
with itself. Across the 128-bit space, accidental collision is not a concern at
any repository size.

**Abbreviation.** Like git, `eid`s are shown and accepted abbreviated to the
shortest unique prefix in the repository, minimum 8 hex characters. `gita id`
prints the abbreviated form; every command accepts either.

### 3.2 Genesis

The **genesis commit** of an entity is the earliest commit, in the indexed walk,
at which the entity appears. "Earliest" is by the walk's topological sequence
(§5.2), which is a function of the commit DAG alone — not of when indexing ran
or in what order.

Consequences, stated plainly because they are the sharp edges:

- **Rebuilding from the same DAG yields identical identifiers.** This is a
  tested invariant (§8.2), not an aspiration.
- **Rewriting history changes identifiers**, exactly as it changes commit SHAs.
  A rebased branch is a different history and its entities are different rows.
- **Deepening a shallow clone changes identifiers**, because entities are then
  discovered to be older than the index believed. `git fetch --unshallow`
  invalidates the index and forces a rebuild (§6.4). This is the one genuinely
  unpleasant consequence of genesis-anchoring, and it is accepted because the
  alternative — a mutable identity — destroys everything built on top.

### 3.3 Carrying identity forward

At each commit, for each changed file, the existing pipeline already produces
the answer:

```
diff_trees(before, after)   →  per-file matching: id, then content hash, then similarity
reconcile_moves(changes)    →  cross-file matching: hash only
```

Year 1 adds no matching logic. It **persists the result**:

| change kind | identity action |
| --- | --- |
| `body_changed`, `signature_changed`, `cosmetic` | inherit the `eid` bound to the previous entity |
| `renamed`, `moved` | inherit the `eid`; record the new name and path on the event |
| `added` | mint a new `eid` from this commit as genesis |
| `removed` | record a death event; the `eid` and its history remain |

The one rule that is new, and that governs everything: **when matching is
ambiguous, mint a new identity rather than guess a link.**

This is P2 applied to identity, and the existing code already leans this way —
`_unique_by` in `diff/differ.py` keeps only keys occurring exactly once on both
sides, and `MIN_BODY_TOKENS = 3` refuses to match bodies too small to be
distinctive. Year 1 inherits both guards unchanged.

The asymmetry matters and drives the acceptance thresholds in §8.4:

- A **missed** link mints a fresh identity. History appears to start later than
  it did. Annoying, visible, recoverable.
- A **false** link fuses two unrelated functions permanently. History is wrong,
  blame is wrong, and in Year 4 a constraint is enforced against the wrong
  code. Silent, and corrupting.

Precision is therefore the binding constraint, not recall. §8.4 revises the
kill criterion accordingly.

### 3.4 Resurrection

An entity removed at commit *n* and reintroduced at commit *m* mints a new
identity at *m*. Reconnecting it would require searching all dead entities on
every add, and the evidence — an identical body — is exactly the weak signal
§3.3 refuses to trust.

Deferred, not forgotten: the `alias` column (§4.2) can express it later without
a schema change.

### 3.5 Aliases

`entity.alias_of` exists from day one and is always `NULL` in Year 1.

It reserves the mechanism for the case a first-parent walk cannot produce but a
multi-ref walk can: the same logical entity independently minted on two
branches, later found to be one thing. Queries already resolve through it
(§7.2), so enabling it later is not a migration.

---

## 4. On-disk format

### 4.1 Location

```
$(git rev-parse --git-common-dir)/gita/
    index.sqlite          the index
    index.sqlite-wal      write-ahead log
    index.sqlite-shm      shared memory
    build.lock            advisory lock, held only during a build
```

`--git-common-dir`, not `--git-dir`, so that all linked worktrees share one
index. It returns a path relative to the working directory (`.git` in a plain
checkout), so it must be resolved against `--show-toplevel` before use.

Inside the git directory the index is untracked by construction, invisible to
`git status`, ignored by `git clean`, and removed with the repository.

### 4.2 Schema

```sql
PRAGMA journal_mode = WAL;      -- readers never block on the builder
PRAGMA synchronous  = NORMAL;   -- a derived cache does not need O_SYNC per commit
PRAGMA foreign_keys = ON;       -- off by default; the constraints below are
                                -- otherwise parsed and ignored. Per-connection,
                                -- and silently ignored if issued inside a
                                -- transaction, so it must be set immediately
                                -- after connect, on every open. See WS-2 §3.
PRAGMA user_version = 1;        -- schema version; a mismatch forces rebuild

-- Interning. Paths and names repeat across millions of events.
CREATE TABLE path (id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE);
CREATE TABLE name (id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE);

-- Indexed commits, in walk order.
CREATE TABLE commits (
    id        INTEGER PRIMARY KEY,   -- dense, equals topological sequence
    sha       BLOB    NOT NULL UNIQUE,
    parent    BLOB,                  -- first parent, NULL at a root
    authored  INTEGER NOT NULL,      -- unix seconds
    author    INTEGER NOT NULL REFERENCES name(id),
    subject   TEXT    NOT NULL
);

-- Durable identity. One row per entity, ever.
CREATE TABLE entity (
    eid            BLOB PRIMARY KEY,           -- 16 bytes, §3.1
    kind           INTEGER NOT NULL,           -- EntityKind ordinal
    genesis_commit INTEGER NOT NULL REFERENCES commits(id),
    genesis_path   INTEGER NOT NULL REFERENCES path(id),
    genesis_name   INTEGER NOT NULL REFERENCES name(id),
    alias_of       BLOB REFERENCES entity(eid) -- always NULL in Year 1, §3.5
);

-- One row per (entity, commit) where something happened.
CREATE TABLE event (
    eid        BLOB    NOT NULL REFERENCES entity(eid),
    commit_id  INTEGER NOT NULL REFERENCES commits(id),
    kind       INTEGER NOT NULL,               -- ChangeKind ordinal
    path       INTEGER NOT NULL REFERENCES path(id),
    name       INTEGER NOT NULL REFERENCES name(id),
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    raw        BLOB NOT NULL,                  -- 8 bytes each, as today
    content    BLOB NOT NULL,
    signature  BLOB NOT NULL,
    body       BLOB NOT NULL,
    PRIMARY KEY (eid, commit_id)
) WITHOUT ROWID;

CREATE INDEX event_by_commit ON event(commit_id);
CREATE INDEX event_by_name   ON event(name);
CREATE INDEX event_by_path   ON event(path, commit_id);

-- Build state and provenance.
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tracked (ref TEXT PRIMARY KEY, tip BLOB NOT NULL);
```

`WITHOUT ROWID` on `event` stores rows directly in the primary-key B-tree,
which is how they are read — all of one entity's history is one contiguous
range.

`event_by_name` is what makes `gita id <name>` a lookup rather than a scan, and
it indexes *every* name an entity ever had, so a function is findable by its old
name after a rename. That is the point of the whole exercise.

The schema above was executed against SQLite 3.45.1 while writing this document:
it creates cleanly, and a representative row round-trips through the
`WITHOUT ROWID` blob primary key.

### 4.3 `meta` keys

| key | meaning |
| --- | --- |
| `schema` | integer, mirrors `user_version` |
| `gita_version` | the version that built this index |
| `semantics` | fingerprint of everything that can change extraction or matching (§4.4) |
| `walk` | `first-parent` in Year 1 |
| `object_format` | `sha1` or `sha256` |
| `built_at`, `updated_at` | unix seconds, diagnostics only |

### 4.4 The semantics fingerprint

An index is only valid for the code that produced it. Upgrading a tree-sitter
grammar, adding a language, or changing `RENAME_THRESHOLD` can change the
entities extracted or the links formed — silently, and long after the rows were
written.

```
semantics = BLAKE2b-128(
    grammar name and version for every registered language, sorted ‖
    the extension → language table ‖
    RENAME_THRESHOLD ‖ MIN_BODY_TOKENS ‖ _SHINGLE ‖
    the ChangeKind and EntityKind vocabularies ‖
    ENTITY_SCHEMA_VERSION
)
```

`ENTITY_SCHEMA_VERSION` does not exist yet; M2 adds it to `entities/model.py`
and bumps it whenever the shape or hashing of an `Entity` changes. Everything
else in the fingerprint is read from existing constants.

On open, a mismatch means the index describes a semantics that no longer exists.
gita reports it and rebuilds. Cheap to compute, and it converts a class of
silent wrongness into a visible one-off cost.

### 4.5 Size

Measured on the corpus: **3.2 supported-file touches per commit**, and 666
material changes over 150 Flask commits — call it 4.5 events per commit.

An `event` row is 16 + 8 + 1 + 4 + 4 + 8 + 32 ≈ 73 bytes of payload, ~110 bytes
stored with B-tree overhead.

| history | events | index |
| --- | ---: | ---: |
| 1,000 commits | ~4,500 | ~1 MB |
| 10,000 commits | ~45,000 | ~7 MB |
| 100,000 commits, wide monorepo (20 events/commit) | 2,000,000 | ~300 MB |

Small enough to ignore below 10k commits. `gita index --since <rev>` bounds the
monorepo case.

### 4.6 The index is a cache

**Invariant: deleting the index loses nothing but time.** Every command must
work without it, computing live at today's speed. The index is `commit-graph`,
not `objects`.

This is not tidiness. Year 4 attaches durable notes to `eid`s, and notes are
*data* — they must be committed, pushed and reviewed. So notes will live in git
refs (`refs/notes/gita`), referencing identifiers the index can always
re-derive. Were the index authoritative, `rm -rf .git/gita` would destroy user
data and the index would need backup, replication and its own format stability
guarantees.

Keeping it purely derived in Year 1 is what keeps Year 4 cheap.

---

## 5. The build

### 5.1 Cost, measured

Two probes. First, where the **existing live path** spends its time — 100
first-parent Flask commit pairs through `diff_revisions`
(`scratch/probe_live_cost.py`):

| component | time | per commit | share |
| --- | ---: | ---: | ---: |
| `changed_files` enumeration, one `git diff` per commit | 7.95 s | 79.5 ms | **75%** |
| everything else — blob reads, parse, matching | 2.64 s | 26.4 ms | 25% |
| **end to end** | **10.60 s** | **106.0 ms** | |
| the same enumeration in bulk, one `git log --raw` | 0.08 s | 0.8 ms | **95x faster** |

Second, the phases an indexer would run, over 300 commits (766 distinct blobs,
12.6 MB, 33,273 entities — `scratch/probe_index_cost.py`):

| phase | per commit | note |
| --- | ---: | --- |
| enumerate, bulk `git log --raw` | 0.3 ms | negligible |
| read blobs, `cat-file --batch` | 0.6 ms | 326x faster than a process per blob |
| parse | 21.1 ms | 2.0 MB/s, **96% of what remains** |
| **total** | **22.1 ms** | |

Three findings drive the design:

1. **Per-commit process spawning dominates the live path, and it is
   enumeration, not object reading.** Blob reads already go through
   `_ObjectReader`'s `cat-file --batch`; it is the `git diff --name-status` per
   commit pair that costs 79.5 ms. `Repo.walk` already fetches commits and their
   changed files in one call — `diff_revisions` simply does not use it. Routing
   history walks through bulk enumeration is a **4x** win on every command,
   with or without an index.
2. **Parsing dominates what is left**, and it is embarrassingly parallel: a
   parse depends only on `(blob, path)`.
3. Reading objects is already solved. No work needed.

Projected full build of 10,000 commits: **3.7 minutes sequential, ~40 seconds
with an 8-way parse pool.**

*(An earlier draft of this section blamed per-blob process spawning, on the
strength of a synthetic 199 ms/commit measurement of `git cat-file` invoked once
per blob. gita has never done that. The live-path probe above was written to
check the claim and refuted it — the cost is enumeration. Recorded because the
wrong version would have sent M1 at code that is already correct.)*

### 5.2 The walk

Year 1 indexes the **first-parent walk** of each tracked ref, oldest commit
first, sequence number dense and ascending.

First-parent is not a simplification of convenience; it is the correct default:

- It is what landed on the branch. On Flask, 110 of 150 commits are merges —
  each merge is one pull request, and its first-parent diff is that pull
  request's whole effect. Indexing the side branch would record every
  work-in-progress commit as history.
- It keeps the walk linear, so identity carries forward in a single pass with
  one in-memory map, instead of needing a resolved entity map at every merge
  parent.

The cost is that a commit reachable only from a second parent is not indexed and
does not appear in `gita history`. `meta.walk` records this so queries can state
it rather than imply completeness. A `--walk=all` mode is a Year 2 option, and
§3.5 already reserves the alias mechanism it would need.

### 5.3 Phases

```
A  enumerate    git log --first-parent --raw --no-renames  →  (commit, path, blob) triples
B  read         git cat-file --batch, interleaved          →  blob bytes
C  parse        (blob, path) → EntityTree                  →  parallel, pure, cacheable
D  chain        diff_trees + reconcile_moves, sequential   →  identity and events
E  commit       one SQLite transaction per batch
```

C is pure and order-independent; D is sequential and must see commits in walk
order. Splitting them is what allows the parse pool.

Two implementation notes that are easy to get wrong:

- **`cat-file --batch` must interleave writes and reads.** Writing every request
  before reading any response deadlocks once git fills the 64 KB stdout pipe.
  The existing `_ObjectReader` in `vcs/git.py` already does this correctly; the
  first draft of the benchmark did not, and hung.
- **`--no-renames`** in phase A. gita does its own entity-level rename detection
  and git's file-level guess would pre-empt it with a different threshold.

### 5.4 Incremental update

```
for each tracked ref R with recorded tip T:
    if T == tip(R):                              nothing to do
    elif T is an ancestor of tip(R):             walk T..tip(R), append
    else:                                        history was rewritten (§6.4)
```

Ancestry is `git merge-base --is-ancestor`. The common case — a fetch, a few new
commits — costs 22 ms per commit and touches nothing already written.

**Events are append-only.** No row is ever updated. A build either commits new
rows or fails leaving the index exactly as it was.

### 5.5 Batching and interruption

One transaction per 256 commits. On interruption the index is consistent at the
last committed batch and `tracked` reflects it, so the next run resumes rather
than restarts. Progress is reported to stderr when interactive, per the existing
telemetry convention.

### 5.6 Concurrency

- **Readers** never block. WAL gives them a consistent snapshot during a build.
- **Writers** are serialised by `build.lock` (`O_EXCL`, holding pid and start
  time) plus `BEGIN IMMEDIATE`. A second `gita index` reports the running build
  and exits 0 — two concurrent builds would produce identical rows, so racing is
  wasteful, not incorrect.
- A lock whose pid is dead and whose start time is over an hour old is stale and
  may be broken by the next builder.

---

## 6. Failure modes

Each is a decision, not a discovery.

### 6.1 Missing or unreadable index

Fall back to live computation, warn once on stderr, exit code unchanged. §4.6.

### 6.2 Corruption

`PRAGMA quick_check` on open when the file's mtime is newer than `updated_at`,
otherwise trust it. Any SQLite error at any point: report, delete, rebuild. The
index is derived, so recovery is always "throw it away".

### 6.3 Schema or semantics mismatch

Rebuild, stating which fingerprint changed (§4.4). Never migrate: a migration
would have to reproduce the *old* extraction semantics to be correct, which
means keeping every past grammar.

### 6.4 Rewritten history

A tracked tip that is no longer a descendant of the recorded tip means rebase,
amend, or force-push. Delete events for the commits no longer reachable from any
tracked ref, then walk forward from the merge base. Entities whose genesis was in
a discarded commit are deleted with it; their identifiers are gone, as they
should be.

`git fetch --unshallow` and `git replace` invalidate the walk wholesale and force
a full rebuild.

### 6.5 Unsupported and unparseable files

A file with no registered grammar is not indexed — no rows, no error. A file that
fails to parse records `parse_error` on its module entity and contributes no
child entities, matching today's extractor behaviour exactly.

### 6.6 Very large files and pathological entities

Blobs over 5 MB and entities over 50,000 tokens are skipped with a counter in
`meta`. Parse cost is linear in bytes, but generated files (lockfiles, bundled
JavaScript, vendored code) are pure cost and no user asks about their entity
history.

---

## 7. Query surface

### 7.1 CLI

```
gita index                    bring the index up to date; idempotent
gita index --status           what is indexed, how fresh, how large
gita index --rebuild          discard and rebuild
gita index --verify           check the invariants in §8.1
gita index --since <rev>      bound the walk for very large histories

gita id <entity>              durable identifier for a name, path::name, or eid
gita id --at <rev> <entity>   resolve as of a revision
```

`gita id` is the join key that every later year depends on, so it is the one new
noun Year 1 introduces.

```
$ gita id check_token
9f3a2c14  check_token   security.py:41-47   born 4 commits ago in auth.py as validate_token
```

`gita history` switches to identity when the index is present: resolve the name
to an `eid`, then read that entity's events. This is the fix for the 2-of-5
failure, and it needs no new user-facing syntax.

### 7.2 Resolution order

`gita id <token>` resolves in this order, stopping at the first hit:

1. an `eid` or unique `eid` prefix
2. `path::qualname` exact, at `--at` or `HEAD`
3. entity name exact, alive at `--at` or `HEAD`
4. entity name exact, at any point in history (reported as historical)

Ambiguity lists the candidates and exits 1. It never picks one — §3.3 again.

Every resolution follows `alias_of` to a canonical `eid` before returning.

### 7.3 Python API

```python
class EntityIndex:
    def id_of(self, name: str, at: str = "HEAD") -> list[bytes]
    def history(self, eid: bytes, limit: int | None = None) -> list[Event]
    def state_at(self, eid: bytes, commit: str) -> Version | None
    def events_in(self, base: str, head: str) -> list[Event]
    def entities_in(self, path: str, at: str = "HEAD") -> list[Version]
```

`events_in` is the seam Year 2's `gita query` and `gita merge` are built on.

---

## 8. Verification

### 8.1 Invariants, checked by `gita index --verify`

1. Every `event.eid` has an `entity` row.
2. Every entity has exactly one event whose kind is `added`, at its genesis
   commit.
3. No entity has an event before its genesis commit.
4. No entity has an event after a `removed` event without an intervening
   `added` — resurrection does not happen (§3.4).
5. `commits.id` is dense, ascending, and consistent with first-parent ancestry.
6. Every `alias_of` chain terminates and is acyclic.
7. Recomputing `eid` from `(genesis_commit, genesis_path, genesis_name)`
   reproduces the stored `eid` for every row.

Invariant 7 is the one that matters: it makes the identifier checkable rather
than merely stored.

### 8.2 Determinism

Property test, on every corpus repository:

- **Rebuild equality.** Two independent full builds produce byte-identical
  `entity` and `event` tables.
- **Incremental equality.** Building 1..n, then updating to 1..n+k, produces the
  same tables as building 1..n+k in one pass.

The second is the real risk: an incremental path that diverges from a full
rebuild produces identifiers that depend on when a user ran `gita index`, which
would make every downstream year unreliable. This is git's "repack must not
change what the objects mean", and it is checked the same way — by doing both
and comparing.

### 8.3 Identity accuracy — two answer keys

Recall requires links known to be true, independent of the matcher being tested.

**Key A — synthetic mutation.** Take a real file, apply a known refactor
(rename, move to a new file, rename-and-move, extract, reorder siblings),
commit. Ground truth is exact by construction, and volume is unlimited. Covers
precision and recall, but only over refactors we thought to generate.

**Key B — mined, independently evidenced.** Commits where git's *file*-level
rename detection fires, and a function of the same name exists on both sides.
The link is then established by git's file similarity, not by gita's body
matching — independent evidence. Realistic, but only covers file moves without
renames.

**Rejected — mining by body similarity.** Finding "a function that vanished here
and a similar one that appeared there" uses the hypothesis as its own evidence.
It would score well and mean nothing.

Both keys are reported separately. A combined figure would hide that A is
generous on realism and B is narrow in coverage.

### 8.4 Kill criteria

FIRST-PRINCIPLES set: *warm queries under 100 ms on a 10,000-commit repository,
and identity surviving rename-plus-move across 100 real refactors with recall
above 0.9.*

Two revisions, with reasons.

**Precision joins the criterion, and binds harder than recall.** §3.3: a missed
link is visible and recoverable, a false link is silent and corrupting, and in
Year 4 it enforces a constraint against the wrong function. A matcher that links
everything would score recall 1.0 and be useless.

| | threshold | rationale |
| --- | --- | --- |
| identity precision | **≥ 0.99** | a false link is unrecoverable corruption |
| identity recall | ≥ 0.90 | as originally set |
| warm query, 10k commits | < 100 ms | as originally set |
| full build, 10k commits | < 5 min | measured at 3.7 min sequential (§5.1) |
| incremental update, 100 commits | < 5 s | 22 ms/commit gives ~2.2 s |
| index size, 10k commits | < 50 MB | projected ~7 MB (§4.5) |
| rebuild determinism | exact | §8.2, not a threshold |

**The 10,000-commit repository does not exist in our corpus.** The largest is
express at 1,242 commits. Before the criterion can be evaluated, add one
long-history repository — CPython, Django or Rails — as a benchmark fixture. If
this is skipped, the performance criterion is untested and must not be reported
as met.

### 8.5 What failure means

Precision below 0.99 does not mean tuning thresholds until it passes. It means
the four hashes are insufficient evidence for durable identity, and Years 2–5
are built on sand. The honest response is to narrow the claim — link only within
a file, or only on exact body equality — and accept lower recall, rather than
keep a matcher that quietly fuses unrelated code.

---

## 9. Rejected alternatives

**A custom binary format.** More git-like in spirit, and wrong. We would be
writing B-trees, a free list, crash recovery and a locking protocol — all of
which SQLite has had for twenty years, in the standard library, with no added
dependency. Year 2 is a query layer; starting on a query engine is worth more
than starting on a file format. Revisit only if a measurement says SQLite is the
bottleneck; §5.1 says parsing is.

**Storing every entity at every commit.** O(commits x entities) rows, and it
buys nothing: state at a commit is the last event at or before it, which is one
indexed lookup on a table already sorted by `(eid, commit_id)`.

**A monotonic counter for identifiers.** Needs an allocator, so two clones of
the same history disagree. Content-addressing makes identity a property of the
history rather than of the machine that indexed it.

**Identity from the current name and path** — today's behaviour. Breaks on the
exact operation that matters (§1.1).

**Identity from body hash alone.** Every entity's identity would change whenever
its body did, which is what history *is*.

**Auto-indexing inside every command.** Turns an innocuous `gita diff` into a
four-minute stall. `gita index` is explicit; other commands use the index when
it is fresh, fall back when it is not, and say so once.

**Migrating the schema across semantics changes.** Would require reproducing
historical extraction behaviour to stay correct, which means retaining every
past grammar version. Rebuilding takes minutes and is always right (§6.3).

---

## 10. Milestones

Each is independently useful and independently revertible.

| # | deliverable | proves |
| --- | --- | --- |
| **M1** | Route history walks through bulk enumeration (`Repo.walk`) instead of a `git diff` per commit | ~4x on every command today, no index required (§5.1) |
| **M2** | Schema, open/create, semantics fingerprint, `--status` | the format, and that a mismatch rebuilds |
| **M3** | Sequential builder, phases A–E, first-parent walk | end-to-end build; determinism tests (§8.2) go green |
| **M4** | `gita id`, resolution order, abbreviation | identity is addressable |
| **M5** | `gita history` reads the index | the 2-of-5 failure becomes 5-of-5 |
| **M6** | Incremental update, rewritten-history handling, locking | safe on a working repository |
| **M7** | Parallel parse pool | 10k commits inside the build budget |
| **M8** | Answer keys A and B, benchmark fixture, kill-criteria report | whether Year 1 actually holds |

M1 ships value before any of this exists, and M8 is allowed to fail loudly.

---

## 11. Open questions

1. **Sibling ordinals across commits.** `url#2` becoming `url#3` when a sibling
   is inserted is handled inside a file by `_resolve_group`, but the `qualname`
   at genesis is baked into the `eid`. An entity born as `url#2` keeps that in
   its identifier forever, which is correct but reads oddly in `gita id` output.
   Display concern, not correctness — confirm before M4.
2. **Submodules.** Out of scope in Year 1; they should be neither indexed nor
   an error. Confirm the enumerator skips gitlink entries cleanly.
3. **Case-insensitive filesystems.** `Foo.py` and `foo.py` are distinct paths to
   git and the same file to Windows. Path interning must not normalise case, or
   two entities fuse. Needs a test on Windows specifically.
4. **`meta.walk` enforcement.** If an index built with `first-parent` is later
   queried by something assuming `all`, the answer is quietly partial. Queries
   should carry the walk mode into their output rather than rely on the caller.
