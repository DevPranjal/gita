"""Descriptive trace diagnostics; never used to select or exclude tasks."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .common import read_events, read_json, write_json
from .reconcile import execution_events


def arm_diagnostics(directory: Path) -> dict:
    events = read_events(directory / "tools.jsonl")
    names = [e["tool"] for e in events]
    gita = [e for e in events if e["tool"] == "gita_diff"
            and not e["arguments"].get("filter") and e["ok"]]
    raw = [e for e in events if e["tool"] == "raw_diff"
           and not e["arguments"].get("path") and e["ok"]]
    cli_events, _ = execution_events(directory)
    checkpoints = [e["data"] for e in cli_events
                   if e.get("type") == "session.usage_checkpoint"]
    schemas = []
    if checkpoints:
        for state in checkpoints[-1].get("promptCacheBreakState", []):
            schemas.extend(m.get("tool_tokens") for m in state.get("models", {}).values())
    return {
        "raw_diff_calls": names.count("raw_diff"),
        "gita_diff_calls": names.count("gita_diff"),
        "gita_show_calls": names.count("gita_show"),
        "gita_expand_calls": names.count("gita_expand"),
        "read_file_calls": names.count("read_file"), "search_calls": names.count("search"),
        "list_files_calls": names.count("list_files"),
        "budget_reached": len(events) >= 40,
        "budget_rejected_calls": sum("call limit" in e["result"].get("error", "") for e in events),
        "first_gita_answer_tokens": gita[0]["result"]["tokens"] if gita else None,
        "first_gita_truncated": gita[0]["result"]["truncated"] if gita else None,
        "full_raw_patch_tokens": raw[0]["result"]["total_tokens"] if raw else None,
        "tool_schema_tokens": schemas[-1] if schemas else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = read_json(args.report)
    rows = []
    for pair in report["pairs"]:
        row = {"task": pair["task"], "repo": pair["repo"],
               "language": pair["language"], "split": pair["split"]}
        for arm in ("baseline", "gita"):
            roots = [r / pair["task"] / arm for r in args.runs
                     if (r / pair["task"] / arm / "result.json").exists()]
            if len(roots) != 1:
                raise ValueError("Expected exactly one arm directory")
            row.update({f"{arm}_{k}": v for k, v in arm_diagnostics(roots[0]).items()})
            row.update({f"{arm}_{k}": pair[arm][k] for k in (
                "input_tokens", "total_tokens", "output_tokens", "reported_credits",
                "model_requests", "tool_calls", "tool_errors", "wall_seconds",
                "tool_output_tokens_cl100k")})
            row[f"{arm}_format_valid"] = pair[arm].get("format_valid")
            row[f"{arm}_content_valid"] = pair[arm].get("content_valid")
            if pair["scores"]:
                row.update({f"{arm}_{k}": pair["scores"][arm][k] for k in (
                    "recall", "semantic_recall", "defect_recall", "generated", "matched")})
        row["input_reduction"] = 1 - pair["gita"]["input_tokens"] / pair["baseline"]["input_tokens"]
        rows.append(row)
    write_json(args.output, {"source_report": args.report.name, "pairs": rows})
    if rows:
        keys = sorted({key for row in rows for key in row})
        with args.output.with_suffix(".csv").open("w", encoding="utf8", newline="") as stream:
            writer = csv.DictWriter(stream, keys)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({"pairs": len(rows), "output": str(args.output)}))


if __name__ == "__main__":
    main()
