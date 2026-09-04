# WS-1 — Enumeration

**Delivers:** one bulk `git log` per history walk instead of one `git diff` per
merge commit. Measured 2.1x on `series`, 2.8x on `entity_history`, over
merge-heavy history. No index involved.

**Depends on:** nothing. Ships first.

**Milestone:** M1.

---

## 1. The problem, measured

`scratch/probe_live_cost.py`, 100 first-parent Flask commit pairs through
`diff_revisions`:

| component | per commit | share |
| --- | ---: | ---: |
| `changed_files` — one `git diff --name-status` per commit pair | 79.5 ms | **75%** |
| blob reads, parse, matching | 26.4 ms | 25% |
| **end to end** | **106.0 ms** | |
| the same enumeration in bulk, one `git log --raw` | 0.8 ms | **95x faster** |

Blob reads are *already* batched through `_ObjectReader`. Enumeration is the
whole problem.

## 2. Why the existing bulk path does not help

`Repo.walk` already asks for commits and their files in one `git log
--name-status` call. It then undoes most of the benefit:

```python
for record in records:
    if record.is_merge:
        record.files = self.changed_files(record.parent, record.sha, ...)
```

Git prints no file list for a merge by default, so each merge costs a process.
On Flask **110 of 150 commits are merges** — the fallback is the common case,
not the exception.

`git log --diff-merges=first-parent` removes it. Verified on git 2.55 against a
real Flask merge: 1 line of output by default, **17 with the flag**, matching
the first-parent diff.

Separately, `history.commits()` does not use `Repo.walk` at all — it runs its own
`git log` for `(sha, subject, date)` and leaves `diff_revisions` to call
`changed_files` per pair. That is the 79.5 ms.

> **Correction, made during implementation.** The paragraph above is wrong, and
> so is the attribution of 79.5 ms/commit to the history path. `series()` has
> always routed through `repo.walk()`; `commits()` is used only by
> `series(batched=False)`, the unbatched reference path. The 79.5 ms came from
> `probe_live_cost.py`, a loop calling `diff_revisions` directly — the
> single-diff path.
>
> The real defect is only the merge fallback, measured directly on flask with
> `scratch/probe_ws1_baseline.py`:
>
> | path | git processes | time |
> | --- | ---: | ---: |
> | `series(limit=100)` | **42** — 41 of them the fallback | 5.81 s |
> | `entity_history` | 16 — 14 fallback | 1.84 s |

## 2a. Result

| path | before | after | |
| --- | ---: | ---: | --- |
| `series(limit=100)` | 42 processes, 5.81 s | **1 process, 2.72 s** | 2.1x |
| `entity_history` | 16 processes, 1.84 s | **3 processes, 0.65 s** | 2.8x |

Below the ~4x in **Delivers** above, which was extrapolated from the wrong
measurement. The honest number is 2–3x on merge-heavy history, and nothing on
linear history, where there was never a fallback to remove.

## 3. Interface

```python
# vcs/git.py
class Repo:
    def walk(self, since=None, until="HEAD", limit=20, paths=None,
             first_parent: bool = False) -> list[CommitRecord]:
        """One git call. Merges included, via --diff-merges=first-parent."""

    @property
    def supports_diff_merges(self) -> bool:
        """git >= 2.31. Probed once per process, cached per Repo."""
```

`history.commits()` is **not** changed. The original plan had it return
`CommitRecord`, on the mistaken premise that the history path went through it.
It is used only by `series(batched=False)`, the unbatched reference path that
exists so tests can prove the two agree. Widening its return type would be churn
with no caller to benefit.

**No output changes anywhere.** That is what makes the tests in §4 necessarily
mechanical.

---

## 4. TDD sequence

### Cycle 0 — the acceptance test, red until the workstream closes

`tests/test_enumeration.py::test_history_walk_costs_one_enumeration_call`

```
given   merge_repo, 12 commits, at least 3 of them merges
when    entity_history(repo, "handle", limit=12) runs
then    exactly one `git log` process is spawned
and     no `git diff` process is spawned
```

Fails today: one `git diff` per commit pair, plus one per merge inside `walk`.

Uses the `git_calls` fixture (§5). Asserting on argv rather than elapsed time
keeps it deterministic on a loaded machine.

---

### Cycle 1 — the counting fixture

`tests/test_enumeration.py::test_git_calls_fixture_records_argv`

```
given   the git_calls fixture wrapping Repo._run
when    repo.changed_files("HEAD^", "HEAD") runs
then    git_calls.of("diff") == 1
```

The fixture is test infrastructure, so it gets its own test first — a
miscounting fixture would make every later cycle meaningless.

Fails until the fixture exists.

---

### Cycle 2 — merges enumerate without a fallback

`tests/test_enumeration.py::test_walk_enumerates_merges_in_one_call`

```
given   merge_repo
when    repo.walk(limit=20)
then    every merge record has a non-empty .files
and     git_calls.of("diff") == 0
```

Fails today on the second assertion only: files are correct, the process count
is not. This is the cycle that proves the point of §2.

---

