"""Separate measurement validity, response-schema compliance and review content.

Reanalysis never re-runs the reviewer and never overwrites original artifacts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import (
    execution_events, final_json, parse_usage, read_events, read_json, validate_findings, write_json,
)
from .runner import audit_tools


def review_content(answer: dict) -> list[dict]:
    """Only fields actually consumed by issue matching; category is not scored."""
    findings = answer["findings"]
    if not isinstance(findings, list):
        raise ValueError("findings must be an array")
    result = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("finding must be an object")
        for key in ("path", "title", "body"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                raise ValueError(f"Missing content field: {key}")
        if finding.get("side") not in ("left", "right"):
            raise ValueError("Invalid content side")
        start, end = finding.get("start_line"), finding.get("end_line")
        if type(start) is not int or type(end) is not int or not 0 < start <= end:
            raise ValueError("Invalid content line range")
        result.append(dict(finding))
    return result


def reconcile_arm(directory: Path) -> dict:
    original = read_json(directory / "result.json")
    result = {**original, "original_status": original["status"], "analysis_version": 1}
    try:
        events, event_metadata = execution_events(directory)
        result.update(event_metadata)
        result.update(parse_usage(directory / "usage.json"))
        result.update(audit_tools(events, original["arm"], read_events(directory / "tools.jsonl")))
        if "wall_seconds" not in original:
            result.update(wall_seconds=None, wall_time_source="unavailable",
                          timing_note="Native usage and journal are valid; wrapper elapsed time was not saved. "
                          "CLI session duration, if present, has a different boundary and is not substituted.")
        result["status"] = "measured"
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        result.update(status="measurement_invalid", measurement_error=str(error))
        write_json(directory / "analysis.json", result)
        return result
    try:
        answer = final_json(events)
        findings = review_content(answer)
        result.update(content_valid=True, findings=len(findings))
    except (ValueError, KeyError, TypeError) as error:
        findings = []
        result.update(content_valid=False, content_error=str(error), findings=0)
    try:
        validate_findings(final_json(events))
        result["format_valid"] = True
    except (ValueError, KeyError, TypeError) as error:
        result.update(format_valid=False, format_error=str(error))
    write_json(directory / "content.json", {"findings": findings})
    write_json(directory / "analysis.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    args = parser.parse_args()
    results = [reconcile_arm(path.parent) for path in sorted(args.runs.glob("*/*/result.json"))]
    print(json.dumps({
        "arms": len(results), "measured": sum(r["status"] == "measured" for r in results),
        "content_failures": sum(r.get("content_valid") is False for r in results),
        "format_failures": sum(r.get("format_valid") is False for r in results),
    }), flush=True)


if __name__ == "__main__":
    main()
