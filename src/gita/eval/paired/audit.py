from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import read_json, utc_now, write_json


def comparison_map(directory: Path) -> dict:
    mapping = read_json(directory / "mapping.json")
    return {(mapping[c["candidate"]]["arm"], mapping[c["candidate"]]["index"], c["reference"]):
            c["verdict"] for c in read_json(directory / "comparisons.json")}


def compare_judges(original: Path, repeat: Path) -> dict:
    audited, disagreements, score_changes = [], [], []
    for path in sorted(repeat.glob("*/grade.json")):
        repeated = read_json(path)
        first_path = original / path.parent.name / "grade.json"
        if not first_path.exists():
            continue
        first = read_json(first_path)
        if first["status"] != "ok" or repeated["status"] != "ok":
            raise ValueError("Cannot count invalid judgments as agreement")
        a, b = comparison_map(first_path.parent), comparison_map(path.parent)
        if a.keys() != b.keys():
            raise ValueError("Judge repeats compare different candidate/reference pairs")
        changes = [{"arm": key[0], "candidate_index": key[1], "reference": key[2],
                    "first": a[key], "repeat": b[key]} for key in a if a[key] != b[key]]
        disagreements.extend({"task": repeated["task"], **c} for c in changes)
        for arm in ("baseline", "gita"):
            for metric in ("matched", "semantic_matched", "defect_matched"):
                if first["scores"][arm][metric] != repeated["scores"][arm][metric]:
                    score_changes.append({"task": repeated["task"], "arm": arm, "metric": metric,
                                          "first": first["scores"][arm][metric],
                                          "repeat": repeated["scores"][arm][metric]})
        audited.append({"task": repeated["task"], "pairs": len(a),
                        "disagreements": len(changes),
                        "input_order_changed": first["input_digest"] != repeated["input_digest"]})
    comparisons = sum(t["pairs"] for t in audited)
    return {"at": utc_now(), "tasks": len(audited), "comparisons": comparisons,
            "agreement": 1 - len(disagreements) / comparisons if comparisons else None,
            "disagreements": disagreements, "score_changes": score_changes, "audited": audited}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--repeat", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare_judges(args.original, args.repeat)
    write_json(args.output, result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
