# WS-3 — Builder

> **Historical plan, superseded 2026-09-14.** Follow
> [FOUNDATION.md](FOUNDATION.md), especially F2/F3, for new implementation.
> Persisting the existing diff matcher's decisions verbatim is no longer the
> design. Strict evidence-backed lineage, exact occurrence state, isolated
> scopes and atomic publication replace the contracts below.

**Delivers:** the walk, identity assignment, event writing, incremental update,
rewritten-history recovery, and the parallel parse pool.

**Depends on:** WS-1 (enumeration), WS-2 (store).

**Milestones:** M3, M6, M7.

---

## 1. Scope

`src/gita/index/builder.py`. This is where Year 1 either works or does not: it
is the component that stops throwing identity away.

**No matching logic is written here.** `diff_trees` and `reconcile_moves`
already produce `renamed` and `moved` correctly — verified in
`scratch/probe_moves.py`, which shows both being detected on the very history
`gita history` then fails to follow. WS-3 persists their output. If a cycle
below tempts you to edit `diff/differ.py`, the design has drifted.

## 2. Interface

```python
# src/gita/index/builder.py

@dataclass
class BuildStats:
    commits: int
    entities_created: int
    events: int
    skipped_large: int
    parse_errors: int
    elapsed: float

def build(repo: Repo, store: IndexStore, *, refs: list[str] = ["HEAD"],
          since: str | None = None, workers: int = 0,
          progress: Callable[[int, int], None] | None = None) -> BuildStats: ...

def genesis_id(commit_sha: bytes, path: str, qualname: str) -> bytes:
    """BLAKE2b-128 over the birth triple. §3.1 of the design document."""
```

`workers=0` means sequential — the default until M7, and the reference
implementation the parallel path is checked against.

---

## 3. TDD sequence

### Cycle 0 — the acceptance test

`tests/test_index_builder.py::test_history_finds_all_five_events`

```
given   refactor_repo -- born as validate_token in auth.py, body edited,
        renamed to check_token, moved to security.py, body edited again
when    the index is built and entity_history(repo, "check_token") is asked
then    5 events are returned, oldest first
and     the oldest is `added` at the genesis commit, in auth.py, named validate_token
```

**This is the whole workstream in one test.** Today it returns 2, and
`git log --follow` returns 5 (FIRST-PRINCIPLES §1.1). It stays red until M5,
and it is the number quoted when Year 1 is called done.

`refactor_repo` is `scratch/probe_identity.py` turned into a fixture.

---

### Cycle 1 — the identifier

`::test_genesis_id_matches_specification`

```
given   a known commit sha, path and qualname
then    genesis_id(...) equals a hard-coded 32-hex constant
and     it is 16 bytes
and     changing any one of the three inputs changes it
```

The constant is computed once by hand from the §3.1 definition and pasted in.
Deriving the expected value with the same code under test would assert only that
the function is deterministic.

### Cycle 2 — birth

`::test_added_entity_gets_genesis_event`

```
given   a repo with one commit adding one function
when    built
then    one entity row, genesis_commit is that commit
and     exactly one event, kind `added`
and     entity.eid == genesis_id(commit, path, qualname)
```

### Cycle 3 — identity survives a body change

`::test_body_change_reuses_identity`

```
given   two commits, second edits the body
then    one entity row, two events
and     the second event kind is `body_changed`
```

The first cycle that could pass by accident — a fresh eid per commit would give
two entity rows, so assert the row count, not just the event count.

### Cycle 4 — identity survives a rename

`::test_rename_preserves_identity`

```
given   a function renamed in place, body unchanged
then    still one entity row
and     the second event kind is `renamed`, with the new name
and     the entity is findable by both names
```

### Cycle 5 — identity survives a move

`::test_move_across_files_preserves_identity`

```
given   a function moved to another file, body unchanged
then    still one entity row
and     the event kind is `moved`, carrying the new path
and     genesis_path is still the original file
```

Cycles 4 and 5 are the two operations git tracks by heuristic and gita is
supposed to track by identity. `probe_moves.py` shows the engine already
classifies both; these prove the classification is now *kept*.

### Cycle 6 — rename and move together

`::test_rename_and_move_preserves_identity`

The combination, because they are matched by different passes —
`diff_trees` within a file, `reconcile_moves` across files — and the composition
is where a link is most likely to drop.

### Cycle 7 — ambiguity mints, never guesses

`::test_identical_bodies_do_not_fuse`

```
given   three functions with byte-identical bodies (`return None`)
when    one is deleted and an unrelated one added in the same commit
then    no identity is carried across
and     the new function has its own eid
```

The single most important negative test in Year 1. A false link is silent and
corrupting (design §3.3); this is the assertion that we prefer a missed link.
`_unique_by` and `MIN_BODY_TOKENS` already implement the refusal — this pins it.

### Cycle 8 — death and resurrection

`::test_removed_records_death_event`, `::test_resurrection_mints_new_identity`

```
then    deletion writes a `removed` event and keeps the entity row and history
and     re-adding an identical function later creates a *second* entity
```

Resurrection minting a new identity is a decision (design §3.4), not an
accident. Without a test it would be read as a bug and "fixed" into a false link.

### Cycle 9 — rebuild determinism

`::test_rebuild_is_byte_identical` — property test over `corpus`

