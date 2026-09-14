# WS-5 — Verification

> **Historical plan, superseded 2026-09-14.** Follow
> [FOUNDATION.md](FOUNDATION.md), especially F4 and its verification gates.
> Compare canonical logical exports, not SQLite file bytes. Git file-rename
> detection is a lead for independent entity labels, not an identity oracle.
> Unmeasured performance and accuracy gates remain unverified.

**Delivers:** `gita index --verify`, the two identity answer keys, the
long-history benchmark fixture, and the kill-criteria report.

**Depends on:** WS-3, WS-4.

**Milestone:** M8.

---

## 1. Scope

`src/gita/index/verify.py` and `evals/identity/`.

This workstream is allowed to fail, and saying so up front is the point. Every
other workstream is judged on whether its tests pass; this one is judged on
whether it measures the right thing honestly. If precision comes in at 0.94, WS-5
has succeeded and Year 1 has not.

Design §8.5 governs the response: narrow the claim, do not tune thresholds until
the number is met.

## 2. Interface

```python
# src/gita/index/verify.py
@dataclass
class Violation:
    invariant: int
    detail: str
    eid: bytes | None

def verify(store: IndexStore) -> list[Violation]: ...
```

```
gita index --verify        exit 0 clean, 1 with violations listed
```

```python
# evals/identity/keys.py
def key_a(repo: Path, n: int) -> list[Link]    # synthetic, ground truth exact
def key_b(repo: Path) -> list[Link]            # mined, independently evidenced
def score(links: list[Link], index: EntityIndex) -> Score  # precision, recall
```

---

## 3. TDD sequence — the invariant checker

Each cycle corrupts a good index in one specific way and asserts that exactly
that violation is reported. A checker tested only against valid data is a
checker that has never been observed to detect anything.

### Cycle 1 — a clean index verifies clean

`::test_verify_passes_on_good_index` — over `corpus`. Guards against a checker
that reports violations on healthy data, which would be worse than none.

### Cycles 2–8 — one per invariant

| cycle | corruption injected | invariant |
| --- | --- | --- |
| 2 | delete an `entity` row, keep its events | 1 — every event has an entity |
| 3 | delete an entity's `added` event | 2 — exactly one genesis event |
| 4 | insert an event before genesis | 3 — no event precedes genesis |
| 5 | insert an event after `removed` with no re-`added` | 4 — no resurrection |
| 6 | renumber a commit id out of sequence | 5 — dense, ascending, first-parent |
| 7 | point two entities' `alias_of` at each other | 6 — chains terminate |
| 8 | overwrite an `eid` with a wrong hash | 7 — eid recomputes from genesis |

Cycle 8 is the one that matters: invariant 7 makes the identifier *checkable*
rather than merely stored, so a builder bug that mints a wrong eid is detectable
after the fact rather than inferred from strange history.

### Cycle 9 — violations are specific

`::test_violations_name_the_entity`

```
then    each Violation carries the invariant number and the offending eid
```

"Index is invalid" is not actionable on a 2-million-row table.

---

## 4. TDD sequence — the answer keys

### Cycle 10 — key A generates what it claims

`::test_key_a_mutations_are_what_they_say`

```
given   a source file and a requested rename-and-move
then    the produced commit renames exactly that entity and moves exactly that file
and     the recorded Link matches the mutation actually committed
```

The key is the measuring instrument. A generator whose recorded ground truth
drifts from what it committed would produce a confident, meaningless score.

Mutations: rename in place; move to a new file; rename and move; extract a
helper; reorder siblings; rename a sibling of a same-named group.

### Cycle 11 — key A scores a known-good and a known-bad matcher

`::test_key_a_scores_perfect_matcher_at_one`,
`::test_key_a_scores_null_matcher_at_zero`

```
given   an oracle that returns the ground-truth link
then    precision == recall == 1.0
given   a matcher that never links
then    recall == 0.0 and precision is undefined, reported as such, not 0 or 1
```

Calibrates the scorer at both ends before it is pointed at gita. Undefined
precision must be reported as undefined — scoring it 1.0 flatters a matcher that
does nothing, which is exactly the failure mode §3.3 warns about.

### Cycle 12 — key B is independently evidenced

`::test_key_b_links_come_from_file_level_evidence`

```
given   a corpus repository
then    every mined Link is justified by git's *file* rename detection plus an
        unchanged entity name
and     no Link is justified by entity body similarity
```

Design §8.3 rejects mining by body similarity as circular — it uses the
hypothesis as its own evidence. This test enforces that at the code level, so
the shortcut cannot be reintroduced later by someone widening the miner to
"improve coverage".

### Cycle 13 — the keys are reported separately

`::test_report_does_not_merge_keys`

```
then    the report carries key A and key B scores separately, with counts
and     no combined figure is emitted
```

A is generous on realism, B is narrow on coverage. A single number would hide
both. Enforced in code because the temptation to average appears every time
someone wants one headline.

---

## 5. TDD sequence — benchmark and report

### Cycle 14 — the fixture exists

`::test_benchmark_fixture_available`

```
then    a repository of >= 10,000 commits is present, or the test skips
        with a message naming what to clone
```

**The corpus tops out at express, 1,242 commits.** The 10,000-commit criterion
cannot be evaluated today. `evals/identity/fetch.py` clones a pinned commit of
CPython or Django into `spikes/benchmark/`, and the criterion is reported as
*untested* until it does — never as passed.

### Cycle 15 — the report has a fixed shape

`::test_kill_criteria_report_shape`

```
then    the report contains one row per criterion with: measured value,
        threshold, and a verdict of pass / fail / untested
and     `untested` is a distinct verdict, not a silent omission
```

| criterion | threshold |
| --- | --- |
| identity precision | ≥ 0.99 |
| identity recall | ≥ 0.90 |
| warm query, 10k commits | < 100 ms |
| full build, 10k commits | < 5 min |
| incremental update, 100 commits | < 5 s |
| index size, 10k commits | < 50 MB |
| rebuild determinism | exact |
| incremental equals full | exact |

### Cycle 16 — the report is honest about failure

`::test_failing_criterion_is_not_rounded_up`

```
given   a measured precision of 0.985 against a 0.99 threshold
then    the verdict is `fail`, and no note softens it
```

Written because this project has withdrawn four claims already, and the pattern
each time was a number presented more kindly than it deserved.

---

## 6. Running it

```
gita index --verify
python -m evals.identity --repo <path> --keys a,b --report evals/identity/RESULTS.md
```

Regenerated on demand, committed as evidence, dated. Same discipline as
`evals/RESULTS.md`.

## 7. Definition of done

- [ ] Cycles 1–16 green
- [ ] `gita index --verify` clean on every corpus repository
- [ ] identity precision and recall reported for keys A and B, separately
- [ ] benchmark fixture present, or every performance criterion marked `untested`
- [ ] the report committed with the date and the gita version that produced it

## 8. Risks

**Key A is easier than reality.** Generated refactors are clean: one operation,
committed alone. Real ones arrive tangled with unrelated edits. Key A's precision
will therefore read high. Mitigation: generate a variant that bundles each
mutation with unrelated edits in the same commit, and report it as a third
column rather than folding it into A.

**Key B is narrow.** It can only find file moves where the entity name did not
change, so it says nothing about renames — the case that matters most. Its
coverage must be stated next to its score, or a high B number will be read as
reassurance it cannot provide.

**A passing report is not a working product.** These keys measure identity
accuracy on refactors we can construct or mine. They say nothing about whether
identity is *useful*, which is Year 2's problem and Year 4's bet.
