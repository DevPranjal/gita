from __future__ import annotations

import json
import subprocess

import pytest

from gita.eval.paired.common import MODEL, final_json, parse_usage, validate_findings
from gita.eval.paired.common import append_event, digest, read_events, write_json
from gita.eval.paired.dataset import freeze, resolve_scope
from gita.eval.paired.evidence import Evidence, checked_path, page_text
from gita.eval.paired.runner import cli_command, cli_environment, run_pair
from gita.eval.paired.judge import (
    blind_input, close_lines, judge_comparisons, maximum_matching, score, validate_judgment,
)
from gita.eval.paired.grade_recovery import recover_grades
from gita.eval.paired.report import aggregate, collect, intervals, verify_grade
from gita.eval.paired.reconcile import execution_events, reconcile_arm, review_content
from gita.eval.paired.export import check_export_text, usage_projection
from gita.eval.paired.sensitivity import environment_sensitivity


def usage_payload():
    usage = {"inputTokens": 1000, "outputTokens": 30, "cacheReadTokens": 600,
             "cacheWriteTokens": 300, "reasoningTokens": 10}
    return {"modelMetrics": {MODEL: {"usage": usage, "requests": {"count": 2}}},
            "currentModel": MODEL, "agentMetrics": {"main": {}},
            "tokenDetails": {k: {"tokenCount": v} for k, v in
                             {"input": 100, "output": 30, "cache_read": 600,
                              "cache_write": 300}.items()},
            "totalNanoAiu": 274000000, "totalApiDurationMs": 1200}


def test_usage_counts_one_aggregate_and_separates_cache(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text(json.dumps(usage_payload()), encoding="utf8")
    actual = parse_usage(path)
    assert actual["input_tokens"] == 1000
    assert actual["total_tokens"] == 1030
    assert actual["uncached_input_tokens"] == 100
    assert actual["estimated_credits"] == pytest.approx(.274)


@pytest.mark.parametrize("mutation", ["missing", "wrong_model", "negative", "double_cache"])
def test_invalid_telemetry_never_becomes_zero(tmp_path, mutation):
    value = usage_payload()
    if mutation == "missing":
        del value["modelMetrics"]
    elif mutation == "wrong_model":
        value["currentModel"] = "other"
    elif mutation == "negative":
        value["modelMetrics"][MODEL]["usage"]["outputTokens"] = -1
    else:
        value["modelMetrics"][MODEL]["usage"]["cacheWriteTokens"] = 1000
    path = tmp_path / "usage.json"
    path.write_text(json.dumps(value), encoding="utf8")
    with pytest.raises((ValueError, KeyError)):
        parse_usage(path)


def test_missing_usage_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_usage(tmp_path / "missing.json")


def test_explicit_zero_output_does_not_erase_charged_input(tmp_path):
    raw = usage_payload()
    raw["modelMetrics"][MODEL]["usage"]["outputTokens"] = 0
    raw["tokenDetails"]["output"]["tokenCount"] = 0
    write_json(tmp_path / "usage.json", raw)
    result = parse_usage(tmp_path / "usage.json")
    assert result["input_tokens"] == 1000
    assert result["output_tokens"] == 0


@pytest.mark.parametrize("completed", [True, False])
def test_resume_never_retries_a_terminal_pair(tmp_path, monkeypatch, completed):
    task = {"id": "t"}
    if completed:
        expected = [{"task": "t", "arm": a, "status": "ok"} for a in ("baseline", "gita")]
        for result in expected:
            write_json(tmp_path / "t" / result["arm"] / "result.json", result)
    else:
        expected = [{"task": "t", "status": "preparation_failed", "error": "unavailable"}]
        write_json(tmp_path / "t" / "preparation_failure.json", expected[0])
    def forbidden(*args):
        raise AssertionError("Terminal attempts must not query repositories or run models again")
    monkeypatch.setattr("gita.eval.paired.runner.prepare", forbidden)
    assert run_pair(task, tmp_path, tmp_path / "repos", 900) == expected


def test_final_answer_not_thinking_or_intermediate_message():
    events = [
        {"type": "assistant.message", "data": {"phase": "commentary", "content": "{}"}},
        {"type": "assistant.message", "data": {"phase": "final_answer",
                                              "content": '{"findings":[]}', "toolRequests": []}},
    ]
    assert final_json(events) == {"findings": []}
    assert validate_findings(final_json(events)) == []
    with pytest.raises(ValueError):
        final_json(events[:1])


def test_schema_rejects_inverted_lines():
    with pytest.raises(ValueError):
        validate_findings({"findings": [{"path": "a.py", "title": "t", "body": "b",
                                        "category": "Code Defect", "side": "right",
                                        "start_line": 9, "end_line": 2}]})


@pytest.mark.parametrize("path", ["../truth.json", "/etc/passwd", "C:\\secret", ".git/config",
                                  "a/../../b", "HEAD:foo", "a\nb", "..\\secret"])
def test_paths_cannot_escape_fixed_repository(path):
    with pytest.raises(ValueError):
        checked_path(path)


def test_native_windows_paths_and_git_paths_are_equivalent():
    assert checked_path("src\\module\\file.py") == checked_path("src/module/file.py")


def test_parallel_large_tool_events_are_not_interleaved(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda i: append_event(path, {"index": i, "text": "x" * 50000}), range(100)))
    events = read_events(path)
    assert sorted(e["index"] for e in events) == list(range(100))
    assert all(len(e["text"]) == 50000 for e in events)


