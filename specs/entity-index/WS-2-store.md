# WS-2 — Store

**Delivers:** the persistent index file — schema, open/create, semantics
fingerprint, integrity, locking primitives. No walking, no identity, no queries.

**Depends on:** nothing. Runs in parallel with WS-1.

**Milestone:** M2.

---

## 1. Scope

`src/gita/index/store.py`. Everything about the file on disk and nothing about
what goes in it. WS-3 owns the rows; WS-2 owns the container.

Getting this wrong is expensive later — a schema that cannot express aliases, or
a fingerprint that misses a grammar upgrade, produces silently wrong history
months afterwards. Getting it right is cheap now, because it is almost pure and
almost entirely testable without git.

## 2. Interface

```python
# src/gita/index/store.py

SCHEMA_VERSION = 1

class Staleness(Enum):
    FRESH = "fresh"
    ABSENT = "absent"
    SCHEMA_MISMATCH = "schema"
    SEMANTICS_MISMATCH = "semantics"
    CORRUPT = "corrupt"

@dataclass
class IndexStore:
    path: Path
    conn: sqlite3.Connection

    @classmethod
    def locate(cls, repo: Repo) -> Path: ...
    @classmethod
    def open(cls, repo: Repo, create: bool = False) -> IndexStore | None: ...

    def state(self) -> Staleness: ...
    def discard(self) -> None: ...
    def intern_path(self, value: str) -> int: ...
    def intern_name(self, value: str) -> int: ...
    def meta(self, key: str) -> str | None: ...
    def set_meta(self, key: str, value: str) -> None: ...

    @contextmanager
    def build_lock(self) -> Iterator[None]: ...

def semantics_fingerprint() -> str: ...
```

## 3. Corrections to the design document

Two things the design document got wrong or left implicit. Both are cheap now
and painful later.

**`PRAGMA foreign_keys = ON` is required, and is easy to apply ineffectively.**
SQLite does not enforce foreign keys by default — it parses the constraints and
ignores them. The design document's schema declares six foreign keys that would
never have fired.

Worse, the pragma is **silently ignored when issued inside a transaction**, and
Python's `sqlite3` opens transactions implicitly on the first `INSERT`. Measured:

| `PRAGMA foreign_keys = ON` issued | pragma then reads | orphan insert |
| --- | ---: | --- |
| outside any transaction | 1 | rejected |
| inside an open transaction | **0** | **accepted — orphan row written** |
| connection opened `isolation_level=None` | 1 | rejected |

No error is raised in the failing case. A connection that looks configured
silently permits orphan rows.

Therefore: set the pragma **immediately after connecting, before any statement
that could open a transaction**, and assert it reads back as 1. It is
per-connection, not stored in the file, so this applies on every open — not just
on create.

**`--git-common-dir` returns a relative path.** In a plain checkout it is
`.git`, relative to the process working directory, so it must be resolved
against `--show-toplevel` before use. Verified on this repository.

---

## 4. TDD sequence

### Cycle 0 — acceptance

`tests/test_index_store.py::test_open_create_reopen_roundtrip`

```
given   a repository with no index
when    IndexStore.open(repo, create=True), rows written, closed, reopened
then    state() is FRESH and the rows read back identical
```

Red until the whole workstream lands.

---

### Cycle 1 — location

`::test_index_lives_in_git_common_dir`

```
given   a repository
then    IndexStore.locate(repo) == <toplevel>/.git/gita/index.sqlite
and     the path is absolute
```

Fails on a naive `--git-common-dir` join, which yields a relative `.git/gita/...`
that resolves differently depending on the caller's working directory.

### Cycle 2 — worktrees share one index

`::test_linked_worktree_shares_index`

```
given   a repo and `git worktree add`ed linked worktree
then    locate(main) == locate(linked)
```

`--git-dir` in a linked worktree points at `.git/worktrees/<name>`, which would
give every worktree its own index and re-index the same history N times.
This test is the reason the design says `--git-common-dir`.

### Cycle 3 — creation

`::test_open_creates_schema` / `::test_open_without_create_returns_none`

```
then    all seven tables and three indexes exist
and     PRAGMA user_version == SCHEMA_VERSION
and     open(create=False) on a missing file returns None, raising nothing
```

### Cycle 4 — foreign keys actually enforce

`::test_foreign_keys_enforced`, `::test_pragma_survives_reopen`,
`::test_pragma_set_before_any_transaction`

```
given   an open store
when    an event row is inserted for an eid with no entity row
then    sqlite3.IntegrityError

given   a store closed and reopened
then    the same insert still raises        # pragma is per-connection

given   a connection that has already inserted a row
then    PRAGMA foreign_keys reads 1, not 0  # pragma was set before the txn
```

