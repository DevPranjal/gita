"""Explicitly authorized, separate quality analysis of immutable completed pairs."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import json
import math

from .campaign import slot_id, validate_receipt, verify_protocol
from .common import (IntegrityError, ProtocolError, digest, file_digest, git, read_json,
                     require, require_integrity, write_json)
from .dataset import verify
from .judge import DEVIATIONS, SCORE_NAME, _judge_call, grade_pair, labels_for, official
from .runtime import (CAMPAIGN_SECONDS, CreditLedger, JUDGE_CREDITS, file_lock,
                      reconcile_judge_outcomes)

PRIOR_REVIEWER_BOUND = 227.8672758
TOTAL_AUTHORIZED_CREDITS = 950


def inputs(corpus: Path, reviewer: Path, grader: Path, analysis: Path) -> dict:
    frozen = verify_protocol(reviewer, harness_root=analysis / "reviewer-harness")
    dataset = verify(corpus)
    require(dataset["digest"] == frozen["dataset_digest"], "Analysis dataset differs from reviews")
    authorization = read_json(analysis / "authorization.json")
    require(authorization == {
        "choice": "grade_completed_pairs", "reviewer_protocol": frozen["digest"],
        "judge_credit_cap": 200, "prior_review_unknown_usage_retained": True,
        "reviewer_retries": 0, "fixed_task_denominator": 25},
        "Explicit separate-analysis authorization required")
    for other in (corpus, reviewer, grader):
        require(analysis.resolve() != other.resolve()
                and analysis.resolve() not in other.resolve().parents
                and other.resolve() not in analysis.resolve().parents,
                "Analysis, source, reviewer and grader roots must remain separate")
    ledger = read_json(reviewer / "reviewer-ledger.json")
    require_integrity(ledger["protocol"] == frozen["digest"] and ledger["terminal_stop"] is None,
                      "Reviewer integrity failure cannot enter a separate analysis")
    require(not any(s["state"] == "reserved" for s in ledger["slots"].values()),
            "Active reviewer sessions block partial analysis")
    require(ledger["ceiling"] == frozen["budgets"]["reviewer_credits"],
            "Source reviewer ceiling differs from its frozen protocol")
    slots = ledger["slots"]
    require(all(s["state"] in ("settled", "unknown") for s in slots.values()),
            "Unexpected source ledger state")
    unknown = sum(s["cap"] for s in slots.values() if s["state"] == "unknown")
    require(all(type(s["cap"]) is int and s["cap"] == 30 for s in slots.values()),
            "Unbounded source reservation")
    require(all(type(s["credits"]) in (int, float) and math.isfinite(s["credits"])
                and 0 <= s["credits"] <= s["cap"]
                for s in slots.values() if s["state"] == "settled"),
            "Invalid settled source spend")
    require(abs(sum(s["credits"] for s in slots.values() if s["state"] == "settled")
                - ledger["spent"]) < 1e-6, "Source ledger spend mismatch")
    require(PRIOR_REVIEWER_BOUND + ledger["spent"] + unknown + JUDGE_CREDITS
            <= TOTAL_AUTHORIZED_CREDITS, "Combined study bound exceeds authorization")
    scopes = read_json(reviewer / "scopes.json")
    receipts, fingerprints, delivery = {}, {}, {"B": {}, "H": {}}
    for slot in frozen["schedule"]:
        key = slot_id(slot)
        directory = reviewer / "attempts" / key
        delivery[slot["arm"]][slot["task"]] = "unfinished"
        if key not in slots:
            require(not directory.exists(), "Unledgered reviewer attempt")
            continue
        row = validate_receipt(directory, slot, frozen)
        state = slots[key]
        if state["state"] == "settled":
            require_integrity(row.get("usage") is not None
                              and row["usage"]["credits"] == state["credits"],
                              "Source receipt disagrees with settled spend")
        else:
            require(row["status"] != "valid" and row.get("usage") is None,
                    "Unknown session must not masquerade as a valid review")
        fingerprints[key] = file_digest(directory / "result.json")
        delivery[slot["arm"]][slot["task"]] = row["status"]
        if row["status"] == "valid":
            receipts.setdefault(slot["task"], []).append((slot["arm"], row))
    pairs = {task: rows for task, rows in receipts.items()
             if len(rows) == 2 and {arm for arm, _ in rows} == {"B", "H"}}
    require(len(scopes) == 25 and len(pairs) == 21, "Authorized population is exactly 21/25 pairs")
    sources = Path(__file__).parent
    binding = {
        "reviewer_protocol": frozen["digest"], "dataset_digest": dataset["digest"],
        "reviewer_ledger_digest": file_digest(reviewer / "reviewer-ledger.json"),
        "authorization_digest": file_digest(analysis / "authorization.json"),
        "receipt_digests": fingerprints, "paired_task_ids": sorted(pairs),
        "analysis_root_digest": digest(str(analysis.resolve())),
        "reviewer_root_digest": digest(str(reviewer.resolve())),
        "grader_labels_manifest_digest": file_digest(grader / "labels.json"),
        "analysis_sources": {p.name: file_digest(p) for p in sorted(sources.iterdir())
                             if p.suffix in (".py", ".mjs")},
        "source_reviewer_cost_upper": ledger["spent"] + unknown,
        "retained_source_unknown_reservations": unknown,
        "all_prior_reviewer_cost_upper": PRIOR_REVIEWER_BOUND + ledger["spent"] + unknown,
    }
    return {"binding": binding, "dataset": dataset, "scopes": scopes,
            "pairs": pairs, "delivery": delivery, "seed": frozen["seed"]}


def seal(corpus: Path, reviewer: Path, grader: Path, analysis: Path) -> dict:
    captured = inputs(corpus, reviewer, grader, analysis)
    body = {"kind": "authorized-partial-quality-v1", "binding": captured["binding"],
            "source_git_head": git(Path(__file__).parent.parents[3], "rev-parse", "HEAD").decode().strip(),
            "seed": captured["seed"], "model": "auto", "native_streaming": "off",
            "judge_credit_cap": JUDGE_CREDITS, "fixed_tasks": 25, "eligible_pairs": 21,
            "reviewer_retries": 0, "judge_retries": 0,
            "deadline_seconds": CAMPAIGN_SECONDS, "score_name": SCORE_NAME,
            "deviations": DEVIATIONS + [
                "Separate retrospective analysis of 21 valid pairs after a stopped 25-task campaign.",
                "Prior unknown reviewer usage remains bounded and unresolved; ledger is never cleared.",
                "Native token streaming disabled; full JSON events and terminal usage remain required."],
            "manual_audit": "pending"}
    frozen = {**body, "digest": digest(body)}
    path = analysis / "protocol.json"
    if path.exists():
        require(read_json(path) == frozen, "Existing separate-analysis protocol mismatch")
    else:
        write_json(path, frozen)
    return frozen


def verify_analysis(corpus: Path, reviewer: Path, grader: Path, analysis: Path) -> tuple[dict, dict]:
    frozen = read_json(analysis / "protocol.json")
    require(frozen["digest"] == digest({k: v for k, v in frozen.items() if k != "digest"}),
            "Separate-analysis protocol tampered")
    captured = inputs(corpus, reviewer, grader, analysis)
    require_integrity(captured["binding"] == frozen["binding"],
                      "Immutable reviews, source ledger, labels or analysis code changed")
    require(frozen["judge_credit_cap"] == JUDGE_CREDITS
            and frozen["kind"] == "authorized-partial-quality-v1", "Invalid analysis ceiling/kind")
    return frozen, captured


def grade(corpus: Path, reviewer: Path, grader: Path, analysis: Path) -> dict:
    frozen, captured = verify_analysis(corpus, reviewer, grader, analysis)
    ledger = CreditLedger(analysis / "judge-ledger.json", frozen["digest"], JUDGE_CREDITS)
    deadline = datetime.fromisoformat(ledger.snapshot()["created"]).timestamp() + CAMPAIGN_SECONDS
    scores, pending = {"B": [], "H": []}, []
    with file_lock(analysis / "campaign.lock"):
        require(reconcile_judge_outcomes(analysis, frozen["digest"]) is None,
                "Unresolved or integrity-failed analysis judge blocks further sessions")
        if "stream-qualification" not in ledger.snapshot()["slots"]:
            answer = _judge_call(analysis / "qualification", 'Return only {"qualified":true}.',
                                 ledger, "stream-qualification", deadline)
            write_json(analysis / "qualification" / "answer.json", answer)
        require(read_json(analysis / "qualification" / "answer.json") == {"qualified": True},
                "Nonstreaming zero-tool qualification failed")
        for scope in captured["scopes"]:
            task = scope["task"]
            if task["id"] not in captured["pairs"]:
                pending.append({"task": task["id"], "reason": "No valid complete primary pair"})
                continue
            # Recheck immutable receipts and remaining judge stops before every paid task.
            verify_analysis(corpus, reviewer, grader, analysis)
            require(reconcile_judge_outcomes(analysis, frozen["digest"]) is None,
                    "Prior judge failure blocks additional model sessions")
            try:
                arms = grade_pair(corpus, grader, captured["dataset"], scope,
                                  captured["pairs"][task["id"]], frozen, ledger, deadline)
                for arm in ("B", "H"):
                    scores[arm].append(arms[arm])
            except IntegrityError as error:
                ledger.halt_integrity(task["id"], str(error))
                pending.append({"task": task["id"], "reason": "Integrity: " + str(error)})
                break
            except (ProtocolError, OSError, ValueError) as error:
                pending.append({"task": task["id"], "reason": str(error)})
                if (reconcile_judge_outcomes(analysis, frozen["digest"]) is not None
                        or ledger.snapshot()["spent"] + 30 > JUDGE_CREDITS):
                    break
            output = results(corpus, grader, frozen, captured, scores, pending, ledger)
            write_json(analysis / "quality.json", output)
        output = results(corpus, grader, frozen, captured, scores, pending, ledger)
        write_json(analysis / "quality.json", output)
        return output


def results(corpus: Path, grader: Path, frozen: dict, captured: dict, scores: dict,
            pending: list[dict], ledger: CreditLedger) -> dict:
    aggregates = {arm: official(corpus, scores[arm]) if scores[arm] else None for arm in ("B", "H")}
    bearing = [s["task"]["id"] for s in captured["scopes"]
               if any(g["tp_fp"] == "tp" for g in labels_for(grader, captured["dataset"], s["task"]))]
    bounds = {}
    for arm in ("B", "H"):
        measured = {row["pr_key"]: row["metrics"]["overall"]["grounded_recall"]
                    for row in (aggregates[arm]["per_pr"] if aggregates[arm] else [])}
        lower = sum(measured[key] for key in bearing if key in measured)
        upper = lower + sum(key not in measured and captured["delivery"][arm][key]
                            in ("valid", "unfinished") for key in bearing)
        bounds[arm] = {"tp_bearing_fixed_tasks": len(bearing),
                       "lower": lower / len(bearing) if bearing else None,
                       "upper": upper / len(bearing) if bearing else None}
    return {"name": SCORE_NAME, "protocol": frozen["digest"],
            "reviewer_protocol": frozen["binding"]["reviewer_protocol"],
            "deviations": frozen["deviations"], "official_leaderboard_comparable": False,
            "manual_audit": "pending", "fixed_tasks": 25, "eligible_pairs": 21,
            "scored_pairs": len(scores["B"]), "pending": pending, "ledger": ledger.snapshot(),
            "unscored_task_ids": [s["task"]["id"] for s in captured["scopes"]
                                  if s["task"]["id"] not in {r["pr_key"] for r in scores["B"]}],
            "scores": aggregates, "fixed_delivered_grounded_recall_bounds": bounds,
            "bounds_interpretation": "Worst/best missing-delivery bounds, not confidence intervals",
            "retained_reviewer_unknown_reservations":
                frozen["binding"]["retained_source_unknown_reservations"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("seal", "grade"))
    for name in ("corpus", "reviewer", "grader", "analysis"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "seal":
            result = seal(args.corpus, args.reviewer, args.grader, args.analysis)
        else:
            result = grade(args.corpus, args.reviewer, args.grader, args.analysis)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return int(args.operation == "grade" and result["scored_pairs"] != 21)
    except (ProtocolError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "refused", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
