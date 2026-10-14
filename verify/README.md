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

The legacy positional mode of `bench_index.py` creates a new retained database
in a unique sibling output directory (or explicit `--output`) and indexes
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

## Offline S2 qualification

Install the optional benchmark-only dependency with `pip install -e ".[dev,bench]"`.
It supplies `psutil` for owned process-tree supervision; production imports do not
depend on it. Provision the Python grammar **before** running. The new manifest
mode never downloads corpus/dependencies, imports/executes corpus source, or
invokes a model. Legacy positional parser/snapshot flags remain supported, but
cannot be combined with manifest mode.

Copy [index-smoke.json](index-smoke.json), replace its source revision with the
exact checkout SHA and describe hardware, power mode and contention. Manifests
are validated against the contract in
[index-manifest.schema.json](index-manifest.schema.json), including unknown/
duplicate fields, booleans masquerading as integers, finite deadlines, limits and
scenario names. Example (PowerShell, run from this checkout):

```powershell
Copy-Item verify\index-smoke.json ..\index-manifest.json
$manifest = Get-Content ..\index-manifest.json -Raw | ConvertFrom-Json
$manifest.source_revision = (git rev-parse HEAD)
$manifest | ConvertTo-Json -Depth 8 | Set-Content ..\index-manifest.json
$env:GITA_ENABLE_LEGACY = '0'
.\.venv\Scripts\python.exe verify\bench_index.py --manifest ..\index-manifest.json --output ..\index-run-001
```

The requested directory must be new and outside both the runtime and tooling
checkouts. No directories are cleared, no global caches flushed, and no processes
are selected by name. A per-build 256 MiB storage limit defaults in this tooling
(the production default is unchanged); at least that limit plus 1 GiB of free
headroom is required before provisioning and each clone. Refusals retain prior
attempts and a `campaign-refused.json` receipt. This is a safety reserve, not an
estimate of corpus/index cost.

| Artifact | Actual contract |
| --- | --- |
| `manifest.json`, `environment.json` | Exact commit, actual runtime source hash, parser/matcher fingerprints, package/Git/SQLite/Python/OS versions, CPU/RAM, limits and operator notes. |
| `runtime-source.zip` | Runtime Python source only, including dirty candidates; its SHA-256 is recorded for reproduction. No configuration/cache/credentials are archived. |
| `corpus-ledger.json` | Fixed timestamps/configuration, pinned first-parent commits, authored changed bytes and logical identities, expected intermediate transition flags. |
| `samples.jsonl` | Fsynced observations, including timeouts/crashes/refusals; only published and correctness-passed attempts get completed latency. |
| Per-attempt directories | Isolated local clone/store, setup/run requests, child stderr, publication receipt, result and supervisor sample. |
| `summary.json` | Attempt/pass/failure counts, successful medians and observed ranges; no confidence interval or universal qualification claim. |

[`index_corpus.generate`](index_corpus.py) cycles body/signature edits, rename,
move, retained copy, deletion/reintroduction and cosmetic revert. Reintroduced
bodies are distinctive; generic duplicate/stub/parse-gap/merge cases remain
separate future corpus work. Uniform and hotspot profiles select different edit
targets once multiple cycles are requested. A module root is additional to each
file's requested function count. Large bounds in the manifest schema are
**input limits**, not evidence those sizes have been qualified.

[`index_oracle.verify`](index_oracle.py) verifies every changed original Git blob,
Git object digests, all represented Python function byte/line spans with Python
AST, all active files/definitions, authored identity continuation/new-copy/new-
reintroduction constraints and exact intermediate transition flags. Stored versions/
parses and their canonical IDs, JSON, payloads and relational definitions are
validated before comparison, along with stored identity/graph relationships.
BLAKE2b-256 of every independently checked original byte span (including module
roots) must equal the stored version's source digest, even if corrupted digests
have self-consistent regenerated IDs and payloads. It hashes
immutable parse/version/definition/occurrence records, normalized lineage seeds/
binding intervals, full decision evidence, events and coverage. These checks
are benchmark gates; they do **not** implement product `index --verify`/F4.
Deliberate fixture corruption tests prove the gates reject bad spans, false
identity links, lost event flags, malformed version JSON and self-consistent
wrong source digests.

