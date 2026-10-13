from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

from .common import digest, finite_number, read_json, utc_now, write_json
from .judge import normalized_path, score, validate_judgment

METRICS = ("input_tokens", "total_tokens", "output_tokens", "reported_credits", "wall_seconds")


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def reduction(pairs: list[dict], metric: str) -> float | None:
    usable = metric_pairs(pairs, metric)
    before = sum(p["baseline"][metric] for p in usable)
    return 1 - sum(p["gita"][metric] for p in usable) / before if before else None


def metric_pairs(pairs: list[dict], metric: str) -> list[dict]:
    return [p for p in pairs if all(finite_number(p[a].get(metric))
                                   for a in ("baseline", "gita"))]


def quality_delta(pairs: list[dict], metric: str = "recall") -> float | None:
    usable = [p for p in pairs if p.get("scores") and
              all(p["scores"][a][metric] is not None for a in ("baseline", "gita"))]
    if not usable:
        return None
    return mean([p["scores"]["gita"][metric] - p["scores"]["baseline"][metric] for p in usable])


def intervals(pairs: list[dict], samples: int = 10000, seed: int = 190926) -> dict:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        clusters[pair["repo"]].append(pair)
    if len(clusters) < 2:
        return {}
    names = sorted(clusters)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = defaultdict(list)
    for _ in range(samples):
        sampled = [p for _ in names for p in clusters[rng.choice(names)]]
        for metric in METRICS:
            value = reduction(sampled, metric)
            if value is not None:
                draws[metric].append(value)
        for metric in ("recall", "semantic_recall", "defect_recall", "any_reference_detected"):
            value = quality_delta(sampled, metric)
            if value is not None:
                draws[f"{metric}_delta"].append(value)
    output = {}
    for name, values in draws.items():
        values.sort()
        output[name] = [values[int(.025 * (len(values) - 1))],
                        values[int(.975 * (len(values) - 1))]]
    return output


def aggregate(pairs: list[dict], bootstrap: bool = True) -> dict:
    if not pairs:
        return {"pairs": 0}
    graded = [p for p in pairs if p.get("scores")]
    result = {
        "pairs": len(pairs), "repositories": len({p["repo"] for p in pairs}),
        "graded_pairs": len(graded),
        "reductions": {m: reduction(pairs, m) for m in METRICS},
        "quality_deltas": {m: quality_delta(pairs, m)
                           for m in ("recall", "semantic_recall", "defect_recall",
                                     "any_reference_detected")},
        "metric_pair_counts": {m: len(metric_pairs(pairs, m)) for m in METRICS},
        "arms": {},
    }
    for arm in ("baseline", "gita"):
        totals = {m: sum(p[arm][m] for p in metric_pairs(pairs, m)) for m in METRICS}
        totals.update({m: sum(p[arm][m] for p in pairs) for m in (
            "uncached_input_tokens", "cache_read_tokens", "cache_write_tokens",
            "tool_calls", "gita_calls", "tool_errors", "model_requests",
            "tool_output_tokens_cl100k")})
        scores = [p["scores"][arm] for p in graded]
        matched = sum(s["matched"] for s in scores)
        generated = sum(s["generated"] for s in scores)
        references = sum(s["references"] for s in scores)
        result["arms"][arm] = {
            "totals": totals,
            "means": {m: totals[m] / result["metric_pair_counts"][m]
                      if result["metric_pair_counts"][m] else None for m in METRICS},
            "adoption": mean([bool(p[arm]["gita_calls"]) for p in pairs]),
            "matched": matched, "generated": generated, "references": references,
            "micro_recall": matched / references if references else None,
            "macro_recall": mean([s["recall"] for s in scores]) if scores else None,
            "semantic_macro_recall": mean([s["semantic_recall"] for s in scores]) if scores else None,
            "agreement_precision": matched / generated if generated else None,
            "agreement_f1": 2 * matched / (references + generated) if references + generated else None,
            "prs_with_detection": sum(s["any_reference_detected"] for s in scores),
            "prs_with_all_references": sum(s["all_references_detected"] for s in scores),
            "format_failures": sum(p[arm].get("format_valid") is False for p in pairs),
            "content_failures": sum(p[arm].get("content_valid") is False for p in pairs),
            "input_per_matched_reference": (sum(p[arm]["input_tokens"] for p in graded) / matched
                                            if matched else None),
        }
    result["intervals_95"] = intervals(pairs) if bootstrap else {}
    cost_ci = result["intervals_95"].get("input_tokens")
    quality_ci = result["intervals_95"].get("recall_delta")
    result["input_reduction_supported"] = bool(cost_ci and cost_ci[0] > 0)
    result["recall_noninferiority_supported"] = bool(
        len(graded) == len(pairs) and quality_ci and quality_ci[0] > -.05
        and result["arms"]["baseline"]["matched"] > 0
        and result["arms"]["gita"]["matched"] > 0)
    result["quality_floor_warning"] = any(result["arms"][a]["matched"] == 0
                                          for a in ("baseline", "gita"))
    return result


