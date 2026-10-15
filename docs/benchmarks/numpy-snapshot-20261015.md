# NumPy snapshot: measured cost and coverage limits

This is an actual NumPy snapshot observation of index commit
`94aa46e884a58da113827a7f6b4843dd59326cc6`, not an extrapolation from generated
Python files. [Machine-readable evidence](numpy-snapshot-20261015.json) retains
both the failed first attempt and the explicit higher-bound retry.

## Frozen workload

- Repository: [numpy/numpy](https://github.com/numpy/numpy).
- Revision: `741b1a0a89485ac3310ef82f8b4edc08bdfd6a5d`.
- Scope: one shallow pinned snapshot, **not full history**.
- Tree entries: **2,425**; four parser workers; original parser/matcher limits.
- Pinned `LICENSE.txt` SHA-256:
  `1be1df33863f97a7bc1c4d67980bd6c69c9a6fef0a5ee76e6ad6cb91e56e8491`.
- No source checkout, corpus dependency install, import of NumPy, build, or tests.
  Git state remained unchanged.
- Index runtime SHA-256:
  `8be20d1030cd0aa7cedf827ac3b7a36f96095f975e7092a50f7325eafbc46067`.

## Results: two attempts, not a successful-only average

| Observation | First attempt | Fresh-store retry |
| --- | --- | --- |
| Explicit storage bound | 1 GiB | 2 GiB |
| Supervisor deadline | 600 s | 600 s |
| Outcome | Resource refusal, no publication | Published **partial**, stated gates passed |
| Production build time | Not a completed build | **123.433 s** |
| Supervised time, including verification | 122.121 s | **186.642 s** |
| Verification time | No completed verification | 62.167 s |
| Sampled process-tree RSS peak | 594,731,008 bytes | 596,156,416 bytes |
| Closed database size | No completed-index measurement | **495,419,392 bytes** |

The first build refused a conservative **1,433,951,336-byte commit storage
reservation**, rather than bypassing its 1 GiB bound. The retry used a new owned
repository/store and an explicit 2 GiB bound. It did not relax identity, parsing,
durability, or source checks. A reservation is not the final database size.

These are single observations on a shared Windows host, Python 3.11.9,
SQLite 3.45.1, with possible concurrent preparation work and uncontrolled
power/cache state. No baseline runtime was measured for this NumPy pin, so this
is **not a speedup claim**. RSS includes verification and can double-count shared
pages. SQLite anonymous TEMP storage is not measured.

## Coverage: this is not complete NumPy understanding

| Extraction outcome | Entries |
| --- | ---: |
| Complete | 913 |
| Parse error | 221 |
| Unsupported language | 1,284 |
| Non-source object | 7 |

The index records 33,653 definitions and 30,967 versions in this snapshot.
Native `.c`, Cython `.pyx`, and CUDA `.cu` do not have registered byte adapters.
Other supported files with parser errors are also unknown, not empty. Do not
describe all 2,425 entries as successfully parsed or all NumPy code as indexed.

Stored-record contracts, SQLite integrity/foreign keys and exact scope passed.
Independent checks sampled eight source spans against Git bytes and checked
40 top-level Python AST spans; member/path/tree-entry checks also passed.
These do not establish all-entity extraction completeness or historical lineage
truth. See [the public-corpus verifier](../../verify/public_index_worker.py).

## Reproduction

Provision the exact commit into an owned, normal `.git` repository without
checking out its source, and provision parsers before measuring. Use the
benchmark extra and the same runtime policy. The current public worker supports
an explicit request without changing the three-repository campaign allowlist:

```python
import os
from pathlib import Path

checkout = Path.cwd()
os.environ["GITA_BENCH_SOURCE"] = str(checkout / "src")
os.environ["GITA_ENABLE_LEGACY"] = "0"
from verify.bench_public_index import execute, write

tip = "741b1a0a89485ac3310ef82f8b4edc08bdfd6a5d"
output = Path(r"C:\bench\numpy-observation-new")
output.mkdir(exist_ok=False)
write(output / "request.json", {
    "repository": r"C:\bench\numpy", "scenario": "snapshot",
    "anchor": tip, "ref": tip, "commits": [tip],
    "workers": 4, "max_storage_bytes": 2 * 1024 ** 3,
})
result = execute(output / "request.json", output / "result.json",
                 600, checkout / "src")
assert result["status"] == "passed", result
```

The Python path must include this checkout and its `src`, as in the
[verification guide](../../verify/README.md). Record environment, Git-state
fingerprints, license digest, coverage and all failed attempts when reproducing;
never infer unmeasured memory or failure cost as zero.

## Engineering implications

The delta/publication improvements make the snapshot build possible within this
explicit bound, but **two minutes and a roughly 495 MB store are not a claim of
Git-like indexing efficiency**. Storage amplification, parser/language coverage
and real historical replay still need work. This measurement identifies those
limits; it does not qualify PyTorch or full NumPy history.
