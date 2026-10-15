"""Offline adversarial tests: never start a model."""
import json
from pathlib import Path

import pytest

from gita.eval.reviewbench.common import ProtocolError, digest
from gita.eval.reviewbench.runtime import CreditLedger, REVIEWER_CREDITS, parse_usage
from gita.eval.reviewbench.judge import resolve, validate_judgments


def usage(models=("gpt-6-luna",)):
    metrics = {
        model: {"usage": {"inputTokens": 100, "outputTokens": 10,
                          "cacheReadTokens": 20, "cacheWriteTokens": 30},
                "requests": {"count": 1}, "totalNanoAiu": 0.1e9}
        for model in models
    }
    return {"currentModel": models[-1], "modelMetrics": metrics,
            "agentMetrics": {"main": {"totalNanoAiu": len(models) * 0.1e9}},
            "totalNanoAiu": len(models) * 0.1e9, "totalApiDurationMs": 5,
            "tokenDetails": {
                name: {"tokenCount": count * len(models)}
                for name, count in {"input": 50, "output": 10,
                                    "cache_read": 20, "cache_write": 30}.items()
            }}


def test_auto_routes_not_hardcoded_and_not_doublecounted():
    result = parse_usage(usage(("gpt-6-luna", "claude-opus-5")))
    assert result["models"] == ["claude-opus-5", "gpt-6-luna"]
    assert result["input_tokens"] == 200
    assert result["credits"] == pytest.approx(0.2)


@pytest.mark.parametrize("mutation", ["missing", "nested", "partitions", "negative"])
def test_usage_fails_closed(mutation):
    value = usage()
    if mutation == "missing":
        del value["totalNanoAiu"]
    elif mutation == "nested":
        value["agentMetrics"]["child"] = {}
    elif mutation == "partitions":
        value["tokenDetails"]["input"]["tokenCount"] = 101
    else:
        value["totalNanoAiu"] = -1
    with pytest.raises(ProtocolError):
        parse_usage(value)


def test_reservations_include_interrupted_unknown_spend(tmp_path):
    ledger = CreditLedger(tmp_path / "ledger.json", digest("protocol"), REVIEWER_CREDITS)
    count = REVIEWER_CREDITS // 29
    for i in range(count):
        ledger.reserve(str(i))
        ledger.settle(str(i), 29)
    with pytest.raises(ProtocolError, match="budget"):
        ledger.reserve("26")
    assert ledger.snapshot()["spent"] == count * 29
    other = CreditLedger(tmp_path / "unknown.json", digest("protocol"), REVIEWER_CREDITS)
    other.reserve("interrupted")
    other.settle("interrupted", None)
    with pytest.raises(ProtocolError, match="unknown"):
        other.reserve("retry")


def test_resume_rejects_protocol_mismatch(tmp_path):
    CreditLedger(tmp_path / "ledger.json", digest("p1"), REVIEWER_CREDITS)
    with pytest.raises(ProtocolError, match="protocol"):
        CreditLedger(tmp_path / "ledger.json", digest("p2"), REVIEWER_CREDITS)


def test_official_resolver_many_to_many_order():
    rows = [{"candidate_index": 0, "matched_golden_indices": [1, 0]},
            {"candidate_index": 1, "matched_golden_indices": [0]},
            {"candidate_index": 2, "matched_golden_indices": [1]}]
    result = resolve(rows, 2, 3)
    assert result["matched"] == {0: 0, 2: 1}
    assert result["covered"] == {0, 1}
    assert result["unmatched"] == {1}


@pytest.mark.parametrize("rows", [
    [],
    [{"candidate_index": 0, "matched_golden_indices": []}] * 2,
    [{"candidate_index": 0, "matched_golden_indices": [4]}],
])
def test_malformed_missing_duplicate_judge_rows(rows):
    with pytest.raises(ProtocolError):
        validate_judgments({"correspondences": rows}, 1, 1, classify=False)


