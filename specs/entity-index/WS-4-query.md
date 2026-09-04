# WS-4 — Query and CLI

**Delivers:** `EntityIndex` — the read API — plus `gita id`, index-backed
`gita history`, and `gita index --status`.

**Depends on:** WS-2 (store), WS-3 (rows to read).

**Milestones:** M4, M5.

---

## 1. Scope

`src/gita/index/query.py` and the CLI wiring in `src/gita/cli/__init__.py`.

WS-4 introduces the one new noun Year 1 adds — `gita id` — and makes
`gita history` correct. It also enforces the invariant that pays for Year 4:
**the index is a cache, and removing it changes no answer.**

## 2. Interface

```python
# src/gita/index/query.py

class EntityIndex:
    @classmethod
    def open(cls, repo: Repo) -> EntityIndex | None:
        """None when absent or stale; callers fall back to live computation."""

    def id_of(self, token: str, at: str = "HEAD") -> list[bytes]
    def history(self, eid: bytes, limit: int | None = None) -> list[Event]
    def state_at(self, eid: bytes, commit: str) -> Version | None
    def events_in(self, base: str, head: str) -> list[Event]
    def entities_in(self, path: str, at: str = "HEAD") -> list[Version]
    def abbreviate(self, eid: bytes) -> str
    def expand(self, prefix: str) -> list[bytes]
```

`events_in` is the seam Year 2's `gita query` and `gita merge` are built on;
it is specified now so those do not need a schema change.

CLI:

```
gita id <entity> [--at <rev>]
gita index --status
gita history <entity>            # unchanged syntax, index-backed when fresh
```

---

## 3. TDD sequence

### Cycle 0 — acceptance

`tests/test_index_query.py::test_id_then_history_round_trip`

```
given   indexed_repo (refactor_repo, built)
when    `gita id check_token` then `gita history` on the returned eid
then    5 events, oldest first, and the oldest names auth.py / validate_token
```

The user-facing form of WS-3 Cycle 0.

---

### Cycle 1 — resolve a current name

`::test_id_of_current_name`

```
given   indexed_repo
then    id_of("check_token") returns exactly one eid
```

### Cycle 2 — resolve a name the entity no longer has

`::test_id_of_historical_name`

```
given   indexed_repo, where validate_token was renamed to check_token
then    id_of("validate_token") returns the same eid as id_of("check_token")
```

**The test that justifies `event_by_name` indexing every name an entity ever
had.** An agent reading an old commit message, an old review, or a stale doc
has the old name; requiring the current one would defeat the point.

### Cycle 3 — ambiguity is reported, never resolved

`::test_ambiguous_name_returns_all`, `::test_cli_ambiguous_exits_1`

```
given   two distinct entities both named `handle`
then    id_of("handle") returns both eids
and     the CLI lists both and exits 1, picking neither
```

Design §7.2 and §3.3, at the query layer: refusing beats guessing.

### Cycle 4 — resolution order

`::test_resolution_order_prefers_eid_then_path_then_name`

```
given   a repo where the string "app" is a valid name and a path prefix
then    an eid prefix wins over path::qualname, which wins over a live name,
        which wins over a historical name
and     a historical-only match is flagged as historical in the result
```

Table-driven over the four levels, because a resolution order that is only
implied by code drifts.

### Cycle 5 — abbreviation

`::test_abbreviate_is_unique_prefix`, `::test_expand_rejects_ambiguous_prefix`,
`::test_minimum_abbreviation_is_eight`

```
then    abbreviate() returns the shortest prefix unique in this repo, min 8
and     expand() on an ambiguous prefix returns all matches, not the first
and     a full 32-hex eid always resolves
```

Same contract as git's abbreviated SHAs, and the same failure mode if the
uniqueness check is skipped.

### Cycle 6 — aliases are followed

`::test_resolution_follows_alias_chain`

```
given   an entity row with alias_of set by hand
then    id_of returns the canonical eid
```

`alias_of` is always NULL in Year 1 (design §3.5). The resolution path is built
and tested now so enabling it later is not a migration. Fixture writes the row
directly — this is the one place a test may bypass the builder.

### Cycle 7 — the index is a cache

`::test_history_identical_with_and_without_index`