def test_pages_have_explicit_continuations():
    text = "hello world\n" * 4000
    first = page_text(text)
    assert first["next_offset"] == 6000
    assert first["truncated"]
    second = page_text(text, first["next_offset"])
    assert second["offset"] == 6000
    with pytest.raises(ValueError):
        page_text(text, -1)


def test_freeze_is_deterministic_disjoint_and_complete(tmp_path):
    records = [{"githubPrUrl": f"https://github.com/owner/repo/pull/{i}",
                "source_commit": "a" * 40, "target_commit": f"{i:040x}",
                "project_main_language": lang, "change_line_count": 10,
                "comments": [{"note": "hidden answer"}]}
               for i, lang in enumerate(["Python", "Python", "Go", "Go"], 1)]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(records), encoding="utf8")
    manifest = freeze(source, tmp_path / "manifest.json")
    assert len(manifest["tasks"]) == 4
    assert sum(t["split"] == "pilot" for t in manifest["tasks"]) == 2
    assert "hidden answer" not in json.dumps(manifest)
    assert manifest == freeze(source, tmp_path / "manifest.json")


def test_fresh_home_no_inherited_model_or_plugins(monkeypatch, tmp_path):
    monkeypatch.setenv("COPILOT_MODEL", "other")
    monkeypatch.setenv("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "sensitive")
    env = cli_environment(tmp_path)
    assert env["COPILOT_HOME"] == str(tmp_path)
    assert "COPILOT_MODEL" not in env
    assert "COPILOT_CUSTOM_INSTRUCTIONS_DIRS" not in env


def test_commands_explicitly_restrict_tools(tmp_path, monkeypatch):
    monkeypatch.setattr("gita.eval.paired.runner.shutil.which", lambda name: "copilot.exe")
    command = cli_command(tmp_path, "same prompt", ["review-raw_diff"])
    assert command[command.index("--available-tools") + 1] == "review-raw_diff"
    assert "--disable-builtin-mcps" in command
    assert "--no-custom-instructions" in command
    assert command[-1] == "same prompt"


def test_divergent_source_uses_pinned_merge_base_and_never_current_pr_head(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=("c" * 40 + "\n").encode(), stderr=b"")
    monkeypatch.setattr("gita.eval.paired.dataset.subprocess.run", run)
    task = {"repo": "owner/repo", "base": "a" * 40, "head": "b" * 40}
    scope = resolve_scope(task, tmp_path)
    assert scope["base"] == "c" * 40
    assert scope["supplied_base"] == "a" * 40
    assert scope["head"] == "b" * 40
    assert scope["base_adjusted"]
    assert f"repos/owner/repo/compare/{'a' * 40}...{'b' * 40}" in calls[0]
    assert resolve_scope(task, tmp_path) == scope
    assert len(calls) == 1


def test_unavailable_revision_has_no_success_shaped_fallback(tmp_path, monkeypatch):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"404")
    monkeypatch.setattr("gita.eval.paired.dataset.subprocess.run", run)
    with pytest.raises(RuntimeError, match="Cannot resolve"):
        resolve_scope({"repo": "owner/repo", "base": "a" * 40, "head": "b" * 40}, tmp_path)
    assert not (tmp_path / "scope.json").exists()