The third assertion is the one that catches the real bug (§3): a pragma issued
after a transaction has opened reads back as 0 and enforces nothing, with no
error. Asserting only that a fresh store rejects orphans would pass against an
implementation that configures the connection too late.

### Cycle 5 — WAL and concurrency

`::test_wal_mode_enabled`, `::test_reader_not_blocked_by_open_write`

```
then    PRAGMA journal_mode == 'wal'
given   connection A in BEGIN IMMEDIATE having inserted
then    connection B still reads the pre-transaction snapshot without blocking
```

Readers never blocking during a build is a design promise (§5.6); this is where
it is verified rather than assumed.

### Cycle 6 — interning

`::test_intern_is_idempotent`, `::test_intern_preserves_case`

```
then    intern_path("a/b.py") twice returns the same id, one row
and     intern_path("Foo.py") != intern_path("foo.py")
```

The second is design open question 3: on a case-insensitive filesystem these are
one file to Windows and two paths to git. Normalising case here would silently
fuse two entities. Marked `@pytest.mark.windows` but correct everywhere.

### Cycle 7 — semantics fingerprint

`::test_fingerprint_is_stable`, `::test_fingerprint_tracks_threshold`,
`::test_fingerprint_tracks_language_table`

```
then    two calls in one process agree, and across processes agree
given   RENAME_THRESHOLD monkeypatched
then    the fingerprint changes
given   a language removed from the extension table
then    the fingerprint changes
```

The fingerprint's whole job is to notice changes that would alter extraction or
matching. A test that only checks stability would pass for a constant.

Requires `ENTITY_SCHEMA_VERSION` in `entities/model.py` — it does not exist yet
and WS-2 adds it.

### Cycle 8 — staleness classification

`::test_state_absent`, `::test_state_schema_mismatch`,
`::test_state_semantics_mismatch`, `::test_state_corrupt`

```
given   no file                            then ABSENT
given   user_version bumped underneath     then SCHEMA_MISMATCH
given   meta.semantics overwritten         then SEMANTICS_MISMATCH
given   the file truncated to random bytes then CORRUPT, and no exception escapes
```

Each variant is a different recovery path in §6 of the design document, so each
needs its own name in a report. One boolean `is_stale()` would collapse four
distinguishable causes into an unactionable message.

### Cycle 9 — discard is total

`::test_discard_removes_wal_and_shm`

```
when    discard()
then    index.sqlite, -wal and -shm are all gone
```

Leaving a `-wal` beside a deleted database is a classic way to resurrect a
corrupt index on the next open.

### Cycle 10 — build lock

`::test_build_lock_is_exclusive`, `::test_stale_lock_is_broken`,
`::test_lock_released_on_exception`

```
then    a second build_lock() raises BuildInProgress
given   a lock naming a dead pid, older than an hour
then    it is broken and acquired
given   an exception inside the with-block
then    the lock file is gone
```

### Cycle 11 — row round-trip

`::test_event_roundtrip_preserves_hashes`

```
given   an event with four 8-byte hashes and a 16-byte eid
then    reading back yields identical bytes, not hex or str
```

Guards against a `BLOB`/`TEXT` affinity slip, which SQLite will not complain
about and which would break `eid` comparison everywhere.

---

## 5. Implementation notes

- One module, no ORM, explicit SQL. The schema in the design document is
  verbatim and has been executed against SQLite 3.45.1.
- Pragmas immediately after connect, before any other statement:
  `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`. Read
  `foreign_keys` back and fail loudly if it is 0 (§3).
- `quick_check` only when the file's mtime is newer than `meta.updated_at`;
  running it on every open costs a full scan.
- `state()` never raises. Any `sqlite3.DatabaseError` maps to `CORRUPT`, because
  a caller's only sensible response is to discard and rebuild.
- Lock file holds `pid` and start time so Cycle 10's staleness rule is decidable.

## 6. Definition of done

- [ ] Cycles 0–11 green
- [ ] `state()` cannot raise, checked with a fuzzed file
- [ ] no git operations in this module beyond `locate`
- [ ] `gita index --status` prints the state name and, when fresh, size and counts

## 7. Risks

**Fingerprint completeness.** It can only cover inputs we thought of. A
tree-sitter point release changing a grammar's output without changing its
reported version would go unnoticed. Partial mitigation: include the resolved
`tree_sitter_language_pack` distribution version. Residual risk accepted and
recorded here rather than in a comment.

**`synchronous=NORMAL`.** A power loss mid-build can leave the last batch torn.
Acceptable: the index is derived, `quick_check` catches it, and recovery is a
rebuild. Do not raise to `FULL` for a cache.
