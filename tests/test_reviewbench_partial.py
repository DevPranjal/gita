"""Separate analysis never clears unknown reviewer costs or invents missing reviews."""
import json
from pathlib import Path
import sys

import pytest

from gita.eval.reviewbench import campaign, judge, partial, runtime
from gita.eval.reviewbench.common import (IntegrityError, ProtocolError, digest,
                                        file_digest, read_json, write_json)


@pytest.fixture
def study(tmp_path, monkeypatch):
    corpus, reviewer, grader, analysis = (tmp_path / n for n in ("corpus", "reviewer", "grader", "analysis"))
    for p in (corpus, reviewer, grader, analysis):
        p.mkdir()
    scopes = [{"task": {"id": str(i)}} for i in range(25)]
    schedule = [{"task": str(i), "arm": arm, "repeat": 0, "attempt": 0}
                for i in range(25) for arm in ("B", "H")]
    frozen = {"digest": "review", "dataset_digest": "data", "schedule": schedule, "seed": 7,
              "budgets": {"reviewer_credits": 400}}
    monkeypatch.setattr(partial, "verify_protocol", lambda path, **kwargs: frozen)
    monkeypatch.setattr(partial, "verify", lambda path: {"digest": "data"})
    monkeypatch.setattr(partial, "validate_receipt",
                        lambda directory, *args: read_json(directory / "result.json"))
    slots = {}
    for slot in schedule[:44]:
        key = campaign.slot_id(slot)
        unknown = slot["task"] == "21" and slot["arm"] == "B"
        row = {"status": "measurement_invalid" if unknown else "valid",
               "usage": None if unknown else {"credits": 1}, "findings": []}
        write_json(reviewer / "attempts" / key / "result.json", row)
        slots[key] = {"state": "unknown" if unknown else "settled", "cap": 30,
                      "credits": None if unknown else 1}
    ledger = {"protocol": "review", "terminal_stop": None, "slots": slots, "spent": 43, "ceiling": 400}
    write_json(reviewer / "reviewer-ledger.json", ledger)
    write_json(reviewer / "scopes.json", scopes)
    write_json(grader / "labels.json", {})
    write_json(analysis / "authorization.json", {
        "choice": "grade_completed_pairs", "reviewer_protocol": "review",
        "judge_credit_cap": 200, "prior_review_unknown_usage_retained": True,
        "reviewer_retries": 0, "fixed_task_denominator": 25})
    return corpus, reviewer, grader, analysis


def test_separate_seal_retains_unknown_cost_and_all_fixed_slots(study):
    before = file_digest(study[1] / "reviewer-ledger.json")
    frozen = partial.seal(*study)
    assert frozen["fixed_tasks"] == 25
    assert frozen["eligible_pairs"] == 21
    assert frozen["binding"]["retained_source_unknown_reservations"] == 30
    assert frozen["binding"]["source_reviewer_cost_upper"] == 73
    assert file_digest(study[1] / "reviewer-ledger.json") == before
    assert partial.verify_analysis(*study)[0] == frozen


def test_separate_analysis_rejects_receipt_changes(study):
    partial.seal(*study)
    path = next((study[1] / "attempts").glob("*/result.json"))
    row = read_json(path)
    row["findings"] = [{"message": "changed"}]
    write_json(path, row)
    with pytest.raises(IntegrityError, match="changed"):
        partial.verify_analysis(*study)


@pytest.mark.parametrize("change", ["active", "integrity", "unbounded", "over_budget", "mismatched_spend"])
def test_separate_analysis_refuses_unsafe_source_state(study, change):
    path = study[1] / "reviewer-ledger.json"
    ledger = read_json(path)
    slot = next(iter(ledger["slots"].values()))
    if change == "active":
        slot["state"] = "reserved"
    elif change == "integrity":
        ledger["terminal_stop"] = {"kind": "integrity"}
    elif change == "unbounded":
        slot["cap"] = 31
    elif change == "over_budget":
        ledger["spent"] = 800
        slot["credits"] += 757
    else:
        ledger["spent"] += 1
    write_json(path, ledger)
    with pytest.raises(ProtocolError):
        partial.seal(*study)
    assert not (study[3] / "judge-ledger.json").exists()


def test_frozen_source_archive_is_checked_not_assumed(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    old = archive / "old.py"
    old.write_text("old exact source")
    body = {"run_root_digest": digest(str(tmp_path.resolve())),
            "product_policy_digest": "policy", "harness_sources": {"old.py": file_digest(old)},
            "scope_digests": {}}
    write_json(tmp_path / "protocol.json", {**body, "digest": digest(body)})
    write_json(tmp_path / "scopes.json", [])
    monkeypatch.setattr(campaign, "policy_digest", lambda: "policy")
    assert campaign.verify_protocol(tmp_path, harness_root=archive)["harness_sources"]
    old.write_text("tampered")
    with pytest.raises(ProtocolError, match="Harness changed"):
        campaign.verify_protocol(tmp_path, harness_root=archive)


def test_nonstreaming_invoke_records_exact_native_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "command", lambda *args:
                        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"])
    directory = tmp_path / "attempt"
    directory.mkdir()
    result = runtime.invoke(directory, "JSON only", [], None, streaming=False, timeout=10)
    assert read_json(directory / "invocation.json")["arguments_without_prompt"][-2:] == ["--stream", "off"]
    assert result["usage"] is None


def test_grade_pair_rejects_unknown_review_before_labels_or_models(tmp_path):
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "analysis", 200)
    with pytest.raises(ProtocolError, match="Invalid/unknown"):
        judge.grade_pair(tmp_path, tmp_path, {}, {"task": {"id": "a"}},
                         [("B", {"status": "valid", "usage": {}}),
                          ("H", {"status": "measurement_invalid", "usage": None})],
                         {}, ledger, 0)