### Cycle 3 — the flag does not change the answer

`tests/test_enumeration.py::test_merge_files_match_first_parent_diff`

```
given   merge_repo
for     every merge commit M
then    {f.path for f in walk_record(M).files}
        == {f.path for f in repo.changed_files(M.parent, M.sha)}
```

Guards the semantic risk in Cycle 2: `--diff-merges=first-parent` must be
equivalent to the diff it replaces. Passes today (via the fallback), and must
keep passing once the fallback is gone. A test that passes before and after is
normally suspect; here it is a **regression guard on a substitution**, and its
value is that it starts green and must stay green.

---

### Cycle 4 — old git still works

`tests/test_enumeration.py::test_falls_back_when_diff_merges_unsupported`

```
given   supports_diff_merges monkeypatched to False
when    repo.walk(limit=20) on merge_repo
then    merge records still have correct .files
and     git_calls.of("diff") == number of merges
```

`--diff-merges` landed in git 2.31 (2021). The fallback stays, and stays tested;
it just stops being the default path.

---

### Cycle 5 — capability detection is real

`tests/test_enumeration.py::test_supports_diff_merges_detects_version`

```
given   a Repo
then    supports_diff_merges is a bool
and     it is computed once per Repo, not once per walk
```

Detect by parsing `git --version`, not by running a probe command per walk —
that would reintroduce a process per call, which is the bug being fixed.

---

### Cycle 6 — history reuses the enumeration

`tests/test_enumeration.py::test_history_passes_changed_files_through`

```
given   merge_repo
when    entity_history(repo, "handle", limit=10)
then    git_calls.of("diff") == 0
```

Forces `history.py` to hand `record.files` to `diff_revisions(changed=...)`
rather than let it re-derive them. Closes Cycle 0.

---

### Cycle 7 — equivalence across the corpus

`tests/test_enumeration.py::test_walk_equals_per_commit_on_corpus`

```
given   corpus (parametrised, skipped if absent)
for     the last 50 first-parent commits
then    walk-derived changed files == changed_files(parent, sha), exactly
```

The property test behind the example in Cycle 3, on real history with real
renames, mode changes, submodules and binary files.

---

### Cycle 8 — no regression in what users see

`tests/test_enumeration.py::test_history_output_unchanged`

```
given   the repo fixture
then    entity_history output before and after WS-1 is identical
```

Captured as a golden value in the test, not compared across git revisions.
WS-1 is a pure speedup; any output difference is a bug.

---

## 5. Fixtures

```python
@pytest.fixture
def git_calls(monkeypatch):
    """Record every git subprocess argv issued through Repo._run.

    WS-1 changes performance, not output, so its tests must assert on the
    mechanism. Elapsed time is too noisy to assert on; process count is exact.
    """
    calls: list[tuple[str, ...]] = []
    original = Repo._run

    def recording(self, *args, check=True):
        calls.append(args)
        return original(self, *args, check=check)

    monkeypatch.setattr(Repo, "_run", recording)

    class Calls:
        argv = calls
        def of(self, subcommand): ...   # count matching argv[0]

    return Calls()
```

```python
@pytest.fixture
def merge_repo(tmp_path):
    """A repository whose history is mostly merges, as real projects are.

    main gets 4 commits; three side branches each add one and merge back with
    --no-ff. Flask is 110 merges in 150 commits; a linear fixture would not
    exercise the path that dominates.
    """
```

---

## 6. Implementation notes

Written after the tests, and only as much as the tests demand.

- Add `--diff-merges=first-parent` to the `git log` in `walk` when
  `supports_diff_merges`; delete the merge fallback loop from the same method.
- `first_parent=True` adds `--first-parent`. The builder (WS-3) always wants it;
  today's callers do not.
- Detect the git version once per `Repo`, from `git --version`, cached on the
  instance.
- `history.commits()` returns `CommitRecord`. Its three current call sites move
  from tuple unpacking to attribute access; `CommitRecord` already carries
  `sha`, `subject`, `date`, `parent`, `is_merge`, `files`.
- `diff_revisions` needs no change — it already takes `changed=`.

## 7. Definition of done

- [x] Cycles 0–8 green — 28 tests in `tests/test_enumeration.py`
- [x] `git_calls.of("diff") == 0` for any pure history walk
- [x] existing suite green, no output diffs — 563 passed
- [x] equivalence holds on all five corpus repositories
- [x] `probe_ws1_baseline.py` re-run and recorded (§2a)
- [x] hardened beyond the original cycles: octopus merges, repositories with no
      commits, a missing git binary, and `first_parent=True`

## 8. Risks

**`--diff-merges=first-parent` semantics.** For an evil merge — one that
resolves conflicts by introducing changes present in neither parent — the
first-parent diff includes the resolution. That is correct and is what we want;
Cycle 3 pins it against the diff it replaces so a change in git's behaviour
surfaces as a test failure rather than a silent shift in history.

**`CommitRecord` is a wider return type.** Callers outside `history.py` may
unpack the old triple. Cycle 8 plus the existing suite covers this; grep before
changing the signature.