```
given   any corpus repository
when    built twice into two separate files
then    the entity and event tables are identical, row for row
```

### Cycle 10 — incremental equals full

`::test_incremental_matches_full_rebuild` — property test over `corpus`

```
given   a repository of n commits
when    built to n-k, then updated to n
then    the tables equal a single full build to n
```

**The invariant that matters most.** If it fails, identifiers depend on when a
user happened to run `gita index`, and every later year is built on sand. This
is git's "repack must not change what the objects mean", checked the same way.

### Cycle 11 — first-parent walk

`::test_side_branch_commits_not_indexed`, `::test_merge_records_branch_effect`

```
given   merge_repo
then    commits reachable only via a second parent have no events
and     the merge commit carries the whole first-parent effect of the branch
and     meta.walk == "first-parent"
```

Pins the tradeoff in design §5.2 so it is a documented choice rather than a
surprise.

### Cycle 12 — resumability

`::test_interrupted_build_resumes`

```
given   a build interrupted after the first 256-commit batch
then    the index is consistent, tracked reflects the last batch
when    build() runs again
then    it starts from that batch, and the result equals an uninterrupted build
```

Interruption is simulated by raising from the progress callback — no signals, no
sleeps, deterministic.

### Cycle 13 — rewritten history

`::test_rebase_truncates_and_rewalks`, `::test_amend_of_tip_handled`

```
given   an indexed repo, then a rebase that rewrites the last 3 commits
when    build() runs
then    events for unreachable commits are gone
and     the result equals a full rebuild of the new history
```

### Cycle 14 — concurrency

`::test_second_build_is_refused`

```
given   a build lock held
when    build() is called
then    it reports the running build and exits 0
```

Two builds would write identical rows, so racing is waste, not corruption —
hence exit 0, not an error.

### Cycle 15 — hostile inputs

`::test_unsupported_file_produces_no_rows`,
`::test_parse_error_records_module_only`,
`::test_oversized_blob_skipped_and_counted`

```
then    a .bin file yields nothing and no error
and     a syntactically broken .py yields its module entity and no children
and     a 6 MB blob is skipped and counted in BuildStats.skipped_large
```

Matches existing extractor behaviour exactly; the point is that the builder does
not turn a tolerated condition into a failed build.

### Cycle 16 — parallel equals sequential (M7)

`::test_parallel_parse_matches_sequential` — property test over `corpus`

```
given   any corpus repository
when    built with workers=0 and workers=8
then    the tables are identical
```

Parsing is pure in `(blob, path)`, so the pool may reorder freely — but identity
chaining must stay sequential. This test is what stops someone parallelising
phase D.

---

## 4. Fixtures

```python
@pytest.fixture
def refactor_repo(tmp_path):
    """The §1.1 ground truth, as a fixture.

    Five commits, one function: born as validate_token in auth.py, body edited,
    renamed to check_token, moved to security.py, body edited again.
    git log --follow finds all five; gita finds two. This is the history Year 1
    exists to get right.
    """
```

```python
@pytest.fixture(params=sorted(CORPUS.iterdir()) if CORPUS.exists() else [])
def corpus(request):
    """Real repositories, skipped when the corpus is not checked out."""
```

`corpus` tests take minutes. Mark them `@pytest.mark.slow`, excluded from the
default run and required in CI.

## 5. Implementation notes

Phases, per design §5.3:

```
A  enumerate   repo.walk(first_parent=True)      one git call (WS-1)
B  read        _ObjectReader, interleaved        already correct
C  parse       (blob, path) -> EntityTree        pure; pooled at M7
D  chain       diff_trees + reconcile_moves      sequential, in walk order
E  commit      one transaction per 256 commits
```

- Phase D holds one in-memory map `(path, qualname) -> eid`, which is why the
  walk must be linear (design §5.2).
- **Interleave writes and reads in phase B.** Writing every request before
  reading any response deadlocks on git's 64 KB stdout pipe — this happened
  while benchmarking for the design document. `_ObjectReader` already does it
  correctly; use it rather than a new batch reader.
- `--no-renames` in phase A: gita does entity-level rename detection and git's
  file-level guess would pre-empt it at a different threshold.
- Events are append-only. No `UPDATE` anywhere in this module except
  `meta`/`tracked`.

## 6. Definition of done

- [ ] Cycles 0–16 green, Cycle 0 quoted as "5 of 5"
- [ ] Cycles 9, 10, 16 green on every corpus repository
- [ ] no diff to `diff/differ.py` (§1)
- [ ] `probe_index_cost.py` re-run: build ≤ 25 ms/commit sequential

## 7. Risks

**Ordinal drift.** A sibling inserted before `url#2` shifts it to `url#3`.
`_resolve_group` handles this inside a file, but the qualname at genesis is
baked into the eid. Design open question 1 — confirm the display before M4;
correctness is unaffected because the eid never changes once minted.

**Merge-heavy repositories.** With `--first-parent`, each merge's diff is the
whole branch, so one commit can produce hundreds of events. Cycle 11 asserts
correctness; watch `BuildStats.events` on express, which is merge-heavy.

**The in-memory map on a monorepo.** One entry per live entity. At ~50k entities
this is single-digit MB; at 5M it is not. `--since` bounds it, and
`BuildStats` should report peak map size so the ceiling is measured before it is
hit.
