"""Portable evidence without raw CLI credentials, API IDs, or repository source."""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from .common import digest, read_events, read_json, write_json

_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]{30,}", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
)


def file_hash(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def check_export_text(text: str) -> None:
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        raise ValueError("Possible credential pattern in export; inspect locally before publishing")


def usage_projection(raw: dict) -> dict:
    fields = ("totalPremiumRequestCost", "totalUserRequests", "totalNanoAiu",
              "tokenDetails", "totalApiDurationMs", "sessionStartTime", "currentModel",
              "lastCallInputTokens", "lastCallOutputTokens")
    projected = {k: raw[k] for k in fields}
    projected["modelMetrics"] = {
        model: {k: record[k] for k in ("requests", "usage", "totalNanoAiu", "tokenDetails")}
        for model, record in raw["modelMetrics"].items()}
    return projected


def export_evidence(manifest: dict, report: dict, runs: Path, grades: Path) -> dict:
    tasks = {t["id"]: t for t in manifest["tasks"]}
    experiment = read_json(runs / "experiment.json")
    if experiment["manifest_digest"] != manifest["manifest_digest"]:
        raise ValueError("Export manifest mismatch")
    cases = []
    for pair in report["pairs"]:
        task = tasks[pair["task"]]
        case = {"task": task["id"], "repo": task["repo"], "pr_url": task["pr_url"],
                "language": task["language"], "reference_digest": task["reference_digest"],
                "scope": read_json(runs / task["id"] / "scope.json"), "arms": {}}
        for arm in ("baseline", "gita"):
            directory = runs / task["id"] / arm
            events = read_events(directory / "tools.jsonl")
            usage = read_json(directory / "usage.json")
            case["arms"][arm] = {
                "measurement": pair[arm],
                "answer": read_json(directory / "content.json"),
                "native_usage": usage_projection(usage),
                "artifact_sha256": {name: file_hash(directory / name) for name in
                                    ("events.jsonl", "tools.jsonl", "usage.json", "result.json",
                                     "invocation.json", "analysis.json", "content.json")},
                "calls": [{"tool": e["tool"], "arguments": e["arguments"], "ok": e["ok"],
                           "seconds": e["seconds"], "output_tokens_cl100k": e["output_tokens_cl100k"],
                           "result_sha256": digest(e["result"])} for e in events],
            }
            event_source = pair[arm].get("event_source", "events.jsonl")
            if event_source != "events.jsonl":
                case["arms"][arm]["artifact_sha256"]["native_session_events"] = file_hash(
                    directory / event_source)
        directory = grades / task["id"]
        if (directory / "grade.json").exists():
            grade = read_json(directory / "grade.json")
            if grade["status"] == "ok":
                mapping = read_json(directory / "mapping.json")
                case["judgment"] = {
                    "reference_digest": grade["reference_digest"],
                    "input_digest": grade["input_digest"], "scores": grade["scores"],
                    "mapping": mapping,
                    "comparisons": read_json(directory / "comparisons.json"),
                    "artifact_sha256": {name: file_hash(directory / name) for name in
                                        ("grade.json", "input.json", "mapping.json", "comparisons.json")},
                }
                if "recovery" in grade:
                    case["judgment"]["recovery"] = {
                        "source_grade_sha256": grade["source_grade_sha256"],
                        "native_judge_events_sha256": grade["native_judge_events_sha256"],
                        "new_model_calls": 0,
                    }
        cases.append(case)
    result = {
        "format_version": 1, "dataset_commit": manifest["dataset_commit"],
        "dataset_sha256": manifest["source_sha256"],
        "manifest_digest": manifest["manifest_digest"],
        "experiment": experiment,
        "report_sha256": digest(report),
        "privacy": "Allowlisted usage, review findings, tool arguments and hashes only. "
                   "No raw CLI debug logs, API call identifiers, credentials or source-code tool bodies.",
        "reference_source": "Download the pinned AACR positive_samples.json; references are not duplicated here.",
        "cases": cases,
    }
    # Fail closed rather than silently redact evidence and pretend it is original.
    import json
    check_export_text(json.dumps(result, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--grades", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = export_evidence(read_json(args.manifest), read_json(args.report),
                               args.runs, args.grades)
    write_json(args.output, evidence)
    print(f"Exported {len(evidence['cases'])} paired cases to {args.output}", flush=True)


if __name__ == "__main__":
    main()
