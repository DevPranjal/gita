"""Verify portable numerical evidence and reproduce grading without model calls."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .common import MODEL, digest, read_json
from .evidence import COMMON_TOOLS, GITA_TOOLS
from .export import file_hash
from .judge import blind_input, reference_rows, score, validate_judgment


def verify_bundle(bundle: dict, report: dict, manifest: dict, dataset: Path) -> dict:
    if file_hash(dataset) != manifest["source_sha256"]:
        raise ValueError("Dataset bytes do not match the frozen manifest")
    if bundle["manifest_digest"] != manifest["manifest_digest"]:
        raise ValueError("Bundle manifest mismatch")
    if bundle["report_sha256"] != digest(report):
        raise ValueError("Bundle does not match this report")
    references = reference_rows(dataset)
    expected_tasks = {p["task"] for p in report["pairs"]}
    if len(bundle["cases"]) != len(expected_tasks) or {c["task"] for c in bundle["cases"]} != expected_tasks:
        raise ValueError("Bundle task coverage mismatch")
    totals = {a: {"input_tokens": 0, "output_tokens": 0, "reported_credits": 0.0}
              for a in ("baseline", "gita")}
    calls = 0
    for case in bundle["cases"]:
        if digest(references[case["task"]]) != case["reference_digest"]:
            raise ValueError("Case reference digest mismatch")
        for arm, record in case["arms"].items():
            raw, measured = record["native_usage"], record["measurement"]
            if raw["currentModel"] != MODEL or set(raw["modelMetrics"]) != {MODEL}:
                raise ValueError("Unexpected model in portable usage")
            usage = raw["modelMetrics"][MODEL]["usage"]
            details = raw["tokenDetails"]
            if usage["inputTokens"] != sum(details[k]["tokenCount"]
                                           for k in ("input", "cache_read", "cache_write")):
                raise ValueError("Input/cache decomposition mismatch")
            if (usage["cacheReadTokens"] != details["cache_read"]["tokenCount"]
                    or usage["cacheWriteTokens"] != details["cache_write"]["tokenCount"]
                    or usage["outputTokens"] != details["output"]["tokenCount"]):
                raise ValueError("Native token components disagree")
            for key, value in (("input_tokens", usage["inputTokens"]),
                               ("output_tokens", usage["outputTokens"]),
                               ("reported_credits", raw["totalNanoAiu"] / 1e9)):
                if not math.isclose(measured[key], value, rel_tol=1e-12, abs_tol=1e-9):
                    raise ValueError(f"Measurement/native usage mismatch: {key}")
                totals[arm][key] += value
            allowed = set(COMMON_TOOLS + (GITA_TOOLS if arm == "gita" else ()))
            if len(record["calls"]) != measured["tool_calls"]:
                raise ValueError("Portable tool-call count mismatch")
            if any(call["tool"] not in allowed for call in record["calls"]):
                raise ValueError("Forbidden tool in portable call trace")
            calls += len(record["calls"])
        answers = {a: record["answer"] for a, record in case["arms"].items()}
        payload, mapping = blind_input(case["task"], references[case["task"]], answers)
        judgment = case["judgment"]
        if digest(payload) != judgment["input_digest"] or mapping != judgment["mapping"]:
            raise ValueError("Portable grading input/mapping mismatch")
        decisions = validate_judgment({"comparisons": judgment["comparisons"]}, payload)
        if digest(score(payload, mapping, decisions, list(answers))) != digest(judgment["scores"]):
            raise ValueError("Portable scores do not reproduce")
    summaries = [s for s in report["by_split"].values() if s["pairs"]]
    for arm in totals:
        for metric, value in totals[arm].items():
            expected = sum(s["arms"][arm]["totals"][metric] for s in summaries)
            if not math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-8):
                raise ValueError(f"Aggregate mismatch: {arm} {metric}")
    return {"cases_verified": len(bundle["cases"]), "arms_verified": len(bundle["cases"]) * 2,
            "tool_calls_verified": calls, "grades_reproduced": len(bundle["cases"]),
            "new_model_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_bundle(read_json(args.bundle), read_json(args.report),
                                   read_json(args.manifest), args.dataset)), flush=True)


if __name__ == "__main__":
    main()