```
given   any corpus repository
when    entity_history runs with the index, then with it deleted
then    the two results are equal
```

**The invariant that keeps Year 4 cheap** (design §4.6). If it ever fails, the
index has become authoritative and `rm -rf .git/gita` is destroying user data.
Property test over `corpus`, marked slow.

Note the asymmetry with WS-3 Cycle 0: there the index makes the answer
*correct*, here it must not make it *different*. Both hold because the live path
is fixed to be identity-based too — the index only makes it fast. If the live
path cannot follow identity, this test fails and the design needs revisiting
before M5 closes.

### Cycle 8 — staleness is visible, not fatal

`::test_stale_index_falls_back_and_warns_once`

```
given   an index whose semantics fingerprint no longer matches
then    EntityIndex.open() returns None
and     the command still answers, from the live path
and     exactly one warning is written to stderr, not stdout
```

Once, and on stderr, so a warning cannot corrupt piped or `--json` output.

### Cycle 9 — the walk mode travels with the answer

`::test_history_reports_walk_mode`

```
given   an index built first-parent
then    --json output carries walk: "first-parent"
```

Design open question 4. An answer that is partial by construction must say so
where a machine can read it, rather than rely on the caller remembering.

### Cycle 10 — range and file queries

`::test_events_in_range`, `::test_entities_in_file_at_revision`

```
then    events_in(base, head) equals the events the live walk produces
and     entities_in(path, at) equals extract_path at that revision
```

Both are checked against the live path rather than a golden file, so they cannot
drift from the engine.

### Cycle 11 — CLI surface

`::test_cli_id_output_format`, `::test_cli_id_json`, `::test_cli_index_status`

```
gita id check_token
9f3a2c14  check_token  security.py:41-47  born 4 commits ago in auth.py as validate_token
```

```
then    --json emits eid, name, path, lines, genesis {commit, path, name}
and     `gita index --status` prints state, commits, entities, events, size, updated
and     status on a missing index says so and exits 0
```

Exit 0 for "not built" — it is a legitimate state, not an error, and scripts
should not have to special-case it.

### Cycle 12 — performance guard

`::test_warm_query_under_budget` — marked slow, benchmark fixture

```
given   the 10,000-commit fixture, indexed
then    id_of + history complete in < 100 ms
```

The kill criterion from the design document, as an executable assertion. It
cannot run until WS-5 provides the fixture; until then it is skipped with a
message saying why, never silently passed.

---

## 4. Fixtures

```python
@pytest.fixture
def indexed_repo(refactor_repo):
    """refactor_repo with the index built. The common WS-4 starting point."""
```

```python
@pytest.fixture
def ambiguous_repo(tmp_path):
    """Two distinct entities sharing a name, in different files.

    Exists because the interesting behaviour of a resolver is what it does when
    it cannot decide.
    """
```

## 5. Implementation notes

- `EntityIndex.open` returns `None` for every unusable state. Callers branch
  once, on `None`, rather than inspecting `Staleness` — that stays a `--status`
  concern.
- `gita history` resolves the name to an eid, then reads events. No new syntax.
- Abbreviation length is computed per repository from the eid population, cached
  for the process.
- The warning in Cycle 8 goes through the existing render layer so `--json` and
  `--no-color` are honoured.

## 6. Definition of done

- [ ] Cycles 0–11 green; Cycle 12 green or explicitly skipped pending WS-5
- [ ] `gita history` on `refactor_repo` returns 5 events
- [ ] deleting `.git/gita/` changes no output anywhere in the suite
- [ ] `gita id` documented in `README.md` and the MCP tool list

## 7. Risks

**The live path must also be identity-aware.** Cycle 7 requires the same answer
with and without the index, and Cycle 0 requires that answer to be 5 of 5. So
`entity_history` has to follow renames live, using the same
`diff_trees`/`reconcile_moves` output, with the index only removing the walk.
If that turns out to be too slow to be usable, the honest fix is to say the
index is required for `gita history` and drop the cache invariant for that one
command — a decision worth escalating, not absorbing quietly.

**`gita id` on a large repository with a common name.** `id_of("handle")` could
return thousands of eids. Cap the returned list, report the count, and sort by
most recent event.
