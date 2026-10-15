"""Small, sanitized resource/delivery summary; raw private traces stay private."""
from __future__ import annotations

from pathlib import Path

from .campaign import slot_id, validate_receipt, verify_protocol
from .common import read_json, require, write_json


def report(run_root: Path, *, plumbing_credits: float = 0.422946) -> dict:
    frozen = verify_protocol(run_root)
    rows = []
    for slot in frozen["schedule"]:
        path = run_root / "attempts" / slot_id(slot)
        if not (path / "result.json").exists():
            rows.append({**slot, "status": "unfinished", "credits": None, "findings_count": None})
            continue
        receipt = validate_receipt(path, slot, frozen)
        usage = receipt.get("usage")
        rows.append({**slot, "status": receipt["status"],
                     "findings_count": len(receipt["findings"]) if receipt["status"] == "valid" else None,
                     "credits": usage["credits"] if usage else None,
                     "wall_seconds": receipt["wall_seconds"],
                     "usage": usage, "tool_audit": receipt.get("tool_audit")})
    arms = {}
    for arm in ("B", "H"):
        selected = [row for row in rows if row["arm"] == arm]
        measured = [row for row in selected if row["credits"] is not None]
        arms[arm] = {"scheduled_slots": len(selected), "valid": sum(
            row["status"] == "valid" for row in selected),
            "unfinished": sum(row["status"] == "unfinished" for row in selected),
            "failed": sum(row["status"] not in ("unfinished", "valid") for row in selected),
            "measured_attempts": len(measured), "observed_credits": sum(row["credits"] for row in measured),
            "campaign_credit_total": (sum(row["credits"] for row in measured)
                                      if len(measured) == len(selected) else None),
            "observed_input_tokens": sum(row["usage"]["input_tokens"] for row in measured),
            "mean_input_per_fixed_slot": (sum(row["usage"]["input_tokens"] for row in measured) / len(selected)
                                           if len(measured) == len(selected) else None)}
    reviewer = read_json(run_root / "reviewer-ledger.json") if (run_root / "reviewer-ledger.json").exists() else None
    judge = read_json(run_root / "judge-ledger.json") if (run_root / "judge-ledger.json").exists() else None
    quality = read_json(run_root / "quality.json") if (run_root / "quality.json").exists() else None
    if quality:
        quality = {key: value for key, value in quality.items() if key not in ("pending", "ledger")}
        quality["pending_count"] = len(read_json(run_root / "quality.json")["pending"])
    credits = sum(ledger["spent"] for ledger in (reviewer, judge) if ledger)
    outstanding = sum(30 for ledger in (reviewer, judge) if ledger
                      for slot in ledger["slots"].values() if slot["state"] != "settled")
    require(credits <= 950, "Scientific campaign budget violated")
    output = {"version": frozen["version"], "protocol": frozen["digest"], "pin": frozen["pin"],
              "experiment": "agent+product-auto+tools; NOT fixed-model causal attribution",
              "cohort": "public development starter; one repeat; no automatic retries",
              "scheduled_tasks": 25, "scheduled_sessions": 50, "arms": arms, "slots": rows,
              "quality": quality,
              "reviewer_observed_credits": reviewer["spent"] if reviewer else 0,
              "judge_observed_credits": judge["spent"] if judge else 0,
              "campaign_observed_credits": credits,
              "campaign_credit_bounds": {"observed_lower": credits,
                                         "upper_with_unsettled_reservations": credits + outstanding},
              "separate_plumbing_overhead_credits": plumbing_credits,
              "evaluation_observed_credits_including_plumbing": credits + plumbing_credits,
              "accounting_complete": all(row["credits"] is not None for row in rows),
              "conclusion": "Development measurements only. No efficacy claim; manual quality audit required."}
    write_json(run_root / "sanitized-results.json", output)
    return output
