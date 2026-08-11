"""Eval harness: task specs, real-token log parsing, scoring, run construction.

The model call itself is not tested here -- everything around it is, so a failed
experiment can be distinguished from a failed harness.
"""

from __future__ import annotations

import json

import pytest

from gita.eval.logs import parse_usage
from gita.eval.runner import ArmConfig, build_command, build_env
from gita.eval.score import recall, score_run, summarise_runs
from gita.eval.spec import Task, load_tasks

MANIFEST = """
- id: flask-teardown-review
  repo: flask
  base: fbb6f0bc4c^
  head: fbb6f0bc4c
  category: review
  prompt: |
    Summarise what this commit changes, for a reviewer.
  must_mention:
    - _CollectErrors
    - do_teardown_request
- id: flask-api-break
  repo: flask
  base: HEAD^
  head: HEAD
  category: interface
  prompt: Does this commit change the public API?
  must_mention: [Flask]
"""


class TestTaskSpec:
    def test_loads_a_manifest(self, tmp_path):
        path = tmp_path / "tasks.yaml"
        path.write_text(MANIFEST, encoding="utf8")
        tasks = load_tasks(path)
        assert len(tasks) == 2
        assert tasks[0].id == "flask-teardown-review"
        assert tasks[0].must_mention == ["_CollectErrors", "do_teardown_request"]

    def test_prompt_is_preserved_verbatim(self, tmp_path):
        path = tmp_path / "tasks.yaml"
        path.write_text(MANIFEST, encoding="utf8")
        assert "Summarise" in load_tasks(path)[0].prompt

    def test_rejects_a_task_without_ground_truth(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("- id: x\n  repo: r\n  prompt: p\n", encoding="utf8")
        with pytest.raises(ValueError, match="must_mention"):
            load_tasks(path)

    def test_rejects_duplicate_ids(self, tmp_path):
        path = tmp_path / "dupe.yaml"
        path.write_text(
            "- {id: x, repo: r, prompt: p, must_mention: [a]}\n"
            "- {id: x, repo: r, prompt: p, must_mention: [b]}\n", encoding="utf8")
        with pytest.raises(ValueError, match="duplicate"):
            load_tasks(path)


LOG = """
2026-08-10 info request start
{
  "choices": [{"finish_reason": "tool_calls"}],
  "usage": {
    "prompt_tokens": 14000,
    "completion_tokens": 90,
    "total_tokens": 14090,
    "prompt_tokens_details": {"cached_tokens": 0, "cache_creation_tokens": 13998}
  }
}
2026-08-10 info second request
{
  "usage": {
    "prompt_tokens": 15200,
    "completion_tokens": 300,
    "total_tokens": 15500,
    "prompt_tokens_details": {"cached_tokens": 14000, "cache_creation_tokens": 0}
  }
}
"""


class TestUsageParsing:
    def test_counts_turns(self, tmp_path):
        (tmp_path / "a.log").write_text(LOG, encoding="utf8")
        assert parse_usage(tmp_path)["turns"] == 2

    def test_sums_real_tokens(self, tmp_path):
        (tmp_path / "a.log").write_text(LOG, encoding="utf8")
        usage = parse_usage(tmp_path)
        assert usage["prompt_tokens"] == 29200
        assert usage["completion_tokens"] == 390

    def test_tracks_cache_hits(self, tmp_path):
        (tmp_path / "a.log").write_text(LOG, encoding="utf8")
        assert parse_usage(tmp_path)["cached_tokens"] == 14000

    def test_peak_context_is_the_largest_prompt(self, tmp_path):
        (tmp_path / "a.log").write_text(LOG, encoding="utf8")
        assert parse_usage(tmp_path)["peak_prompt_tokens"] == 15200

    def test_missing_directory_is_empty_not_fatal(self, tmp_path):
        assert parse_usage(tmp_path / "nope")["turns"] == 0

    def test_reads_every_log_file_in_the_directory(self, tmp_path):
        (tmp_path / "a.log").write_text(LOG, encoding="utf8")
        (tmp_path / "b.log").write_text(LOG, encoding="utf8")
        assert parse_usage(tmp_path)["turns"] == 4


class TestScoring:
    def test_recall_is_case_insensitive(self):
        assert recall("Touches _CollectErrors and more", ["_collecterrors"]) == 1.0

    def test_partial_recall(self):
        assert recall("only _CollectErrors here", ["_CollectErrors", "AppContext"]) == 0.5

    def test_no_ground_truth_scores_none(self):
        assert recall("anything", []) is None

    def test_score_run_combines_quality_and_cost(self):
        result = score_run(
            answer="changed _CollectErrors and do_teardown_request",
            must_mention=["_CollectErrors", "do_teardown_request"],
            usage={"prompt_tokens": 1000, "completion_tokens": 50, "turns": 2,
                   "cached_tokens": 0, "peak_prompt_tokens": 1000},
            events=[{"arm": "gita", "tool": "gita_diff", "output_tokens": 300,
                     "layer": "L1", "session": "s"}],
        )
        assert result["recall"] == 1.0
        assert result["tool_tokens"] == 300
        assert result["prompt_tokens"] == 1000
        assert result["used_gita"] is True

    def test_adoption_is_detected_from_telemetry_not_the_prompt(self):
        result = score_run("x", ["y"], {"turns": 1}, [
            {"arm": "git", "tool": "git diff", "output_tokens": 900, "session": "s"},
        ])
        assert result["used_gita"] is False
        assert result["tool_tokens"] == 900


class TestAggregation:
    def runs(self):
        return [
            {"task": "t1", "arm": "git", "recall": 1.0, "prompt_tokens": 20000,
             "tool_tokens": 5000, "turns": 4, "used_gita": False},
            {"task": "t1", "arm": "gita", "recall": 1.0, "prompt_tokens": 9000,
             "tool_tokens": 600, "turns": 3, "used_gita": True},
            {"task": "t2", "arm": "git", "recall": 0.5, "prompt_tokens": 10000,
             "tool_tokens": 3000, "turns": 3, "used_gita": False},
            {"task": "t2", "arm": "gita", "recall": 1.0, "prompt_tokens": 6000,
             "tool_tokens": 400, "turns": 2, "used_gita": True},
        ]

    def test_reports_per_arm_means(self):
        report = summarise_runs(self.runs())
        assert report["by_arm"]["gita"]["mean_recall"] == 1.0
        assert report["by_arm"]["git"]["mean_recall"] == 0.75

    def test_paired_reduction_on_real_tokens(self):
        report = summarise_runs(self.runs())
        assert report["reduction"]["prompt_tokens"] == pytest.approx(1 - 15000 / 30000)

    def test_tokens_per_correct_answer_is_the_headline(self):
        report = summarise_runs(self.runs())
        assert report["by_arm"]["gita"]["tokens_per_correct_answer"] < \
               report["by_arm"]["git"]["tokens_per_correct_answer"]

    def test_adoption_rate_is_reported(self):
        assert summarise_runs(self.runs())["adoption_rate"] == 1.0

    def test_quality_regression_is_surfaced(self):
        runs = self.runs()
        runs[1]["recall"] = 0.0
        report = summarise_runs(runs)
        assert report["quality_delta"] < 0

    def test_empty_input_is_safe(self):
        assert summarise_runs([])["by_arm"] == {}


class TestRunConstruction:
    def test_prompt_is_identical_across_arms(self, tmp_path):
        task = Task(id="t", repo="r", prompt="Explain this commit.",
                    must_mention=["x"], base="HEAD^", head="HEAD", category="review")
        git_cmd = build_command(task, ArmConfig.git(), tmp_path, model="m")
        gita_cmd = build_command(task, ArmConfig.gita(), tmp_path, model="m")
        git_prompt = git_cmd[git_cmd.index("-p") + 1]
        assert task.prompt in git_prompt
        assert git_prompt == gita_cmd[gita_cmd.index("-p") + 1]

    def test_prompt_states_which_revisions_to_examine(self, tmp_path):
        task = Task(id="t", repo="r", prompt="Explain.", must_mention=["x"],
                    base="abc123^", head="abc123", category="review")
        cmd = build_command(task, ArmConfig.git(), tmp_path, model="m")
        assert "abc123" in cmd[cmd.index("-p") + 1]

    def test_no_arm_mentions_gita_in_the_prompt(self, tmp_path):
        task = Task(id="t", repo="r", prompt="Explain this commit.",
                    must_mention=["x"], base="HEAD^", head="HEAD", category="review")
        for arm in (ArmConfig.git(), ArmConfig.gita()):
            prompt = build_command(task, arm, tmp_path, model="m")
            assert "gita" not in prompt[prompt.index("-p") + 1].lower()

    def test_log_dir_is_per_run(self, tmp_path):
        task = Task(id="t", repo="r", prompt="p", must_mention=["x"],
                    base="HEAD^", head="HEAD", category="review")
        cmd = build_command(task, ArmConfig.git(), tmp_path, model="m")
        assert str(tmp_path) in cmd[cmd.index("--log-dir") + 1]

    def test_only_the_gita_arm_gets_gita_on_path(self, tmp_path):
        shim, gita_bin = tmp_path / "shim", tmp_path / "bin"
        git_env = build_env(ArmConfig.git(), "t", "run1", tmp_path, shim, gita_bin)
        gita_env = build_env(ArmConfig.gita(), "t", "run1", tmp_path, shim, gita_bin)
        assert str(gita_bin) in gita_env["PATH"]
        assert str(gita_bin) not in git_env["PATH"]

    def test_both_arms_get_the_git_shim(self, tmp_path):
        shim, gita_bin = tmp_path / "shim", tmp_path / "bin"
        for arm in (ArmConfig.git(), ArmConfig.gita()):
            env = build_env(arm, "t", "run1", tmp_path, shim, gita_bin)
            assert str(shim) in env["PATH"]

    def test_telemetry_is_tagged_with_task_arm_and_session(self, tmp_path):
        env = build_env(ArmConfig.gita(), "task-1", "run-9", tmp_path,
                        tmp_path / "s", tmp_path / "b")
        assert env["GITA_TASK"] == "task-1"
        assert env["GITA_SESSION"] == "run-9"
        assert env["GITA_ARM"] == "gita"
        assert env["GITA_TELEMETRY"].endswith("telemetry.jsonl")


class TestPromptCacheMissesAreNotResults:
    """A run that lost the prompt cache costs 2-3x, and says nothing about gita.

    Cache writes are priced 12.5x cache reads. A single miss adds ~60 credits --
    about double an entire normal task -- and misses land randomly. Worse, they
    land systematically: the first task of a sweep always starts cold, and the
    git arm runs first, so the noise was biased in gita's favour.
    """

    def runs(self, miss_arm: str | None = None) -> list[dict]:
        out = []
        for arm in ("git", "gita"):
            for rep in range(3):
                cold = miss_arm == arm and rep == 0
                out.append({
                    "task": "t", "arm": arm, "recall": 1.0, "used_gita": arm == "gita",
                    "prompt_tokens": 100_000, "tool_tokens": 100, "turns": 3,
                    "cached_tokens": 90_000,
                    "cache_creation_tokens": 120_000 if cold else 18_000,
                    "credits": 90.0 if cold else 30.0,
                })
        return out

    def test_a_cache_miss_is_flagged(self):
        summary = summarise_runs(self.runs(miss_arm="git"))
        assert summary["cache_misses"] == 1

    def test_a_clean_figure_is_reported_alongside_the_raw_one(self):
        summary = summarise_runs(self.runs(miss_arm="git"))
        assert summary["reduction"]["credits"] != summary["reduction"]["credits_cache_clean"]

    def test_the_clean_figure_ignores_the_miss(self):
        """Both arms cost the same; only a cache miss made git look worse."""
        summary = summarise_runs(self.runs(miss_arm="git"))
        assert abs(summary["reduction"]["credits_cache_clean"]) < 0.01

    def test_a_sweep_without_misses_agrees_with_itself(self):
        summary = summarise_runs(self.runs())
        assert summary["cache_misses"] == 0
        assert summary["reduction"]["credits"] == summary["reduction"]["credits_cache_clean"]
