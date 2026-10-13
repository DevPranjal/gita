"""Account for every planned task and every charged reviewer attempt."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .common import execution_events, parse_usage, read_json, write_json


def coverage(manifest: dict, roots: list[Path]) -> dict:
    tasks = {t["id"]: t for t in manifest["tasks"]}
    records, attempts, seen = [], [], set()
    for root in roots:
        experiment = read_json(root / "experiment.json")
        for task_id in experiment["tasks"]:
            if task_id in seen:
                raise ValueError("Duplicate task attempt; do not silently choose a preferred run")
            seen.add(task_id)
            task = tasks[task_id]
            directory = root / task_id
            entry = {"task": task_id, "repo": task["repo"], "pr_url": task["pr_url"],
                     "cohort": root.name, "arms": {}}
            preparation = directory / "preparation_failure.json"
            if preparation.exists():
                entry.update(status="preparation_failed",
                             error=read_json(preparation)["error"].replace(str(root), "<run>"))
            else:
                for arm in ("baseline", "gita"):
                    arm_dir = directory / arm
                    if not (arm_dir / "result.json").exists():
                        entry["arms"][arm] = {"status": "not_completed"}
                        continue
                    result = read_json(arm_dir / "analysis.json")
                    record = {"task": task_id, "repo": task["repo"], "cohort": root.name,
                              "arm": arm, "status": result["status"],
                              "content_valid": result.get("content_valid"),
                              "format_valid": result.get("format_valid")}
                    if (arm_dir / "usage.json").exists():
                        record["measured_usage"] = parse_usage(arm_dir / "usage.json")
                    if result["status"] != "measured":
                        record["measurement_error"] = result.get("measurement_error")
                        events, _ = execution_events(arm_dir)
                        server_records = [s for e in events if e.get("type") == "session.mcp_servers_loaded"
                                          for s in e["data"]["servers"] if s["name"] == "review"]
                        record["review_server"] = server_records[-1] if server_records else None
                    entry["arms"][arm] = record
                    attempts.append(record)
                entry["status"] = ("paired_measured" if all(
                    r["status"] == "measured" for r in entry["arms"].values())
                    else "pair_not_eligible")
            records.append(entry)
    charged = [a for a in attempts if a.get("measured_usage")]
    return {
        "manifest_digest": manifest["manifest_digest"],
        "planned_tasks": len(records), "task_status_counts": dict(Counter(r["status"] for r in records)),
        "reviewer_attempts": len(attempts), "attempts_with_native_usage": len(charged),
        "all_attempt_input_tokens": sum(a["measured_usage"]["input_tokens"] for a in charged),
        "all_attempt_reported_credits": sum(a["measured_usage"]["reported_credits"] for a in charged),
        "cost_scope": "All recorded reviewer attempts, including ineligible pairs. "
                      "Excludes research, calibration/aborted pilot versions and judge calls.",
        "ineligible_attempts": [a for a in attempts if a["status"] != "measured"],
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = coverage(read_json(args.manifest), args.runs)
    write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("records", "ineligible_attempts")}))


if __name__ == "__main__":
    main()
