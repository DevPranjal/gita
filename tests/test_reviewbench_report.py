"""Report declared populations and measured spend, not hard-coded auto campaigns."""
from gita.eval.reviewbench import report as module
from gita.eval.reviewbench.common import write_json


def test_report_uses_fixed_model_population_and_actual_reservations(tmp_path, monkeypatch):
    frozen = {"version": "test", "digest": "protocol", "pin": "pin",
              "model": "gpt-5.6-sol", "reviewer_reasoning": "medium",
              "judge_model": "gpt-5.6-sol", "judge_reasoning": "medium",
              "interpretation": "Fixed reviewer model; engine workflow",
              "budgets": {"reviewer_credits": 300, "judge_credits": 150},
              "schedule": [{"task": "one", "arm": "B", "repeat": 0, "attempt": 0},
                           {"task": "one", "arm": "H", "repeat": 0, "attempt": 0}]}
    monkeypatch.setattr(module, "verify_protocol", lambda root: frozen)
    write_json(tmp_path / "reviewer-ledger.json", {
        "spent": 32.5, "slots": {"first": {"state": "overrun", "credits": 32.5, "cap": 30},
                                "second": {"state": "unknown", "credits": None, "cap": 45}}})
    result = module.report(tmp_path)
    assert result["scheduled_tasks"] == 1 and result["scheduled_sessions"] == 2
    assert result["reviewer_model"] == "gpt-5.6-sol"
    assert result["reviewer_observed_credits"] == 32.5
    assert result["campaign_credit_bounds"]["upper_with_unsettled_reservations"] == 77.5
    assert result["separate_plumbing_overhead_credits"] == 0
    assert result["frozen_campaign_ceiling"] == 450
    assert not result["accounting_complete"]
