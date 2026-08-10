"""The dashboard must never show cost without quality beside it."""

from __future__ import annotations

import pytest

from gita.dashboard import load_results, render_dashboard

RUNS = [
    {"task": "t1", "arm": "git", "recall": 1.0, "prompt_tokens": 20000,
     "tool_tokens": 5000, "turns": 4, "used_gita": False},
    {"task": "t1", "arm": "gita", "recall": 1.0, "prompt_tokens": 9000,
     "tool_tokens": 600, "turns": 3, "used_gita": True},
    {"task": "control", "arm": "git", "recall": 1.0, "prompt_tokens": 1000,
     "tool_tokens": 200, "turns": 2, "used_gita": False, "expect_no_benefit": True},
    {"task": "control", "arm": "gita", "recall": 1.0, "prompt_tokens": 3000,
     "tool_tokens": 400, "turns": 5, "used_gita": True, "expect_no_benefit": True},
]

EVENTS = [
    {"session": "s1", "arm": "git", "tool": "git diff", "output_tokens": 4000,
     "latency_ms": 40},
    {"session": "s2", "arm": "gita", "tool": "gita_diff", "output_tokens": 400,
     "latency_ms": 900, "layer": "L1"},
]


class TestRendering:
    def test_produces_a_standalone_document(self):
        page = render_dashboard(RUNS, EVENTS)
        assert page.startswith("<!doctype html>")
        assert "</html>" in page

    def test_has_no_external_requests(self):
        page = render_dashboard(RUNS, EVENTS)
        for marker in ("http://", "https://", "src=", "cdn"):
            assert marker not in page, f"dashboard must work offline: found {marker}"

    def test_shows_quality_next_to_cost(self):
        page = render_dashboard(RUNS, EVENTS)
        assert "Quality delta" in page
        assert "recall" in page

    def test_marks_control_tasks(self):
        assert 'class="control"' in render_dashboard(RUNS, EVENTS)

    def test_includes_caveats(self):
        page = render_dashboard(RUNS, EVENTS).lower()
        assert "control" in page
        assert "directional" in page

    def test_reports_adoption(self):
        assert "adoption" in render_dashboard(RUNS, EVENTS).lower()

    def test_survives_empty_input(self):
        page = render_dashboard([], [])
        assert "<!doctype html>" in page

    def test_escapes_task_names(self):
        runs = [dict(RUNS[0], task="<script>x</script>"),
                dict(RUNS[1], task="<script>x</script>")]
        assert "<script>x</script>" not in render_dashboard(runs, [])


class TestLoading:
    def test_missing_directory_is_empty(self, tmp_path):
        results, events = load_results(tmp_path / "nope")
        assert results == [] and events == []

    def test_reads_results_and_nested_telemetry(self, tmp_path):
        (tmp_path / "results.jsonl").write_text(
            '{"task":"t","arm":"git","recall":1.0,"prompt_tokens":10}\n',
            encoding="utf8")
        run = tmp_path / "t__git__r1"
        run.mkdir()
        (run / "telemetry.jsonl").write_text(
            '{"arm":"git","tool":"git diff","output_tokens":5,"session":"s"}\n',
            encoding="utf8")

        results, events = load_results(tmp_path)
        assert len(results) == 1 and len(events) == 1

    def test_skips_corrupt_lines(self, tmp_path):
        (tmp_path / "results.jsonl").write_text(
            '{"task":"a","arm":"git"}\nnot json\n', encoding="utf8")
        assert len(load_results(tmp_path)[0]) == 1
