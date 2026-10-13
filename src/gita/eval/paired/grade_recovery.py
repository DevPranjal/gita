"""Recover an existing judge call from its native journal, never ask a new judge."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import digest, execution_events, parse_usage, read_json, write_json
from .export import file_hash
from .judge import judge_comparisons, score


def recover_grades(source: Path, destination: Path) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("Recovery must preserve the original grade directory")
    copied, recovered = 0, []
    for path in sorted(source.glob("*/grade.json")):
        old = read_json(path)
        prior, target = path.parent, destination / path.parent.name
        payload = read_json(prior / "input.json")
        mapping = read_json(prior / "mapping.json")
        if digest(payload) != old["input_digest"]:
            raise ValueError("Original judge input was modified")
        result = {**old, "source_grade_sha256": file_hash(path)}
        if old["status"] == "ok":
            comparisons = read_json(prior / "comparisons.json")
            copied += 1
        elif old.get("error", "").startswith("JSONDecodeError:"):
            events, metadata = execution_events(prior / "call")
            comparisons = judge_comparisons(events, payload)
            usage = parse_usage(prior / "call" / "usage.json")
            result.update(
                status="ok", original_error=old["error"],
                recovered_from=str(prior), recovery=metadata, judge_usage=usage,
                native_judge_events_sha256=file_hash(prior / "call" / metadata["event_source"]),
                scores=score(payload, mapping, comparisons, ["baseline", "gita"]),
                uncertain_pairs=sum(c["verdict"] == "uncertain" for c in comparisons),
                comparisons=len(comparisons))
            recovered.append(old["task"])
        else:
            raise ValueError(f"Grade is not recoverable without another model call: {path}")
        write_json(target / "input.json", payload)
        write_json(target / "mapping.json", mapping)
        write_json(target / "comparisons.json", comparisons)
        write_json(target / "grade.json", result)
    return {"copied_valid_grades": copied, "recovered_grades": recovered, "new_model_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(recover_grades(args.source, args.output)), flush=True)


if __name__ == "__main__":
    main()