@pytest.fixture
def evidence_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def command(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()
    command("init")
    command("config", "user.name", "Eval Test")
    command("config", "user.email", "eval-test@example.invalid")
    (repo / "sample.py").write_bytes(b"def divide(a, b):\n    return a / b\n")
    command("add", "sample.py")
    command("commit", "-m", "base")
    base = command("rev-parse", "HEAD")
    (repo / "sample.py").write_bytes(b"def divide(a, b):\n    return a // b\n")
    command("add", "sample.py")
    command("commit", "-m", "head")
    return {"repo": str(repo), "base": base, "head": command("rev-parse", "HEAD"),
            "arm": "baseline", "tool_log": str(tmp_path / "tools.jsonl"), "max_calls": 40}


def test_both_arms_get_identical_raw_evidence_and_baseline_cannot_call_gita(evidence_config):
    baseline = Evidence(evidence_config)
    treatment = Evidence({**evidence_config, "arm": "gita"})
    assert baseline.raw_diff() == treatment.raw_diff()
    assert "return a // b" in baseline.read_file("sample.py")["text"]
    assert "error" in baseline.execute("gita_diff", {})
    assert "error" not in treatment.execute("gita_diff", {})
    assert "error" in baseline.execute("read_file", {"path": "../labels.json"})


def test_evidence_budget_is_explicit(evidence_config):
    evidence = Evidence({**evidence_config, "max_calls": 1})
    assert "error" not in evidence.execute("list_files", {})
    assert "limit reached" in evidence.execute("list_files", {})["error"]


def test_maximum_matching_does_not_greedily_lose_a_match():
    assert len(maximum_matching([(0, 0), (0, 1), (1, 0)])) == 2
    assert len(maximum_matching([(0, 0), (1, 0), (2, 0)])) == 1


def test_blind_grading_and_localization_are_separate():
    refs = [{"path": "x.py", "side": "right", "from_line": 8, "to_line": 6,
             "note": "Division by zero", "category": "Code Defect"}]
    finding = {"path": "x.py", "side": "right", "start_line": 90, "end_line": 91,
               "title": "Divide by zero", "body": "Zero divisor crashes", "category": "Code Defect"}
    payload, mapping = blind_input("test", refs, {"baseline": {"findings": [finding]},
                                                "gita": {"findings": [finding]}})
    assert "baseline" not in json.dumps(payload)
    assert "gita" not in json.dumps(payload)
    assert payload["references"][0]["start_line"] == 6
    comparisons = [{**p, "verdict": "same", "reason": "Same cause"} for p in payload["pairs"]]
    validate_judgment({"comparisons": comparisons}, payload)
    scores = score(payload, mapping, comparisons, ["baseline", "gita"])
    assert scores["baseline"]["recall"] == 0
    assert scores["baseline"]["semantic_recall"] == 1
    assert scores["gita"] == scores["baseline"]


def test_judge_must_cover_every_pair_exactly_once():
    payload = {"pairs": [{"candidate": "c0", "reference": "r0"}]}
    row = {"candidate": "c0", "reference": "r0", "verdict": "same", "reason": "match"}
    for comparisons in ([], [row, row], [{**row, "verdict": "maybe"}],
                        [None], [{**row, "candidate": []}]):
        with pytest.raises(ValueError):
            validate_judgment({"comparisons": comparisons}, payload)


def test_native_path_format_does_not_lose_a_correct_finding():
    refs = [{"path": "src/x.py", "side": "right", "from_line": 6, "to_line": 6,
             "note": "x", "category": "Code Defect"}]
    finding = {"path": "src\\x.py", "side": "right", "start_line": 6, "end_line": 6,
               "title": "x", "body": "x", "category": "Code Defect"}
    payload, _ = blind_input("test", refs, {"baseline": {"findings": [finding]}})
    assert payload["pairs"] == [{"candidate": "c0", "reference": "r0"}]


def test_missing_unscored_category_is_not_missing_review_content():
    finding = {"path": "x.py", "title": "bug", "body": "actual cause", "side": "right",
               "start_line": 1, "end_line": 1}
    assert review_content({"findings": [finding]}) == [finding]
    assert "category" not in review_content({"findings": [finding]})[0]
    with pytest.raises(ValueError):
        validate_findings({"findings": [finding]})
    with pytest.raises(ValueError):
        review_content({"findings": [{**finding, "body": ""}]})


def test_reconciliation_retains_failed_format_cost_without_overwriting_raw(tmp_path, monkeypatch):
    original = {"task": "x", "arm": "baseline", "status": "invalid", "wall_seconds": 10,
                "error": "missing category"}
    raw = json.dumps(original)
    (tmp_path / "result.json").write_text(raw, encoding="utf8")
    answer = {"findings": [{"path": "x.py", "title": "bug", "body": "actual cause",
                            "side": "right", "start_line": 1, "end_line": 1}]}
    events = [{"type": "assistant.message", "data": {
        "phase": "final_answer", "content": json.dumps(answer), "toolRequests": []}}]
    monkeypatch.setattr("gita.eval.paired.reconcile.read_events", lambda path: events)
    monkeypatch.setattr("gita.eval.paired.reconcile.execution_events",
                        lambda path: (events, {"event_source": "events.jsonl"}))
    monkeypatch.setattr("gita.eval.paired.reconcile.parse_usage",
                        lambda path: {"input_tokens": 123, "output_tokens": 10})
    monkeypatch.setattr("gita.eval.paired.reconcile.audit_tools",
                        lambda *args: {"tool_calls": 1})
    result = reconcile_arm(tmp_path)
    assert result["status"] == "measured"
    assert result["input_tokens"] == 123
    assert result["content_valid"] and not result["format_valid"]
    assert result["format_error"]
    assert (tmp_path / "result.json").read_text(encoding="utf8") == raw
    assert json.loads((tmp_path / "content.json").read_text()) == answer


def test_line_tolerance_is_five_not_a_file_wide_match():
    ref = {"side": "right", "start_line": 10, "end_line": 10}
    assert close_lines({"side": "right", "start_line": 15, "end_line": 15}, ref)
    assert not close_lines({"side": "right", "start_line": 16, "end_line": 16}, ref)
    assert not close_lines({"side": "left", "start_line": 10, "end_line": 10}, ref)


def paired_result(repo, cost, recall):
    run = {"input_tokens": cost, "total_tokens": cost + 10, "output_tokens": 10,
           "reported_credits": cost / 100, "wall_seconds": cost / 10,
           "uncached_input_tokens": cost, "cache_read_tokens": 0, "cache_write_tokens": 0,
           "tool_calls": 1, "gita_calls": 0, "tool_errors": 0, "model_requests": 1,
           "tool_output_tokens_cl100k": cost}
    quality = {"references": 10, "generated": 10, "matched": int(recall * 10),
               "recall": recall, "semantic_recall": recall, "defect_recall": recall,
               "any_reference_detected": recall > 0, "all_references_detected": recall == 1}
    return {"repo": repo, "baseline": run, "gita": {**run, "input_tokens": cost / 2},
            "scores": {"baseline": quality, "gita": quality}}


def test_stats_keep_cache_misses_and_all_pairs():
    pairs = [paired_result("one", 100, .5), paired_result("two", 1000, .5)]
    pairs[1]["baseline"]["cache_write_tokens"] = 900
    result = aggregate(pairs, bootstrap=False)
    assert result["pairs"] == 2
    assert result["arms"]["baseline"]["totals"]["input_tokens"] == 1100
    assert result["reductions"]["input_tokens"] == .5
    assert result["quality_deltas"]["recall"] == 0


def test_one_repository_cannot_provide_a_cluster_interval():
    pairs = [paired_result("same", 100, .5), paired_result("same", 200, .5)]
    assert intervals(pairs, samples=100) == {}


def test_constant_effect_bootstrap_is_reproducible():
    pairs = [paired_result("one", 100, .5), paired_result("two", 200, .5)]
    result = intervals(pairs, samples=100)
    assert result["input_tokens"] == [.5, .5]
    assert result["recall_delta"] == [0, 0]


def test_equal_zero_quality_is_not_a_successful_noninferiority_claim():
    result = aggregate([paired_result("one", 100, 0), paired_result("two", 200, 0)])
    assert result["quality_floor_warning"]
    assert not result["recall_noninferiority_supported"]


def test_report_rejects_duplicate_attempts_instead_of_selecting_wins(tmp_path):
    manifest = {"manifest_digest": "x", "tasks": [{"id": "t", "repo": "r"}]}
    root = tmp_path / "run"
    (root / "t" / "baseline").mkdir(parents=True)
    (root / "experiment.json").write_text(
        json.dumps({"manifest_digest": "x", "tasks": ["t"]}), encoding="utf8")
    (root / "t" / "baseline" / "result.json").write_text('{"status":"ok"}', encoding="utf8")
    with pytest.raises(ValueError, match="Duplicate"):
        collect(manifest, [root, root], [])


def test_report_binds_grades_to_actual_answers(tmp_path):
    refs = [{"path": "x.py", "side": "right", "from_line": 1, "to_line": 1,
             "note": "division by zero", "category": "Code Defect"}]
    finding = {"path": "x.py", "side": "right", "start_line": 1, "end_line": 1,
               "title": "bug", "body": "division by zero", "category": "Code Defect"}
    answers = {a: {"findings": [finding]} for a in ("baseline", "gita")}
    payload, mapping = blind_input("task", refs, answers)
    comparisons = [{**p, "verdict": "same", "reason": "same cause"} for p in payload["pairs"]]
    grade = {"reference_digest": digest(refs), "input_digest": digest(payload),
             "scores": score(payload, mapping, comparisons, list(answers))}
    directory = tmp_path / "grade"
    write_json(directory / "input.json", payload)
    write_json(directory / "mapping.json", mapping)
    write_json(directory / "comparisons.json", comparisons)
    write_json(directory / "grade.json", grade)
    paths = {a: tmp_path / a for a in answers}
    for arm, answer in answers.items():
        write_json(paths[arm] / "answer.json", answer)
    loaded_grade = json.loads((directory / "grade.json").read_text())
    verify_grade({"reference_digest": digest(refs)}, loaded_grade, directory, paths, False)
    write_json(paths["baseline"] / "answer.json",
               {"findings": [{**finding, "body": "A different issue"}]})
    with pytest.raises(ValueError, match="different reviewer answer"):
        verify_grade({"reference_digest": digest(refs)}, loaded_grade, directory, paths, False)


def test_evidence_export_rejects_credential_patterns():
    check_export_text("Only public repository paths and numeric token counts.")
    with pytest.raises(ValueError, match="credential"):
        check_export_text("ghp_" + "a" * 40)


def test_usage_export_does_not_copy_unknown_fields():
    raw = usage_payload()
    raw.update(totalPremiumRequestCost=1, totalUserRequests=1,
               sessionStartTime="2026-09-18T00:00:00Z",
               lastCallInputTokens=1000, lastCallOutputTokens=30,
               private_access_token="do-not-export")
    raw["modelMetrics"][MODEL].update(totalNanoAiu=274000000,
                                      tokenDetails=raw["tokenDetails"], apiCallId="private-id")
    exported = usage_projection(raw)
    assert exported["modelMetrics"][MODEL]["usage"]["inputTokens"] == 1000
    assert "private_access_token" not in exported
    assert "apiCallId" not in exported["modelMetrics"][MODEL]


def test_sleep_sensitivity_does_not_replace_the_primary_population():
    before = paired_result("one", 100, .5)
    interrupted = paired_result("two", 200, .5)
    after = paired_result("three", 300, .5)
    for task_id, pair, date in (("a", before, "2026-09-18"), ("b", interrupted, "2026-09-18"),
                                ("c", after, "2026-09-19")):
        pair["task"] = task_id
        for arm in ("baseline", "gita"):
            pair[arm] = {**pair[arm], "started": f"{date}T12:00:00+00:00",
                         "finished": f"{date}T12:01:00+00:00"}
    report = {"pairs": [before, interrupted, after]}
    result = environment_sensitivity(report, {"wake_time_utc": "2026-09-19T05:20:15Z",
                                             "affected_arms": [{"task": "b", "arm": "gita"}]})
    assert result["primary_all"]["pairs"] == 3
    assert result["without_interrupted_pairs"]["pairs"] == 2
    assert result["completed_before_resume"]["pairs"] == 1
    assert result["started_after_resume"]["pairs"] == 1
    assert len(report["pairs"]) == 3


def test_corrupt_presentation_stream_uses_only_verified_native_journal(tmp_path):
    terminal = {"type": "result", "exitCode": 0, "sessionId": "session-1",
                "usage": {"sessionDurationMs": 1234}}
    (tmp_path / "events.jsonl").write_text(
        '{"broken"\n' + json.dumps(terminal) + "\n", encoding="utf8")
    journal = tmp_path / "home" / "session-state" / "session-1" / "events.jsonl"
    journal.parent.mkdir(parents=True)
    event = {"type": "assistant.message", "data": {"phase": "final_answer",
                                                 "content": '{"findings":[]}', "toolRequests": []}}
    journal.write_text(json.dumps(event) + "\n", encoding="utf8")
    events, metadata = execution_events(tmp_path)
    assert final_json(events) == {"findings": []}
    assert metadata["cli_session_seconds"] == 1.234
    assert metadata["stream_parse_error"]
    assert "wall_seconds" not in metadata
    terminal["sessionId"] = "different-session"
    (tmp_path / "events.jsonl").write_text('{"broken"\n' + json.dumps(terminal), encoding="utf8")
    with pytest.raises(ValueError, match="uniquely verified"):
        execution_events(tmp_path)


def test_missing_time_is_not_zero_and_does_not_discard_valid_token_pairs():
    pairs = [paired_result("one", 100, .5), paired_result("two", 200, .5)]
    pairs[0]["gita"]["wall_seconds"] = None
    result = aggregate(pairs, bootstrap=False)
    assert result["pairs"] == 2
    assert result["metric_pair_counts"]["input_tokens"] == 2
    assert result["metric_pair_counts"]["wall_seconds"] == 1
    assert result["arms"]["baseline"]["means"]["wall_seconds"] == 20
    assert result["reductions"]["input_tokens"] == .5


def test_jsonl_does_not_split_unicode_inside_valid_strings(tmp_path):
    path = tmp_path / "events.jsonl"
    event = {"text": "left\u2028right\u0085last"}
    path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf8")
    assert read_events(path) == [event]


def test_judge_audit_cannot_succeed_with_missing_inventory():
    with pytest.raises(ValueError, match="Missing judge"):
        judge_comparisons([{"type": "session.usage_checkpoint", "data": {}}], {"pairs": []})


def test_recover_existing_judge_without_another_model_call(tmp_path):
    source, output = tmp_path / "source", tmp_path / "recovered"
    refs = [{"path": "x.py", "side": "right", "from_line": 1, "to_line": 1,
             "note": "bug", "category": "Code Defect"}]
    finding = {"path": "x.py", "side": "right", "start_line": 1, "end_line": 1,
               "title": "bug", "body": "cause", "category": "Code Defect"}
    payload, mapping = blind_input("t", refs, {a: {"findings": [finding]}
                                             for a in ("baseline", "gita")})
    prior = source / "t"
    write_json(prior / "input.json", payload)
    write_json(prior / "mapping.json", mapping)
    original = {"task": "t", "status": "invalid", "error": "JSONDecodeError: presentation",
                "reference_digest": digest(refs), "input_digest": digest(payload)}
    write_json(prior / "grade.json", original)
    call = prior / "call"
    write_json(call / "usage.json", usage_payload())
    terminal = {"type": "result", "exitCode": 0, "sessionId": "j1",
                "usage": {"sessionDurationMs": 1000}}
    (call / "events.jsonl").write_text('{"broken"\n' + json.dumps(terminal), encoding="utf8")
    journal = call / "home" / "session-state" / "j1" / "events.jsonl"
    journal.parent.mkdir(parents=True)
    comparisons = [{**p, "verdict": "same", "reason": "same concern"} for p in payload["pairs"]]
    events = [
        {"type": "assistant.message", "data": {"phase": "final_answer", "toolRequests": [],
                                              "content": json.dumps({"comparisons": comparisons})}},
        {"type": "session.usage_checkpoint", "data": {"promptCacheBreakState": [
            {"models": {MODEL: {"tool_count": 0}}}]}},
    ]
    journal.write_text("\n".join(json.dumps(e) for e in events), encoding="utf8")
    result = recover_grades(source, output)
    assert result["new_model_calls"] == 0
    assert result["recovered_grades"] == ["t"]
    assert json.loads((prior / "grade.json").read_text()) == original
    recovered = json.loads((output / "t" / "grade.json").read_text())
    assert recovered["status"] == "ok"
    assert recovered["scores"]["baseline"]["matched"] == 1
