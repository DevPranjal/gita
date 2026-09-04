# Entity index — implementation specs

Implementation of [SPEC-ENTITY-INDEX.md](../../scratch/SPEC-ENTITY-INDEX.md)
(Year 1 of [FIRST-PRINCIPLES.md](../../scratch/FIRST-PRINCIPLES.md)).

The design document says *what* and *why*. These say *how*, *in what order*, and
*how we know it works*.

---

## The workstreams

| | workstream | delivers | depends on |
| --- | --- | --- | --- |
| **WS-1** | [Enumeration](WS-1-enumeration.md) | one bulk `git log` per walk instead of a `git diff` per commit | — |
| **WS-2** | [Store](WS-2-store.md) | the SQLite index: schema, open, fingerprint, integrity | — |
| **WS-3** | [Builder](WS-3-builder.md) | the walk, identity chaining, events, incremental update | WS-1, WS-2 |
| **WS-4** | [Query and CLI](WS-4-query.md) | `gita id`, index-backed `gita history`, `gita index --status` | WS-2, WS-3 |
| **WS-5** | [Verification](WS-5-verification.md) | answer keys, invariant checker, benchmark, kill-criteria report | WS-3, WS-4 |

```
WS-1 ──┐
       ├──> WS-3 ──> WS-4 ──> WS-5
WS-2 ──┘
```

WS-1 and WS-2 are independent and can run in parallel. WS-1 ships a ~4x
speedup on its own, before any index exists.

---

## Test-driven, and what that means here

**No production line is written before a test that fails without it.**

Each workstream below is a numbered sequence of red-green cycles. Each cycle
names the test, the file it lives in, what it asserts, and — the part that is
usually skipped — **why it fails today**. A cycle whose test passes before the
implementation is written is not a test, it is a description, and it must be
rewritten or deleted.

Three rules specific to this project:

1. **Every workstream opens with its acceptance test**, written first and left
   red until the workstream closes. It is the definition of done, expressed as
   code rather than prose.
2. **Assert mechanism, not just outcome, where the mechanism is the
   requirement.** WS-1 is a performance change whose output is unchanged, so its
   tests count git subprocesses. A test that only checks the answer would pass
   before and after and prove nothing.
3. **Determinism gets property tests, not examples.** "Rebuild produces the same
   identifiers" is checked by building twice and comparing tables, over every
   corpus repository — not by one hand-written case.

### Conventions

Matching the existing suite (`tests/conftest.py`, `pyproject.toml`):

- pytest; `testpaths = ["tests"]`, `pythonpath = ["src"]`
- `from __future__ import annotations` at the top of every test module
- fixtures build **real** git repositories under `tmp_path` — no mocking of git
- a module docstring saying why the module exists, and a docstring on any test
  whose reason is not obvious from its name
- new test modules: `tests/test_index_store.py`, `tests/test_index_builder.py`,
  `tests/test_index_query.py`, `tests/test_index_verify.py`,
  `tests/test_enumeration.py`

### Shared fixtures to add to `tests/conftest.py`

| fixture | purpose | first needed |
| --- | --- | --- |
| `git_calls` | wraps `Repo._run` and records argv; asserts on subprocess count | WS-1 |
| `merge_repo` | a repository with a real merge commit and a first-parent line | WS-1 |
| `refactor_repo` | the §1.1 ground truth: born, edited, renamed, moved, edited | WS-3 |
| `indexed_repo` | `refactor_repo` with the index built | WS-4 |
| `corpus` | parametrised over `spikes/attribution/corpus/*`, skipped if absent | WS-3, WS-5 |

`refactor_repo` is the fixture form of `scratch/probe_identity.py`, which
already builds exactly this history.

---

## Sequencing

Ship in this order. Each row is independently revertible and independently
useful.

| step | workstream | milestone | user-visible effect |
| --- | --- | --- | --- |
| 1 | WS-1 | M1 | every history command ~4x faster, no index |
| 2 | WS-2 | M2 | none yet; `gita index --status` says "not built" |
| 3 | WS-3 | M3 | `gita index` works; determinism tests green |
| 4 | WS-4 | M4, M5 | `gita id` exists; `gita history` answers 5 of 5 |
| 5 | WS-3 | M6 | incremental update, safe on a live repository |
| 6 | WS-3 | M7 | parallel parse; 10k commits inside budget |
| 7 | WS-5 | M8 | kill criteria measured and reported, pass or fail |

Step 7 is allowed to fail. If it does, §8.5 of the design document applies:
narrow the claim, do not tune thresholds until the number is met.

---

## Definition of done for Year 1

- [ ] `gita history` returns 5 of 5 events on `refactor_repo` (today: 2 of 5)
- [ ] rebuild determinism and incremental-equals-full hold on every corpus repo
- [ ] identity precision ≥ 0.99, recall ≥ 0.90, reported separately for keys A and B
- [ ] warm query < 100 ms and full build < 5 min on a 10,000-commit fixture
- [ ] `gita index --verify` checks all seven invariants
- [ ] deleting the index changes no answer, only latency
