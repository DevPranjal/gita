"""Audit reference coordinates against pinned source without relabeling them."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .common import git, read_json, write_json
from .judge import normalized_path, reference_rows


def audit_coordinates(manifest: dict, references: dict, repos: Path, task_ids: list[str]) -> dict:
    records, tasks_checked = [], 0
    for task in manifest["tasks"]:
        if task["id"] not in task_ids:
            continue
        repo = repos / task["id"]
        if not (repo / "scope.json").exists():
            continue
        scope = read_json(repo / "scope.json")
        if "changed_files" not in scope:
            continue
        tasks_checked += 1
        files, lengths = {}, {}
        for i, reference in enumerate(references[task["id"]]):
            path, side = normalized_path(reference["path"]), reference["side"].lower()
            start, end = reference["from_line"], reference["to_line"]
            record = {"task": task["id"], "reference": i, "path": path, "side": side,
                      "start_line": start, "end_line": end,
                      "in_changed_file": path in scope["changed_files"]}
            if side not in ("left", "right"):
                record["status"] = "invalid_side"
            elif type(start) is not int or type(end) is not int:
                record["status"] = "invalid_line_type"
            else:
                revision = scope["base"] if side == "left" else scope["head"]
                if side not in files:
                    files[side] = set(git(repo, "ls-tree", "-r", "--name-only", revision).splitlines())
                record["inverted_range"] = start > end
                if path not in files[side]:
                    record["status"] = "missing_file"
                else:
                    key = (side, path)
                    if key not in lengths:
                        lengths[key] = len(git(repo, "show", f"{revision}:{path}").splitlines())
                    record["file_lines"] = lengths[key]
                    record["status"] = ("in_bounds" if 1 <= min(start, end) <= max(start, end)
                                        <= lengths[key] else "out_of_bounds")
            records.append(record)
    return {"tasks_checked": tasks_checked, "references_checked": len(records),
            "status_counts": dict(Counter(r["status"] for r in records)),
            "inverted_ranges": sum(r.get("inverted_range", False) for r in records),
            "not_in_changed_file": sum(not r["in_changed_file"] for r in records),
            "policy": "Diagnostic only; no reference is excluded or changed in scoring.",
            "records": records}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repos", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task_ids = [t for root in args.runs for t in read_json(root / "experiment.json")["tasks"]]
    result = audit_coordinates(read_json(args.manifest), reference_rows(args.source),
                               args.repos, task_ids)
    write_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))


if __name__ == "__main__":
    main()
