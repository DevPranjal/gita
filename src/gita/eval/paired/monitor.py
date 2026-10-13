"""Continuously grade completed pairs and refresh an auditable live report."""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .common import read_events, read_json, utc_now, write_json
from .judge import grade_task, reference_rows
from .report import collect, render
from .reconcile import reconcile_arm


def terminal(task: dict, runs: Path) -> bool:
    root = runs / task["id"]
    return ((root / "preparation_failure.json").exists()
            or all((root / arm / "result.json").exists() for arm in ("baseline", "gita")))


def live_audit(task: dict, runs: Path, reconciled: bool = False) -> list[dict]:
    findings = []
    for arm in ("baseline", "gita"):
        directory = runs / task["id"] / arm
        result_file = directory / ("analysis.json" if reconciled else "result.json")
        if not result_file.exists():
            continue
        result = read_json(result_file)
        if result["status"] != ("measured" if reconciled else "ok"):
            findings.append({"task": task["id"], "arm": arm, "issue": result.get("error")})
            continue
        if result.get("format_valid") is False:
            findings.append({"task": task["id"], "arm": arm,
                             "issue": f"Response format: {result['format_error']}"})
        events = read_events(directory / "tools.jsonl")
        tools = [e["tool"] for e in events]
        if arm == "baseline" and any(t.startswith("gita_") for t in tools):
            raise ValueError("Baseline contamination")
        for event in events:
            if not event["ok"]:
                findings.append({"task": task["id"], "arm": arm,
                                 "issue": event["result"].get("error"), "tool": event["tool"]})
        if result["gita_calls"] == 0 and arm == "gita":
            findings.append({"task": task["id"], "arm": arm, "issue": "No gita adoption"})
        if len(events) >= 40:
            findings.append({"task": task["id"], "arm": arm, "issue": "Evidence budget reached"})
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--max-minutes", type=int, default=180)
    parser.add_argument("--reconciled", action="store_true")
    args = parser.parse_args()
    manifest = read_json(args.manifest)
    experiment = read_json(args.runs / "experiment.json")
    tasks = [t for t in manifest["tasks"] if t["id"] in experiment["tasks"]]
    references = reference_rows(args.source)
    start = time.monotonic()
    while True:
        if args.reconciled:
            for path in sorted(args.runs.glob("*/*/result.json")):
                if not (path.parent / "analysis.json").exists():
                    reconcile_arm(path.parent)
        result_name = "analysis.json" if args.reconciled else "result.json"
        success = "measured" if args.reconciled else "ok"
        ready = [t for t in tasks if terminal(t, args.runs)
                 and not (args.grades / t["id"] / "grade.json").exists()
                 and all((args.runs / t["id"] / a / result_name).exists()
                         and read_json(args.runs / t["id"] / a / result_name)["status"] == success
                         for a in ("baseline", "gita"))]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(grade_task, t, references[t["id"]], args.runs.resolve(),
                                   args.grades.resolve(), reconciled=args.reconciled) for t in ready]
            for future in as_completed(futures):
                future.result()
        report = collect(manifest, [args.runs], [args.grades], args.reconciled)
        report["live_audit"] = [finding for t in tasks
                                for finding in live_audit(t, args.runs, args.reconciled)]
        write_json(args.report.with_suffix(".json"), report)
        args.report.write_text(render(report), encoding="utf8")
        print(json.dumps({"at": utc_now(), "measured_pairs": report["complete_measured_pairs"],
                          "planned": len(tasks), "pending": sum(not terminal(t, args.runs) for t in tasks),
                          "audit_notices": len(report["live_audit"])}), flush=True)
        if all(terminal(t, args.runs) for t in tasks):
            break
        if time.monotonic() - start > args.max_minutes * 60:
            raise TimeoutError("Monitor deadline reached before all scheduled pairs completed")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