def collect(manifest: dict, run_roots: list[Path], grade_roots: list[Path],
            reconciled: bool = False) -> dict:
    tasks = {t["id"]: t for t in manifest["tasks"]}
    expected, runs, grades, scopes = set(), {}, {}, {}
    run_paths, grade_paths = {}, {}
    protocol = None
    for root in run_roots:
        experiment = read_json(root / "experiment.json")
        if experiment["manifest_digest"] != manifest["manifest_digest"]:
            raise ValueError("Run manifest mismatch")
        this_protocol = {k: experiment.get(k) for k in
                         ("model", "prompt_digest", "reasoning_effort", "context", "timeout")}
        if protocol is not None and this_protocol != protocol:
            raise ValueError("Cannot combine different reviewer protocols")
        protocol = this_protocol
        expected.update(experiment["tasks"])
        for task_id in experiment["tasks"]:
            scope = root / task_id / "scope.json"
            if scope.exists():
                scopes[task_id] = read_json(scope)
            for arm in ("baseline", "gita"):
                path = root / task_id / arm / ("analysis.json" if reconciled else "result.json")
                if path.exists():
                    key = (task_id, arm)
                    if key in runs:
                        raise ValueError(f"Duplicate attempt in report: {key}")
                    runs[key] = read_json(path)
                    run_paths[key] = path.parent
    for root in grade_roots:
        for path in sorted(root.glob("*/grade.json")):
            grade = read_json(path)
            if grade["task"] in grades:
                raise ValueError("Duplicate grade; keep judge repeats separate")
            grades[grade["task"]] = grade
            grade_paths[grade["task"]] = path.parent
    pairs, incomplete = [], []
    for task_id in sorted(expected):
        task = tasks[task_id]
        arms = {a: runs.get((task_id, a)) for a in ("baseline", "gita")}
        if not all(r and r["status"] == ("measured" if reconciled else "ok") for r in arms.values()):
            incomplete.append({"task": task_id, "repo": task["repo"],
                               "arms": {a: r["status"] if r else "missing" for a, r in arms.items()}})
            continue
        grade = grades.get(task_id, {})
        if grade.get("status") == "ok":
            verify_grade(task, grade, grade_paths[task_id],
                         {a: run_paths[(task_id, a)] for a in arms}, reconciled)
        pairs.append({"task": task_id, "repo": task["repo"], "language": task["language"],
                      "split": task["split"], **arms,
                      "scores": grade.get("scores") if grade.get("status") == "ok" else None})
    by_split = {split: aggregate([p for p in pairs if p["split"] == split])
                for split in ("pilot", "confirmation")}
    timeout = protocol.get("timeout") if protocol else None
    timing_warnings = [
        {"task": p["task"], "arm": a, "wall_seconds": p[a]["wall_seconds"],
         "arm_deadline_seconds": timeout}
        for p in pairs for a in ("baseline", "gita")
        if timeout is not None and finite_number(p[a].get("wall_seconds"))
        and p[a]["wall_seconds"] > timeout]
    timing_unavailable = [{"task": p["task"], "arm": a}
                          for p in pairs for a in ("baseline", "gita")
                          if not finite_number(p[a].get("wall_seconds"))]
    return {"generated_at": utc_now(), "manifest_digest": manifest["manifest_digest"],
            "reconciled": reconciled,
            "timing_warnings": timing_warnings,
            "timing_unavailable": timing_unavailable,
            "planned_pairs": len(expected), "complete_measured_pairs": len(pairs),
            "incomplete_pairs": incomplete, "invalid_grades": [
                {"task": t, "error": g.get("error")} for t, g in grades.items()
                if g["status"] != "ok"],
            "ungraded_measured_pairs": [p["task"] for p in pairs if not p["scores"]],
            "adjusted_bases": sum(s["base_adjusted"] for s in scopes.values()),
            "by_split": by_split,
            "by_language": {lang: aggregate([p for p in pairs if p["language"] == lang
                                             and p["split"] == "confirmation"], bootstrap=False)
                            for lang in sorted({p["language"] for p in pairs})},
            "pairs": pairs}