Snapshot and full history have fresh stores. Incremental setup builds all but
the final commit in a separate closed process; only the last edge is measured.
No-op/query setup builds the tip then closes. No-op requires the same generation
and zero parses/cache reads. Query records open/policy cost separately from ten
warm calls each to entities/state/history; it does not measure CLI rendering.
Every timed response must match a bounded complete expected response assembled
from ledger-verified canonical records without calling production query methods.
This checks occurrences, identities, history decisions/flags and page/coverage
metadata, not just repeated-answer stability. `query_response_checks` records
the 30 successful comparisons; null, consistently empty and plausible wrong
responses fail the sample. Expectation construction is outside query timing.
Setup, oracle verification and profiling are outside completed operation latency.
Lightweight nested phase timers and counters are recorded; overlapping phase
times must not be summed as elapsed time.

RSS is sampled at 50 ms as a simultaneous sum of owned PID+creation-time
identities, including workers/Git, through the child lifetime including gates.
Short-lived processes may be missed; observed tree CPU is a lower bound.
DB/WAL sampled peaks and closed-file sizes are measured. SQLite anonymous TEMP
storage is `null` with an unavailable reason: **memory/storage qualification is
not complete**. Coverage/build status is retained, including `partial` even when
all extraction succeeded (for example cached source availability remains
unchecked in the product).

For comparison, set `GITA_BENCH_SOURCE` to an isolated baseline checkout's `src`,
then run identical manifests without profiling/concurrent owned workloads:

```powershell
.\.venv\Scripts\python.exe verify\compare_index_runs.py ..\baseline-run ..\candidate-run
```

[`compare_index_runs.compare`](compare_index_runs.py) refuses missing/failed
attempts, different workload/environment/parse policy, counter changes and
different ledger/graph/coverage semantics. Same-policy equivalence separately
compares exact canonical IDs/payloads, excluding only generation attachment/
allocation IDs and operational generation history. Across builder fingerprints,
it compares independently verified ledger mappings, never raw EID equality.

The first optimization caches only validated immutable own-token tuples inside
one build transaction (`builder._CandidateTokens`): at most 1,024 entries,
65,536 tokens and 4 MiB serialized payload, plus bounded Python object overhead.
Oversize items are validated normally without caching. Full populations, copy
guards, pair/candidate bounds, durability and parse coverage are unchanged.
Because builder source is fingerprinted, old indexes must explicitly rebuild.

Final Windows/Python 3.11.9 smoke measurements, rerun after strengthening the
source-digest/canonical-record and measured-query gates:

| History workload | Samples per arm | Baseline | Candidate |
| --- | ---: | ---: | ---: |
| 80 anchor files, 10 functions/file, 10 commits, workers=1 | 3 | Median 11.508 s (11.341–11.813) | Median 6.430 s (6.354–7.779) |
| 1,000 anchor files, 10 functions/file, 10 commits, workers=4 | 1 | 71.301 s | 67.838 s |

No attempts failed; the comparisons passed identical counters and independently
verified ledger/evidence semantics. The small workload had 87 parses/one cache
read, 902 mapped logical entities, 8,040 AST checks and 88 Git byte checks. Its
`partial` status retained one unchecked cached-source record.

The larger workload had 1,007 parses/one cache read, 11,022 mapped logical
entities, 100,040 AST checks, 1,008 Git byte checks and 110,044 source-digest checks.
Its `partial` status includes **two matching candidate-limit records** as well
as one unchecked cached-source record: this is not complete lineage coverage.
Candidate closed database size was 121,479,168 bytes. Sampled simultaneous
process-tree RSS peaked at 400,302,080 bytes across the operation and verification;
anonymous SQLite TEMP storage remains unmeasured. Index operation time excludes
the 99.595 s oracle: total supervised candidate time was 168.348 s.

Power and shared-machine contention were uncontrolled; no intentionally
concurrent benchmark campaign was run. These observations do not establish a
statistical speedup, full T2/T3 qualification, universal memory bound, or
arbitrary-repository SLA. The smaller improvement on the larger workload is
reported rather than extrapolating the small-workload ratio.

CI's dedicated `index-qualification` job is configured to run these contracts and owned
publication recovery/equivalence tests on Windows and Linux with legacy disabled,
and retains artifacts. No T2/T3/real-repository, product F4, full fault campaign,
query rendering, source/patch, suffix, rebuild or GC performance claim is made.