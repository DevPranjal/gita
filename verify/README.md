# Verification

Run from the repository root after installing the project and grammars as shown
in [the README](../README.md). These commands use unique retained temporary
fixtures; they do not change the repository being developed.

```powershell
.venv/Scripts/python.exe verify/check.py --stage f1
.venv/Scripts/python.exe verify/check.py --stage f2
.venv/Scripts/python.exe verify/check.py --stage f3
```

| Checkpoint | What it checks |
| --- | --- |
| `f1` | Original committed bytes, generation publication/reopen, rollback and protected GC. |
| `f2` | Five events on one lineage, fresh-process SQLite inspection, retained-copy refusal and observed-boundary provenance. |
| `f3` | Real CLI prefix build/resume/no-op, old pinned cursor, current/historical lookup, five-event history, exact source slices, budget errors, missing-index no-write behavior and F4 refusal. |
| `f1-evidence` | Independent Python AST byte spans, cache round trip, literal/coordinate evidence, parse errors and storage guards. |
| `f1-worker` | Isolated parsing, deadline refusal, transaction rollback and exact retry identity. |
| `f2-events` | Independent transition flags on explicitly supplied accepted endpoints. |

F3 resumes three commits to five by parsing **three changed path/blob pairs**:
the retained source file becomes comment-only, the moved file has a new path,
and its body changes again. The old prefix is not reparsed. An identical-tip
request then reports zero parses and zero cache reads.

The F3 command intentionally confirms that `index --verify` returns exit 3 with
`verification_unavailable`. That PASS means the missing gate is reported honestly,
not that an independent F4 verification was performed.

```powershell
.venv/Scripts/python.exe -m pytest -q
```

Unit/integration tests create local Git repositories and SQLite stores. Optional
corpus checks skip when their third-party checkouts are absent; those checkouts
and benchmark artifacts are intentionally not included in the release tree.
Coverage percentages and real-world accuracy/performance guarantees are not claimed.

## Indexing Throughput

`bench_index.py` creates a new retained temporary database for each run and indexes
one committed snapshot, including lineage, bindings, events and publication. It
does not overwrite the repository's index or execute its source code.

```powershell
.venv/Scripts/python.exe verify/bench_index.py C:/path/to/repository --max-seconds 180
.venv/Scripts/python.exe verify/bench_index.py C:/path/to/repository --workers 1 --sql-profile --max-seconds 600
.venv/Scripts/python.exe verify/bench_index.py src/gita/index/evidence.py --repeat 10 --profile
```

Output includes the pinned commit, elapsed time, files, definitions and parse
statuses. A deadline interruption is a failed benchmark, not a successful index.
The timer requests cancellation; an in-flight native call may delay cleanup.
`--profile` adds Python profiler overhead, so use it to locate bottlenecks, not
for latency claims. `--sql-profile` times statement execution but not subsequent
cursor fetching. For a baseline implementation, set `GITA_BENCH_SOURCE` to that
worktree's `src` directory, using the same repository, revision and environment.

No indexing throughput guarantee is made for this release. Measure a cold
snapshot separately from full historical replay, without concurrent workloads.
Incomplete and unsupported files remain visible in the reported coverage rather
than being counted as parsed entities. A run that stops before publication is
not a completed-index latency measurement.

The legacy default harness, `--stage ws1`, `--stage year1` and `--demo` clear fixed
temporary directories named `gita-verify-merges` and/or `gita-verify-refactor`.
Do not keep work there. Prefer the explicitly scoped F1/F2/F3 commands above.