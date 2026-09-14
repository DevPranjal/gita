# Verifying gita yourself

The harness reports measured checkpoint results separately from unimplemented
work. The current contract is [FOUNDATION.md](../specs/entity-index/FOUNDATION.md);
the older WS-2 through WS-5 plans are superseded. A passing checkpoint does not
mean the entity index is complete or that identity decisions are correct.

## F1 Evidence Checkpoint

Run from the repository root with the project dependencies installed:

```powershell
.venv\Scripts\python.exe verify\check.py --stage f1-evidence
```

This scoped command runs five local checks and retains source fixtures and an
SQLite database in a fresh `gita-verify-f1-*` temporary directory. It prints that
path for inspection. It does not index or modify your repository and does not
use the legacy harness's fixed scratch directories. No model or network is used;
the parser grammar must already be installed.

| Check | Evidence |
| --- | --- |
| Original-byte spans and digests | Python's standard-library AST provides independent function bounds; original byte slices and BLAKE2b-256 digests are compared with the adapter. |
| Parse cache round trip | Four parse bundles and their versions are written, closed and reopened; records and integrity checks must agree. |
| Location and literal evidence | A prefix insertion changes definition location but preserves the leaf version; changed spaces inside a string change the normalized body. |
| Syntax errors remain unknown | Independently invalid Python stays incomplete after reopen, with diagnostics and no authoritative definitions. |
| Rollback and read-only guards | An aborted transaction leaves no parse or version rows; a reopened reader refuses a writer transaction. |

All five passed at the 2026-09-14 checkpoint. These are small Python fixtures.
The AST/span checks are independent of tree-sitter; serialization round trips
share production code and are not an independent proof of the whole database.
No lineage, generation publication, indexed CLI, performance or accuracy gate is
established here. The adapter is synchronous; the separate worker checkpoint below
adds deadline isolation. Byte-safe Git enumeration is still pending. SQL is explicitly unsupported by this new
adapter, without changing the existing SQL differ.

## F1 Worker Checkpoint

```powershell
.venv\Scripts\python.exe verify\check.py --stage f1-worker
```

This command retains fixtures in a unique `gita-verify-worker-*` directory without
modifying the repository. All four checks passed on 2026-09-14:

| Check | Evidence |
| --- | --- |
| Worker evidence matches direct parse | Two full bundles and source digests agree while reusing one isolated child. This shares the parser, not an independent AST oracle. |
| Deadline refusal is terminal | An immediate deadline raises `resource_limit` without returning partial evidence; the worker then refuses another parse. |
| Cache transaction rollback | Prior committed evidence survives; staged and timed-out evidence remain absent after reopen. |
| Fresh worker retry and explicit persist | A new worker returns identical IDs; an explicit writer persists the result and read-only reopening verifies it. |

The immediate deadline includes child startup and is not a parse-speed benchmark.
The 42 worker tests separately exercise real blocked send/receive, process crash,
cancellation, malformed replies, failed termination, late completion and retry
identity. Five new harness cases cover real execution, unique retained fixtures,
CLI registration and injected parse failures. No sleeps or polling are needed to
coordinate the fault-injection workers.

The worker defaults to five seconds per request, uses one persistent spawn child,
and does not change deterministic evidence fingerprints. Deadline, transport and
cancellation failures close the worker; a fresh instance is needed to retry.
OS startup and reaping are not hard real-time guarantees. If cleanup itself fails,
unfinished resources remain owned for another `close()` attempt. Native RSS limits,
generation publication and the full indexed workflow remain unverified. Library
callers on Windows need the usual `if __name__ == "__main__":` guard for spawning.

## Reading Results

```powershell
.venv\Scripts\python.exe verify\check.py --stage ws2
```

This currently reports `NOT VERIFIED` and returns `2`, even though the partial
store module exists. A checkpoint requires a registered runner and its required
module. File presence alone is not a passing result.

- `0`: selected registered checks completed without `FAIL`. Inspect any `SKIP`,
  `EXPECTED FAIL` and unverified-stage lines; this is not a release verdict.
- `1`: a check failed, a registered runner raised, or it returned no checks.
- `2`: an unknown stage argument or an explicitly selected unverified stage.

The default command runs registered checkpoints and the legacy Year 1 target,
listing other stages as unverified. Eight harness regression tests exercise
registration, missing modules, unique scratch paths, failed/raising/empty
runners, unknown stage selection and a missing stored parse. They replace the
previous instructions that edited and reverted tracked production source.

## Existing Enumeration And History Checks

```powershell
.venv\Scripts\python.exe verify\check.py --stage ws1
.venv\Scripts\python.exe verify\check.py --demo
```

**Scratch-directory warning:** these legacy paths and the default command reuse
and clear fixed directories under the system temp folder:
`gita-verify-merges` and/or `gita-verify-refactor`. Do not keep work there. The new
`f1-evidence` and `f1-worker` commands do not clear either directory.

WS-1 compares changed-file lists with Git and forces the old enumeration path as
a negative control. Its process count concerns the measured Git wrapper calls,
not necessarily every process in a complete product workflow. Optional corpus
checks may report `SKIP` when the local corpus is absent.

The demo builds five commits: function birth, body edit, rename, move and body
edit. It prints the repository path and commands to inspect it. Legacy gita
history currently returns two of these five events while `git log --follow`
finds five in this fixture. Detecting individual rename/move diffs does not yet
persist their relationship. Future indexed history is opt-in; this checkpoint
does not change legacy history's behavior or promise transparent fallback.

## Regression And Remaining Gates

Latest full Unit Test MCP run: **968 passed, 1 failed**, no skips, in 316.6s.
The failure was separately confirmed as
`test_foundation_tracks_five_events_after_restart`: the proposed `gita index`
command is not implemented. The full suite is deliberately not green; no test
is skipped or marked xfail to conceal that acceptance gap. Coverage percentages
were not measured.

For a manual rerun:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest tests/test_index_store.py -q
.venv\Scripts\python.exe -m pytest tests/test_index_builder.py::test_foundation_tracks_five_events_after_restart -q
```

The store file currently has 279 passing tests. The exact acceptance command
above must still fail. [Foundation section 14](../specs/entity-index/FOUNDATION.md#14-implementation-plan)
records implemented subsets and pending work. Full relational constraints,
generation publication, offline Git access, lineage, indexed queries and the
independent release gates remain unverified or unimplemented. Corrupt databases
are rejected and preserved, never automatically deleted as recovery.