def verify_grade(task: dict, grade: dict, directory: Path,
                 arm_paths: dict[str, Path], reconciled: bool) -> None:
    if grade["reference_digest"] != task["reference_digest"]:
        raise ValueError("Grade reference digest mismatch")
    payload = read_json(directory / "input.json")
    if digest(payload) != grade["input_digest"]:
        raise ValueError("Grade input digest mismatch")
    mapping = read_json(directory / "mapping.json")
    answers = {a: read_json(p / ("content.json" if reconciled else "answer.json"))["findings"]
               for a, p in arm_paths.items()}
    seen = set()
    for candidate in payload["candidates"]:
        identity = mapping[candidate["id"]]
        arm, index = identity["arm"], identity["index"]
        if (arm, index) in seen or arm not in answers or not 0 <= index < len(answers[arm]):
            raise ValueError("Invalid grade candidate mapping")
        seen.add((arm, index))
        finding = answers[arm][index]
        expected = {"id": candidate["id"], **finding, "path": normalized_path(finding["path"])}
        if candidate != expected:
            raise ValueError("Grade was produced from a different reviewer answer")
    if seen != {(a, i) for a, fs in answers.items() for i in range(len(fs))}:
        raise ValueError("Grade omitted reviewer findings")
    comparisons = read_json(directory / "comparisons.json")
    validate_judgment({"comparisons": comparisons}, payload)
    if digest(score(payload, mapping, comparisons, list(answers))) != digest(grade["scores"]):
        raise ValueError("Stored grade scores do not reproduce from judgments")


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def points(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:+.1f} pp"