def test_max_two_reservations_and_persisted_started_slots(tmp_path):
    ledger = CreditLedger(tmp_path / "ledger.json", digest("p"), REVIEWER_CREDITS)
    ledger.reserve("a")
    ledger.reserve("b")
    with pytest.raises(ProtocolError, match="two"):
        ledger.reserve("c")
    resumed = CreditLedger(tmp_path / "ledger.json", digest("p"), REVIEWER_CREDITS)
    with pytest.raises(ProtocolError, match="retried"):
        resumed.reserve("a")


def test_native_cap_violation_retains_measured_overrun_receipt(tmp_path):
    ledger = CreditLedger(tmp_path / "ledger.json", digest("p"), REVIEWER_CREDITS)
    ledger.reserve("a")
    with pytest.raises(ProtocolError, match="exceeded"):
        ledger.settle("a", 31)
    assert ledger.snapshot()["slots"]["a"]["state"] == "overrun"
    assert ledger.snapshot()["spent"] == 31
    assert ledger.snapshot()["terminal_stop"]["kind"] == "integrity"


def test_judge_sentinel_not_ignored_empty_flag(monkeypatch, tmp_path):
    from gita.eval.reviewbench.runtime import command
    monkeypatch.setattr("shutil.which", lambda _: "copilot.exe")
    args = command(tmp_path, "fixed prompt", [], None)
    assert args[args.index("--available-tools") + 1] == "review-no-such-tool"
    assert args[args.index("--max-ai-credits") + 1] == "30"
    assert "--model" in args and args[args.index("--model") + 1] == "auto"


@pytest.mark.parametrize("tools", [["powershell"], ["task"], ["web_fetch"]])
def test_inventories_reject_builtin_leaks_and_every_transition(tools):
    from gita.eval.reviewbench.runtime import inventories
    def event(names):
        return {"type": "session.usage_checkpoint", "data": {
            "promptCacheBreakState": [{"models": {"auto-route": {
                "tool_count": len(names), "tools": [{"name": name} for name in names]}}}]}}
    with pytest.raises(ProtocolError, match="tools"):
        inventories([event([]), event(tools), event([])], set())


def test_missing_inventory_or_tool_completion_fails():
    from gita.eval.reviewbench.runtime import inventories
    with pytest.raises(ProtocolError, match="Missing"):
        inventories([], set())


def test_classifications_fail_closed():
    base = {"candidate_index": 0, "tp_fp": "tp", "severity": "high",
            "category": "bug", "evidence": "f.py:2 exact call"}
    assert validate_judgments({"classifications": [base]}, 1, 1, classify=True) == [base]
    for rows in ([], [base, base], [{**base, "tp_fp": "uncertain"}],
                 [{k: v for k, v in base.items() if k != "evidence"}]):
        with pytest.raises(ProtocolError):
            validate_judgments({"classifications": rows}, 1, 1, classify=True)


def test_final_json_does_not_salvage_failed_or_fenced_answer():
    from gita.eval.reviewbench.common import final_answer
    with pytest.raises(ProtocolError):
        final_answer([])
    with pytest.raises(ProtocolError):
        final_answer([{"type": "assistant.message", "data": {
            "phase": "final_answer", "content": '```json\n{"findings":[]}\n```'}}])


def test_parser_success_without_native_inventory_is_not_ready(monkeypatch):
    from gita.eval.reviewbench.preparation import provision_parsers
    import gita.index.evidence  # Bind the real parser before mocking the external import.
    monkeypatch.setattr("tree_sitter_language_pack.get_parser", lambda _: object())
    monkeypatch.setattr("gita.index.evidence._artifacts", lambda: ())
    with pytest.raises(ProtocolError, match="inventory"):
        provision_parsers()


def test_source_lines_are_physical_lf_not_unicode_separators():
    from gita.eval.reviewbench.common import source_lines
    assert source_lines("first\u2028still first\nsecond\n".encode()) == ["first\u2028still first", "second"]
    assert source_lines(b"") == []
