"""Explicit environmental sensitivity; the primary cohort is never overwritten."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .common import read_json, write_json
from .report import aggregate


def environment_sensitivity(report: dict, environment: dict) -> dict:
    affected = {entry["task"] for entry in environment["affected_arms"]}
    pairs = report["pairs"]
    wake = datetime.fromisoformat(environment["wake_time_utc"].replace("Z", "+00:00"))
    clean = [p for p in pairs if p["task"] not in affected]
    before = [p for p in clean if all(datetime.fromisoformat(p[a]["finished"]) < wake
                                     for a in ("baseline", "gita"))]
    after = [p for p in clean if all(datetime.fromisoformat(p[a]["started"]) > wake
                                    for a in ("baseline", "gita"))]
    return {
        "policy": "Primary retains all measured pairs. These are sensitivity subsets, not replacements.",
        "excluded_task_ids": sorted(affected),
        "primary_all": aggregate(pairs),
        "without_interrupted_pairs": aggregate(clean),
        "completed_before_resume": aggregate(before),
        "started_after_resume": aggregate(after),
        "other_boundary_pairs": [p["task"] for p in clean if p not in before and p not in after],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = environment_sensitivity(read_json(args.report), read_json(args.environment))
    write_json(args.output, result)
    print(json.dumps({k: {"pairs": v["pairs"], "reductions": v.get("reductions"),
                           "quality_deltas": v.get("quality_deltas")}
                      for k, v in result.items() if isinstance(v, dict)}), flush=True)


if __name__ == "__main__":
    main()