def render(report: dict) -> str:
    lines = [
        "# Paired AACR evaluation results",
        "", f"Generated: {report['generated_at']}. Model: GPT-5.6 Sol / medium / default.",
        "", f"Planned pairs: **{report['planned_pairs']}**; complete measured pairs: "
        f"**{report['complete_measured_pairs']}**; incomplete/invalid: "
        f"**{len(report['incomplete_pairs'])}**; measured but ungraded: "
        f"**{len(report['ungraded_measured_pairs'])}**.",
        "", f"Resolved merge-base adjustments: **{report['adjusted_bases']}**.",
        "", "Positive savings mean gita used less. Costs below are conditional on "
        "both arms having valid measured runs; incomplete pairs are NOT zero cost. "
        "Cached context is included in input tokens. No cache outliers are removed.",
    ]
    if report.get("timing_warnings"):
        lines += ["", "**Timing warning:** some measured arms exceed the configured "
                  "deadline. Raw wall time includes an environmental interruption; "
                  "do not interpret it as clean model/tool latency. Runs and token "
                  "costs remain included; see the environment audit and sensitivity results."]
    if report.get("timing_unavailable"):
        lines += ["", "**Timing coverage:** wrapper elapsed time is unavailable for "
                  f"{len(report['timing_unavailable'])} arm(s). Valid token/quality data "
                  "remain included; time comparisons use only complete timing pairs."]
    for split, summary in report["by_split"].items():
        lines += ["", f"## {split.title()}", ""]
        if not summary["pairs"]:
            lines.append("No completed paired results yet.")
            continue
        lines += [f"{summary['pairs']} PRs, {summary['repositories']} repositories, "
                  f"{summary['graded_pairs']} graded pairs.", "",
                  "| Metric | Baseline | Gita | Reduction [95% cluster interval] | Paired n |",
                  "|---|---:|---:|---:|---:|"]
        for metric in METRICS:
            ci = summary["intervals_95"].get(metric)
            interval = f" [{percent(ci[0])}, {percent(ci[1])}]" if ci else " [not estimated]"
            before, after = (summary["arms"][a]["means"][metric] for a in ("baseline", "gita"))
            before_text = "N/A" if before is None else f"{before:,.2f}"
            after_text = "N/A" if after is None else f"{after:,.2f}"
            lines.append(f"| Mean {metric} | {before_text} | {after_text}"
                         f" | {percent(summary['reductions'][metric])}{interval}"
                         f" | {summary['metric_pair_counts'][metric]} |")
        lines += ["", "| Quality (reference agreement, not executable task success) | Baseline | Gita |",
                  "|---|---:|---:|"]
        for metric in ("macro_recall", "micro_recall", "semantic_macro_recall",
                       "agreement_precision", "agreement_f1", "adoption"):
            lines.append(f"| {metric} | {percent(summary['arms']['baseline'][metric])}"
                         f" | {percent(summary['arms']['gita'][metric])} |")
        for metric in ("matched", "generated", "references", "prs_with_detection",
                       "prs_with_all_references", "format_failures", "content_failures"):
            lines.append(f"| {metric} | {summary['arms']['baseline'][metric]}"
                         f" | {summary['arms']['gita'][metric]} |")
        ci = summary["intervals_95"].get("recall_delta")
        lines += ["", f"Macro recall delta: **{points(summary['quality_deltas']['recall'])}**"
                  + (f" (95% CI [{points(ci[0])}, {points(ci[1])}])." if ci else "."),
                  f"Input-token reduction supported: **{summary['input_reduction_supported']}**.",
                  "Recall non-inferiority at predeclared -5 percentage-point margin: "
                  f"**{summary['recall_noninferiority_supported']}**.",
                  f"Zero-match quality floor warning: **{summary['quality_floor_warning']}**.",
                  "", "These checks do not turn annotation agreement into actual task completion."]
        detection_ci = summary["intervals_95"].get("any_reference_detected_delta")
        lines += ["", "PRs with at least one reference match, rate delta: "
                  f"**{points(summary['quality_deltas']['any_reference_detected'])}**"
                  + (f" (95% CI [{points(detection_ci[0])}, {points(detection_ci[1])}])."
                     if detection_ci else "."),
                  "This detection proxy is separate from macro issue recall; "
                  "do not use the macro non-inferiority result to claim equal PR success."]
    lines += ["", "## Confirmation by language", "",
              "| Language | Pairs | Input reduction | Macro recall delta |",
              "|---|---:|---:|---:|"]
    for lang, summary in report["by_language"].items():
        if summary["pairs"]:
            lines.append(f"| {lang} | {summary['pairs']} | {percent(summary['reductions']['input_tokens'])}"
                         f" | {points(summary['quality_deltas']['recall'])} |")
    lines += ["", "## Limitations", "",
              "- Public benchmark contamination cannot be ruled out.",
              "- Same-model semantic judge; inspect saved judgments and repeat audits.",
              "- Unmatched findings can be valid novel issues; agreement precision is not true precision.",
              "- This tests isolated code review, not fixing bugs, test pass rates or history reuse.",
              "- Small samples and repository clustering can leave wide intervals.",
              "- Pilot is diagnostic and excluded from confirmation claims.",
              "- Raw runs are local and ignored by Git; versioned aggregates contain no credentials.",
              ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--grades", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reconciled", action="store_true")
    args = parser.parse_args()
    report = collect(read_json(args.manifest), args.runs, args.grades, args.reconciled)
    write_json(args.output.with_suffix(".json"), report)
    args.output.write_text(render(report), encoding="utf8")
    print(render(report))


if __name__ == "__main__":
    main()
