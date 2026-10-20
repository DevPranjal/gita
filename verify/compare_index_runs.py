"""Compare completed campaigns only after workload/environment/semantic gates."""

import argparse
import json
from math import isfinite
from pathlib import Path
import statistics
import sys


def compare(baseline, candidate):
    def load(root):
        return (json.loads((root / "manifest.json").read_text()),
                json.loads((root / "environment.json").read_text()),
                [json.loads(line) for line in (root / "samples.jsonl").read_text().splitlines()])

    bm, be, bs = load(baseline)
    cm, ce, cs = load(candidate)
    if bm != cm:
        raise ValueError("comparison requires identical requested workloads/operational settings")
    for field in ("python", "platform", "machine", "processor", "cpu_count", "ram_bytes", "sqlite", "git",
                  "packages", "parse_fingerprint", "workload_limits", "notes", "tooling_digest"):
        if be[field] != ce[field]:
            raise ValueError(f"incompatible environment: {field}")
    result = {"schema_version": 1, "scenarios": {}, "qualification": "smoke; observed range, not statistical qualification"}
    for scenario in bm["scenarios"]:
        left = [sample for sample in bs if sample["scenario"] == scenario]
        right = [sample for sample in cs if sample["scenario"] == scenario]
        attempts = bm["repetitions"]
        if len(left) != attempts or len(right) != attempts:
            raise ValueError("missing attempts; failures must remain in denominator")
        if any({sample["repetition"] for sample in samples} != set(range(attempts))
               for samples in (left, right)):
            raise ValueError("duplicate/missing repetition")
        if any(sample["status"] != "passed" or not sample["correctness"]["passed"] for sample in left + right):
            raise ValueError("failed/correctness-refused attempt: no speed ratio is publishable")
        if len({sample["corpus_digest"] for sample in left + right}) != 1:
            raise ValueError("different authored corpus")
        if len({sample["correctness"]["semantic_digest"] for sample in left + right}) != 1:
            raise ValueError("ledger/evidence/graph/coverage disagree across interpretations")
        if any(sample["counters"] != left[0]["counters"] for sample in left + right):
            raise ValueError("population/counters changed")
        base_times = [sample["completed_seconds"] for sample in left]
        candidate_times = [sample["completed_seconds"] for sample in right]
        if any(type(value) not in (int, float) or not isfinite(value) or value <= 0
               for value in base_times + candidate_times):
            raise ValueError("invalid timing observation")
        result["scenarios"][scenario] = {
            "baseline_seconds": base_times, "candidate_seconds": candidate_times,
            "baseline_attempts": len(left), "candidate_attempts": len(right),
            "failures": 0, "median_ratio": statistics.median(base_times) / statistics.median(candidate_times),
            "build_statuses": sorted({sample.get("build_status", "query") for sample in left + right}),
            "semantic_digest": left[0]["correctness"]["semantic_digest"],
            "baseline_range": [min(base_times), max(base_times)],
            "candidate_range": [min(candidate_times), max(candidate_times)],
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(compare(args.baseline, args.candidate), sort_keys=True, indent=2))
        return 0
    except (ValueError, KeyError, OSError) as error:
        print(f"Comparison refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