def test_fixed_recall_bounds_handle_tp_free_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(partial, "official", lambda root, rows: {
        "per_pr": [
            {"pr_key": "tp", "metrics": {"overall": {"grounded_recall": 0.5}}},
            {"pr_key": "no_tp", "metrics": {"overall": {"grounded_recall": None}}}]})
    monkeypatch.setattr(partial, "labels_for", lambda grader, dataset, task:
                        [{"tp_fp": "tp"}] if task["id"] != "no_tp" else [])
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "analysis", 200)
    frozen = {"digest": "analysis", "deviations": [],
              "binding": {"reviewer_protocol": "review", "retained_source_unknown_reservations": 30}}
    captured = {"scopes": [{"task": {"id": n}} for n in ("tp", "missing", "no_tp")],
                "dataset": {}, "delivery": {
                    "B": {"tp": "valid", "missing": "measurement_invalid", "no_tp": "valid"},
                    "H": {"tp": "valid", "missing": "unfinished", "no_tp": "valid"}}}
    result = partial.results(tmp_path, tmp_path, frozen, captured,
                             {"B": [{"pr_key": "tp"}], "H": [{"pr_key": "tp"}]}, [], ledger)
    assert result["fixed_delivered_grounded_recall_bounds"]["B"] == {
        "tp_bearing_fixed_tasks": 2, "lower": 0.25, "upper": 0.25}
    assert result["fixed_delivered_grounded_recall_bounds"]["H"]["upper"] == 0.75


def test_compact_judge_paths_keep_full_identity_and_reject_collisions(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "labels_for", lambda *args: [])
    def stop(*args):
        raise ProtocolError("before paid calls")
    monkeypatch.setattr(judge, "source_context", stop)
    task, protocol = "a" * 64, "b" * 64
    rows = [(arm, {"status": "valid", "usage": {"credits": 1}, "findings": []})
            for arm in ("B", "H")]
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "analysis", 200)
    with pytest.raises(ProtocolError, match="before paid"):
        judge.grade_pair(tmp_path, tmp_path, {}, {"task": {"id": task}},
                         rows, {"digest": protocol, "seed": 7}, ledger, 0)
    destination = tmp_path / "judgments" / protocol[:32] / task[:32]
    assert read_json(destination / "binding.json") == {"protocol": protocol, "task": task}
    assert len(str(destination / "matcher" / "workspace")) < 240
    with pytest.raises(IntegrityError, match="collision"):
        judge.grade_pair(tmp_path, tmp_path, {}, {"task": {"id": task[:32] + "c" * 32}},
                         rows, {"digest": protocol, "seed": 7}, ledger, 0)


def test_smaller_judge_ceiling_reconciles_without_reset(tmp_path):
    ledger = runtime.CreditLedger(tmp_path / "judge-ledger.json", "analysis", 169)
    assert runtime.reconcile_judge_outcomes(tmp_path, "analysis") is None
    assert ledger.snapshot()["ceiling"] == 169


def test_prior_failed_judge_reservation_reduces_next_cap(study):
    prior = study[3].parent / "prior-quality"
    prior.mkdir()
    body = {"binding": {"reviewer_protocol": "review", "analysis_sources": {}},
            "judge_credit_cap": 200}
    frozen = {**body, "digest": digest(body)}
    write_json(prior / "protocol.json", frozen)
    known = prior / "qualification.json"
    unknown = prior / "failed.json"
    write_json(known, {"status": "runtime_complete", "usage": {"credits": 0.5}})
    write_json(unknown, {"status": "measurement_invalid", "usage": None})
    ledger = {"protocol": frozen["digest"], "ceiling": 200, "terminal_stop": None, "spent": 0.5,
              "slots": {"known": {"state": "settled", "cap": 30, "credits": 0.5,
                                  "receipt_path": str(known)},
                        "unknown": {"state": "unknown", "cap": 30, "credits": None,
                                    "receipt_path": str(unknown)}}}
    write_json(prior / "judge-ledger.json", ledger)
    write_json(prior / "quality.json", {"protocol": frozen["digest"], "scored_pairs": 0})
    entries = [{"root": str(prior), "protocol_sha256": file_digest(prior / "protocol.json"),
                "ledger_sha256": file_digest(prior / "judge-ledger.json"),
                "quality_sha256": file_digest(prior / "quality.json"),
                "source_archive": str(prior / "archive")}]
    write_json(study[3] / "prior-judges.json", entries)
    frozen = partial.seal(*study)
    assert frozen["judge_credit_cap"] == 169
    assert frozen["binding"]["prior_judge_cost_upper"] == 30.5
    assert frozen["judge_credit_cap"] + 30.5 <= 200
    ledger["slots"]["unknown"]["state"] = "settled"
    ledger["slots"]["unknown"]["credits"] = 0
    write_json(prior / "judge-ledger.json", ledger)
    with pytest.raises(ProtocolError, match="changed"):
        partial.verify_analysis(*study)
