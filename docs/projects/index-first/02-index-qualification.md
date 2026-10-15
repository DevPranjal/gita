# S2: Index correctness, reliability, benchmarking, and optimization

**Status:** Draft specification. **Priority:** Core release blocker.
**Implementation update:** A first S2 engineering increment is implemented, not
full qualification. [`verify/index_qualification.py`](../../../verify/index_qualification.py)
validates versioned manifests, supervises separate snapshot/history/single-update/
no-op/query scenarios, retains failed attempts and samples owned process trees.
[`index_corpus.py`](../../../verify/index_corpus.py) authors deterministic Python
operation ledgers; [`index_oracle.py`](../../../verify/index_oracle.py) checks exact
Git bytes, independent AST spans, lineage continuity/refusals, intermediate events
and canonical evidence. Recovery/equivalence tests live in
[`test_index_reliability.py`](../../../tests/test_index_reliability.py).
The bounded `_CandidateTokens` cache in `builder.py` avoids repeated validated
version decoding without skipping candidate/population/copy checks. Schema and
matching rules are unchanged, but the artifact fingerprint changes: old indexes
continue to refuse use/update until explicitly rebuilt.

The local 80-file/800-function/10-commit experiment has three unprofiled
samples per interpretation, equal independently mapped evidence and no failed
attempts. These are smoke observations, **not** a T1/T2/T3 or statistical release
gate. Both interpretations retain `partial` coverage for a cached-source
availability record; all extraction results are complete and that partial
outcome is retained in samples. A further 1,000-file/10,000-function/10-commit
history observation has one sample per interpretation; its partial coverage also
includes two matching candidate-limit records. Final runs use strengthened
stored-record/source-digest and measured-query gates. See the
[measurement table and limitations](../../../verify/README.md#offline-s2-qualification).
Anonymous SQLite TEMP storage remains explicitly
unavailable, not zero. Missing work includes the full multi-language/boundary/
merge/parse-gap ledger corpus, approved real repositories, suffix/source/rebuild/
GC scenarios, comprehensive fault boundaries, detailed SQL consumption and
storage instrumentation, paired uncertainty and reference-machine qualification.
The requirements below remain the target; no claim is made that all 13 are met.

**Audience:** Index maintainers, test engineers, performance reviewers.

**Companions:** [S1: Retirement](01-safe-retirement.md),
[S3: Product contracts](03-indexed-product.md), [delivery sequence](README.md).

## 1. Product objective

Make the index trustworthy enough to be the only source of entity identity, and
measure its costs well enough to improve it responsibly.

The product must answer three separate questions:

1. **Is the recorded evidence correct under the declared policy and coverage?**
2. **Can builds/updates fail safely and recover predictably?**
3. **What do cold builds, updates, no-ops, and queries cost on named hardware?**

A faster incomplete build is not an improvement. A repeatable wrong identity is
not correctness. A tiny snapshot benchmark is not full-history qualification.

## 2. Existing baseline and gaps

Current mechanisms to retain:

- Transactional first-parent build, suffix continuation, no-op, persisted frontier,
  policy refusal, and explicit rebuild:
  [`builder.build`](../../../src/gita/index/builder.py#L302).
- Original-byte evidence, parser fingerprints, explicit unsupported/parse-error/
  resource outcomes: [`evidence`](../../../src/gita/index/evidence.py#L413).
- Spawned reusable workers and per-file transport/startup-inclusive deadline:
  [`EvidenceWorker.parse`](../../../src/gita/index/worker.py#L98).
- Publication validation and physical storage reservation:
  [`store`](../../../src/gita/index/store.py#L1343).
- Read-only pinned queries and exact-source digest checks:
  [`query`](../../../src/gita/index/query.py).

The current [benchmark script](../../../verify/bench_index.py) measures repeated
single-file parsing or one cold **snapshot** with `since=ref`. It provides useful
profiles, but not historical replay/update/no-op/query qualification. Its SQL
wrapper times `execute`, not cursor consumption; its wall timer requests
cancellation, not a guaranteed process-tree stop. Do not present it as the new
complete benchmark suite.

Existing F1/F2/F3 tests and
[verification stages](../../../verify/README.md) are valuable, but independent F4
verification and process-tree/large-repository performance are not qualified.

## 3. Requirements

| ID | Requirement | Acceptance |
| --- | --- | --- |
| S2-R01 | Every benchmark MUST pin source, corpus, parser/matcher policy, runtime, limits and hardware. | Complete manifest and actual resolved run metadata; no floating `HEAD` in published results. |
| S2-R02 | Snapshot/history/incremental/no-op/query runs MUST be separate scenarios. | Each scenario has isolated setup and explicit cache state. |
| S2-R03 | Completed-build timing MUST require successful publication and correctness checks. | Timeout/crash/partial extraction distinguished; failures remain in denominator. |
| S2-R04 | Evidence correctness MUST have independent oracles. | Fixture ledger and exact Git byte checks do not derive expected values from the production matcher. |
| S2-R05 | Serial/parallel and cold/incremental builds MUST agree logically under the same interpretation. | Canonical evidence comparison excludes only operational identifiers/fields by an explicit allowlist. |
| S2-R06 | Failure campaigns MUST test atomicity and recovery, not just exception messages. | Previous publication unchanged, no partial new publication, restart and retry behavior verified. |
| S2-R07 | Process-tree memory and DB/WAL/temp storage MUST be measured. | Parent plus workers plus owned Git processes tracked; measurement limitations reported. |
| S2-R08 | Performance changes MUST preserve coverage, policy limits and correctness. | Candidate results pass all semantic gates before speed ratios count. |
| S2-R09 | Timed runs MUST be separated from profiling and data provisioning. | No corpus download, dependency setup, model call, or profiler overhead inside headline timing. |
| S2-R10 | Benchmark execution MUST be safe in a shared environment. | Unique owned directories/PIDs, no global cache flush or service/process-name termination. |
| S2-R11 | Read benchmarks MUST distinguish session open from warm query cost. | Both timings plus policy validation cost; no claim that warm API latency equals CLI latency. |
| S2-R12 | Optimization claims MUST include repeat counts, failures and uncertainty. | Per-run observations retained; paired baseline/candidate summaries reproducible. |
| S2-R13 | Operational settings MUST not silently change deterministic semantics. | Worker/deadline/page settings have invariant tests; resource-policy changes are identified as different workloads. |

## 4. Qualification layers

### 4.1 Pure record and parser correctness

Retain domain-separated IDs, canonical encoding, raw Git paths, exact spans,
representation limits, hash/full-payload consistency, and immutable records.

Use:

- Fixed byte fixtures with manually specified spans.
- Python AST spans as an additional independent Python oracle; normalize
  coordinate conventions explicitly and verify multibyte/CRLF behavior.
- For other languages, reviewed golden source slices and names/containment.
  Reusing Tree-sitter for all expected values is not an independent oracle.
- Boundary cases: empty/comment-only files, syntax errors, duplicate names,
  decorators/wrappers, nested entities, C# file-scoped namespaces, C++ macros,
  non-UTF-8 paths versus non-UTF-8 source, NUL, unsupported extensions, limits.

Extracted structural equality does not establish semantic equivalence. Tests
MUST assert what was represented and what was unknown.

### 4.2 Lineage correctness

Generate repository histories from an authored operation ledger. The ledger
identifies logical entities and expected accepted/refused transitions, never
production-generated EIDs.

Required scenarios:

| Fixture | Required invariant |
| --- | --- |
| Same-locator body edit | Continuation under the accepted enclosing identity; event reflects body/source change. |
| Distinctive rename and move | One supported EID across edges; names/paths change in occurrences. |
| Copy with retained source | New copy is not fused with source identity. |
| Duplicate/stub/overload competition | Conservative ambiguous/refused transitions; no ordinal pairing. |
| Nested/container edit | Parent and descendant classifications remain distinct. |
| Delete then reintroduce | New identity, not resurrection by name. |
| Parse gap then recovery | Unknown continuity preserved; no invented birth or cross-gap uniqueness. |
| Pure edit/revert | Two events, equal endpoint bytes, no dropped intermediate transition. |
| Bounded/shallow anchor | Observed provenance rather than proven birth. |
| Merge and side-parent-only entity | First-parent treatment explicit; no claim to side-parent lineage. |
| Reused historical name | Separate candidates/identities; no name-based merge. |
| Change kind/language | Policy-compatible continuation only; incompatible role stays explicit. |

False accepted links, missing expected accepted links, and unresolved candidates
MUST be reported separately. Synthetic fixtures define exact policy outcomes.
Real histories need reviewed annotations and cannot be reduced to "Git
`--follow` agrees," because file history is not an entity-identity oracle.

### 4.3 Build/rebuild/update equivalence

For each deterministic history:

1. Build from root to tip in one invocation.
2. Build a prefix; close all processes; reopen and append the remaining suffix.
3. Repeat with workers 1 and 4; add workers 2/8 in dedicated qualification.
4. Compare membership, versions, definitions, bindings, lineage, source spans,
   decisions, events, state queries, coverage and final query results.
5. Repeat from a new process and with an unchanged-tip request.

Generation UUIDs, timing, and operational counters are not semantic equality
targets. Use an explicit canonical export, not equality of SQLite file bytes.
EIDs/context IDs MUST match for identical interpretation/history. When code
changes alter fingerprints, compare a reviewed mapping of fixture ledger
identities and graph/query semantics; do not ignore IDs without checking their
relationships, and do not claim cross-policy ID stability.

## 5. Benchmark corpus

### 5.1 Deterministic generated tiers

Proposed initial tiers; fixed seeds, commit timestamps, contents and Git config
MUST be specified by the generator manifest. No external downloads required.

| Tier | Anchor contents | History | Purpose |
| --- | --- | --- | --- |
| T0 contract | Small hand-authored multi-file fixtures | 5-30 commits per fixture | Exact oracles and failure injection. |
| T1 small | 100 Python files, 10 uniquely named functions each | 100 commits | PR CI snapshot, resume/no-op, query and basic history cost. |
| T2 medium | 1,000 files, 10 functions each | 500 commits | Dedicated qualification, unchanged-population cost and bounded memory. |
| T3 scale | 10,000 files, 10 functions each | 1,000 commits | Manual/dedicated runs; no implied consumer-hardware SLA. |

Each function has at least 20 distinct meaningful body tokens with fixture-
specific constants to avoid accidental duplicate/stub ambiguity. Synthetic module
roots count separately. Every fifth file additionally gets nested/container
variants in a **separate profile**, not an undocumented change to tier counts.

Historical operation cycle, repeated with deterministic entity selection:
body edit, signature edit, rename, move, copy, delete/reintroduce, nested edit,
cosmetic edit, parse-gap/recovery, and full revert. The generator records every
operation and expected lineage outcome. Keep two profiles: uniformly distributed
edits and a hotspot profile; publish both instead of hiding worst cases.

### 5.2 Real-repository corpus

Before external claims, freeze a manifest of at least six approved, licensed
repositories spanning:

- Python; TypeScript/JavaScript; Go/Rust; C#; C++.
- Small and larger trees, nested/generated code, duplicate definitions.
- Short and longer first-parent histories.
- Supported/unsupported mixed files.

Repository selection is an implementation task, not completed by this spec.
No performance release gate may use an unpinned placeholder repository. Record
original URL, license, mirror/source availability, object format, anchor/tip
SHAs, shallow status, byte/file counts and manifest digest. Fetching requires
explicit operator action outside timed/offline runs; never execute corpus code.

Keep existing AACR/ReviewBench review datasets separate from an indexing corpus.
Mirrored/minimized review checkouts do not automatically supply full histories.

## 6. Required benchmark scenarios

| ID | Setup and measured operation | Correctness/interpretation |
| --- | --- | --- |
| B01 parser | Fixed original source bytes; measure startup/fingerprint separately from repeated parse. | Definitions/representations match oracle; distinguish worker IPC from in-process parsing. |
| B02 cold snapshot | Empty store, provisioned grammars, anchor=tip. | One committed tree; observed/bounded provenance, not full history. |
| B03 cold history | Empty store, fixed root/bounded anchor to tip. | All declared first-parent commits and ledger events recorded. |
| B04 single-commit update | Existing prefix in closed store; append one fixture commit. | Prefix evidence reused; expected changed path/blob parses and events only. |
| B05 suffix update | Prefix plus 10/100-commit suffix on dedicated fixtures. | No old history reconstruction; equivalent final logical state. |
| B06 same-tip no-op | Compatible published index, same tip/boundary/policy. | Existing generation; zero file parses/cache-parse reads; no new logical publication. |
| B07 query/open | Fresh process/session then id/state/entities/history/events. | Separate open/policy cost from each operation and rendering. |
| B08 warm query | Reuse pinned session; fixed selectors and page sizes. | No extraction/build/ancestry traversal; stable cursor and results. |
| B09 source/patch | Occurrence source retrieval and event-patch rendering. | Exact bytes, digest, encoding; source unavailable handled explicitly. |
| B10 rebuild | Explicit rebuild after policy/boundary change on isolated store. | New context; old generation retained, no false ID continuity. |
| B11 reader/writer | Held old reader/cursor during new publication. | Old snapshot consistent, writer completes or explicit busy result, no corruption. |
| B12 GC/retention | Explicit eligible generation collection with held reader case. | Published/predecessor references protected; no promised immediate main-file shrink. |
| B13 failure/retry | Injected interruption or resource fault during build. | No partial publication; retry with same policy equals clean result. |

**Cache labels are mandatory:** fresh store, cached evidence in existing store,
new process, warm session, OS cache uncontrolled/warm. "Cold" alone is forbidden.
No global OS cache flush on shared machines. Offline runs provision Git objects,
grammars and dependencies first, then test with network unavailable.

## 7. Instrumentation and output contracts

### 7.1 Timings and counters

Instrument monotonic durations for session/policy initialization, history walk,
Git metadata/blob I/O, inventory, parse queue/execution/IPC, evidence persistence,
candidate construction/matching, interval/event writes, publication validation,
commit, and cleanup. Record workload counters alongside time:

- Commits visited/processed and scope length.
- Tree entries inventoried; unchanged population considered.
- Changed path/blob pairs, unique blobs, source bytes read.
- Parse attempts, completed parses, cache hits and per-status counts.
- Definitions, versions, lineages, occurrences, bindings, links/events.
- Candidate pairs attempted, limit omissions, ambiguity and coverage totals.
- SQL calls plus row stepping/consumption where measurable.

Parallel phase durations overlap. Do not add worker durations to infer elapsed
wall time. Keep operational observations outside canonical identity records.
Do not log source text, private paths, credentials or SQL bound values by default.

### 7.2 Resource measurement

- Sample summed resident memory of the **owned live process tree** at 50 ms
  intervals; report it as a sampled peak with possible missed transients.
- Track process identity using PID plus creation time to avoid PID reuse.
  Include parser workers and Git subprocesses; do not sum independent per-process
  maxima and label the sum a simultaneous peak.
- Record available OS high-water/commit metrics separately; platform measurements
  are not interchangeable. If child measurement fails, mark memory incomplete.
- Measure main DB, WAL and temp/spool bytes through the run and after handles
  close; report logical/live counts separately from physical allocated size.
- Record process-tree CPU time, worker count and any unavailable counters.
- Profilers/SQL tracing run separately and are never headline performance samples.

### 7.3 Proposed artifacts

A checked-in manifest schema and runner live under the verification tooling;
generated artifacts go to an explicit unique output directory outside tracked
source. Example **proposed**, not current CLI:

```powershell
.\.venv\Scripts\python.exe verify\bench_index.py --manifest C:\bench\manifest.json --output C:\bench\runs\run-001
```

Preserve existing script functionality while adding explicit subcommands or a
manifest mode; do not silently reinterpret today's positional target.

Required files per run:

| File | Contents |
| --- | --- |
| `manifest.json` | Requested scenarios, pinned source/corpus, settings, seeds and repetitions. |
| `environment.json` | Actual Python/Git/SQLite/parser versions, artifact fingerprints, OS/CPU/RAM/storage, power mode and contention notes. |
| `samples.jsonl` | One terminal observation per attempt, including failures/timeouts; no omitted unsuccessful samples. |
| `summary.json` | Derived aggregates, uncertainty, counts and gate outcomes with schema version. |
| `correctness.json` | Oracle/comparison results and evidence identifiers, not raw corpus source. |
| Optional profiles | Clearly labeled diagnostic runs, never mixed into timing samples. |

Each sample MUST include scenario, source/corpus IDs, attempt/repetition,
status (`completed`, `failed`, `timed_out`, `cancelled`), elapsed time, publication
status, generation/context/policy, correctness outcome, coverage counts, resource
measurement status, counters and structured error. Store absence as `null` plus a
reason, never zero. Input validation failures do not count as completed benchmarks.

Manifest validation rejects missing pins, invalid limits, duplicate scenario IDs,
unknown fields in versioned strict sections, and unsafe output paths before work.

## 8. Experimental method and performance gates

### 8.1 Baseline method

1. Establish baseline on the frozen current indexed implementation.
2. Run candidate and baseline in separate pinned environments/worktrees against
   the same local corpus; no global environment modifications.
3. Randomize paired AB/BA order with recorded seed. Keep common settings and
   refuse comparisons with different deterministic workload/coverage limits.
4. Cold builds/history: at least 10 independent paired attempts; report median,
   min/max and per-run data. Do not present p95 from ten samples.
5. Warm queries/no-ops: at least 100 observations per condition across at least
   five independent sessions/processes as applicable; show median/p95 and session-
   clustered uncertainty, not 100 independent samples from one warmed cache.
6. Use at least 1,000 paired bootstrap resamples for build median ratios;
   resample session groups for query uncertainty. Publish seed/method.
7. Report failure rate separately; successful-only timing cannot hide failures.
   Re-run environmental anomalies only under recorded predeclared rules.

### 8.2 Proposed engineering gates

These allowances are policy proposals, not current measurements:

| Dimension | Gate |
| --- | --- |
| Correctness | Zero unexpected accepted identity links, source mismatches, lost events or coverage concealments on frozen contract fixtures. |
| Parallel determinism | All selected worker settings produce identical canonical evidence under the same interpretation. |
| No-op | Zero new file parses and zero new logical generation; no replay of existing commit ancestry to derive results. Policy checks/ref resolution still allowed and timed. |
| Incremental | Exact ledger-defined parse work for T0 cases; prefix not reparsed. Scaling costs from population inventory/matching reported rather than falsely claiming changed-files-only runtime. |
| Recovery | Every injected failure point leaves no partial publication; clean retry matches expected logical output. |
| Dedicated performance regression | Block if median elapsed ratio exceeds 1.10 and paired 95% interval lower bound exceeds 1.00; inconclusive >10% goes to repeated measurement/manual gate, not automatic pass. |
| Catastrophic regression | If at least three consecutive comparable attempts exceed 1.25 elapsed ratio, stop optimization rollout and investigate without waiting for significance. |
| Memory/storage | Block unexplained >10% median sampled peak or peak storage increase on comparable workloads. A justified trade-off needs explicit review; missing measurements do not pass. |
| Speedup claim | Publish a percentage only with correctness pass, named workload/hardware, full samples and paired uncertainty; no universal speedup claim. |

Do not promise an absolute seconds/GB SLA until a reference hardware profile and
baseline exist. After S2-E1, lock the actual named reference machine and numeric
budgets in a versioned benchmark manifest; changing a budget requires rationale,
not a quiet threshold increase. T2/T3 deadlines are operator-specified resource
caps, not goals manufactured from one plausible run.

## 9. Reliability and fault campaign

| Fault | Injection boundary | Required outcome |
| --- | --- | --- |
| Native parse stall/deadline | Worker before response and during IPC | Build fails explicitly; worker cleanup observed; no empty-parse success. |
| Worker crash/malformed response | Protocol response | Typed failure; transaction rolled back; retry works. |
| User cancellation/parent termination | Before parse, during writes, before publication/commit | Reopen sees old complete generation or new complete generation if commit finished, never partial. |
| Power-loss proxy | Kill isolated owning process at named write boundaries | SQLite recovery/integrity and semantic publication checks; not proof against every hardware power failure. |
| Disk full/I/O/permissions | DB creation/write/commit and source access | Explicit storage/source error, no publication corruption; previous evidence preserved. |
| Two writers/held reader | Busy transaction / WAL snapshot | Bounded busy result or serialized success; old reader consistent. |
| Git object missing/pruned | Commit/tree/blob access and source request | Explicit boundary/source failure; no lazy fetch or empty result. |
| Ref moves/history rewritten | During build and before update | Pinned work consistent; current ref/freshness reported; divergent update requires rebuild. |
| Grammar missing/policy drift | Open/build | Explicit parser/policy error, no download or fallback. |
| Parse/representation/candidate limits | Exact limit and one-over cases | Explicit unknown/omission where policy allows; no silent absence; timeouts remain build failures. |
| Corrupt schema/records/cursors | Isolated DB copy/query | Reject with typed error, no repair, no guessed data. |
| Retention race | Open reader then collect eligible generation | Existing snapshot behavior tested; new open gets explicit gone if collected. |

Use failpoints/test doubles for deterministic cases, plus subprocess termination
tests for actual persistence boundaries. Physical disk exhaustion must use a
quota-limited disposable volume, never fill a user's drive. Cleanup only resolved
run-owned paths/PIDs; retain failure evidence when requested.

## 10. Optimization sequence

Optimize measured bottlenecks one change at a time:

1. **Observe first:** phase counters, query plans, time in worker startup, parser,
   fingerprint hashing, source I/O, matching, and publication.
2. **Low-risk throughput:** prepared/batched writes, bounded prefetch, reuse of
   workers and metadata, avoid repeated invariant work inside a build/session.
3. **Query planning:** covering indexes and bounded lookup shape, with space/write
   cost measured and optional-index compatibility preserved.
4. **Inventory/matching work:** improve full-population indexing and frontier reuse
   without dropping duplicate/copy guards or unsupported/gap handling.
5. **Policy initialization:** consider safe caching of artifact validation within
   a known environment/session; invalidation and changed-asset tests required.
6. **Larger architectural changes:** delta inventories, chunked publication,
   durable resumability or new parse cache keys require separate design and
   correctness review. Current build invocation is atomic; do not add partial
   publications as a casual speed patch.

Forbidden shortcuts: disabling durability, skipping full-evidence equality,
accepting similarity as identity, suppressing gaps, narrowing scope unannounced,
omitting source checks, loosening resource policy, or ignoring failed attempts.

## 11. CI and implementation epics

| Lane | Frequency | Work |
| --- | --- | --- |
| Contract correctness | Every PR | T0, existing foundation suites, no-parser read spies, package contract tests; no strict wall-time gates on shared CI. |
| Cross-platform | Release/PR matrix | Existing Windows/Linux and Python 3.11-3.13; grammar setup, spawn/locking/path cases. |
| Small benchmark smoke | PR | T1 bounded scenarios, schema/counter correctness and catastrophic timeout guard; noisy elapsed values informational. |
| Dedicated qualification | Candidate/release | T1/T2 paired baselines, fault campaign, memory/storage measurement. |
| Scale exploration | Manual scheduled | T3 and frozen real corpora; no unsupported performance marketing from skipped workloads. |

| Epic | Work items | Dependencies | Exit |
| --- | --- | --- | --- |
| S2-E1: Freeze harness contract | Manifest/result schemas, T0/T1 generator/ledger, baseline environment capture, approved real corpus selection, reference-machine budget record. | None | Baseline artifacts and reproducibility instructions reviewed; no unpinned performance corpus. |
| S2-E2: Correctness oracles | Byte/AST/span oracles, lineage ledger, canonical export/differential tests, no-op/update/query spies. | S2-E1 | All oracle outcomes exact, including ambiguity/gaps; baseline failures exposed, not normalized away. |
| S2-E3: Failure and lifecycle | Failpoints, process kill/reopen, lock/storage/source faults, offline and retention tests. | S2-E1 | Fault matrix passes and retained snapshots/retries verified. |
| S2-E4: Measurement runner | Phase/counter hooks, process-tree sampler, strict results, scenario driver, AB/BA statistics, dedicated CI lane. | S2-E1; S2-E2/E3 for release qualification | Reproduction reruns yield matching correctness and comparable declared measurements; gates applied. |
| S2-E5: Optimize | Profile, prioritize, implement one bounded optimization, compare baseline/candidate, publish trade-offs. | S2-E2/E3/E4; coordinate S1 policy changes | No semantic regression; measured benefit on intended workload without hidden resource trade-off. |

### Definition of done

All requirements have automated evidence or explicit measurement artifacts;
correctness/recovery pass before performance is accepted; baseline/candidate
manifests, all attempts, and uncertainty are retained. New results distinguish
consumer-local indexing from cloud-model review costs. No new speed, reliability,
or independent-verification claim is made merely because this spec exists.
