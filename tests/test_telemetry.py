"""WS-8 telemetry: event capture and metric aggregation.

Written before the implementation. What is asserted here is what the dashboard
is allowed to claim.
"""

from __future__ import annotations

import json

import pytest

from gita.telemetry import load_events, record, summarise


def event(**kwargs) -> dict:
    base = {
        "session": "s1",
        "arm": "gita",
        "tool": "gita_diff",
        "output_tokens": 100,
        "latency_ms": 10,
        "ok": True,
    }
    base.update(kwargs)
    return base


class TestRecording:
    def test_writes_one_json_object_per_line(self, tmp_path):
        path = tmp_path / "t.jsonl"
        record(event(), path=path)
        record(event(tool="gita_show"), path=path)
        lines = path.read_text(encoding="utf8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["tool"] == "gita_show"

    def test_stamps_a_timestamp(self, tmp_path):
        path = tmp_path / "t.jsonl"
        record(event(), path=path)
        assert "ts" in load_events(path)[0]

    def test_creates_missing_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "t.jsonl"
        record(event(), path=path)
        assert path.exists()

    def test_never_raises_on_an_unwritable_path(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")
        record(event(), path=blocker / "t.jsonl")  # must not raise

    def test_disabled_when_no_path_is_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITA_TELEMETRY", raising=False)
        assert record(event()) is False

    def test_env_var_selects_the_sink(self, tmp_path, monkeypatch):
        path = tmp_path / "env.jsonl"
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        assert record(event()) is True
        assert len(load_events(path)) == 1


class TestLoading:
    def test_missing_file_is_empty(self, tmp_path):
        assert load_events(tmp_path / "nope.jsonl") == []

    def test_corrupt_lines_are_skipped(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"arm": "gita"}\nnot json\n{"arm": "git"}\n', encoding="utf8")
        assert len(load_events(path)) == 2


class TestSummary:
    def sample(self):
        return [
            event(session="s1", arm="git", tool="git diff", output_tokens=4000),
            event(session="s1", arm="git", tool="git show", output_tokens=2000),
            event(session="s2", arm="gita", tool="gita_diff", output_tokens=500, layer="L1"),
            event(session="s2", arm="gita", tool="gita_show", output_tokens=200, layer="L2"),
            event(session="s3", arm="gita", tool="gita_diff", output_tokens=30, layer="L0"),
        ]

    def test_counts_calls_and_sessions_per_arm(self):
        report = summarise(self.sample())
        assert report["by_arm"]["git"]["calls"] == 2
        assert report["by_arm"]["gita"]["sessions"] == 2

    def test_average_tokens_per_call(self):
        report = summarise(self.sample())
        assert report["by_arm"]["git"]["avg_tokens_per_call"] == 3000
        assert report["by_arm"]["gita"]["avg_tokens_per_call"] == pytest.approx(243.33, abs=0.1)

    def test_average_tokens_per_session(self):
        report = summarise(self.sample())
        assert report["by_arm"]["git"]["avg_tokens_per_session"] == 6000
        assert report["by_arm"]["gita"]["avg_tokens_per_session"] == 365

    def test_reports_savings_against_the_git_arm(self):
        savings = summarise(self.sample())["savings"]
        assert 0.9 < savings["per_call"] < 0.95
        assert 0.9 < savings["per_session"] < 0.95

    def test_drill_depth_distribution(self):
        depth = summarise(self.sample())["drill_depth"]
        assert depth["L0"] == pytest.approx(0.5)
        assert depth["L2"] == pytest.approx(0.5)

    def test_deepest_layer_per_session_is_what_counts(self):
        events = [
            event(session="a", layer="L0"),
            event(session="a", layer="L1"),
            event(session="a", layer="L2"),
        ]
        assert summarise(events)["drill_depth"]["L2"] == 1.0

    def test_empty_input_is_safe(self):
        report = summarise([])
        assert report["calls"] == 0
        assert report["savings"]["per_call"] is None

    def test_single_arm_reports_no_savings(self):
        report = summarise([event(arm="gita")])
        assert report["savings"]["per_call"] is None

    def test_failed_calls_are_counted_separately(self):
        report = summarise([event(ok=False), event(ok=True)])
        assert report["errors"] == 1


class TestPairedTasks:
    """The honest comparison: the same task in both arms."""

    def paired(self):
        return [
            event(task="t1", session="git-1", arm="git", output_tokens=4000),
            event(task="t1", session="gita-1", arm="gita", output_tokens=400),
            event(task="t2", session="git-2", arm="git", output_tokens=2000),
            event(task="t2", session="gita-2", arm="gita", output_tokens=500),
            event(task="t3", session="git-3", arm="git", output_tokens=1000),
        ]

    def test_pairs_only_tasks_present_in_both_arms(self):
        tasks = summarise(self.paired())["tasks"]
        assert {t["task"] for t in tasks} == {"t1", "t2"}

    def test_reports_per_task_reduction(self):
        tasks = {t["task"]: t for t in summarise(self.paired())["tasks"]}
        assert tasks["t1"]["reduction"] == pytest.approx(0.9)
        assert tasks["t2"]["reduction"] == pytest.approx(0.75)

    def test_paired_reduction_is_the_headline(self):
        report = summarise(self.paired())
        assert report["savings"]["paired"] == pytest.approx(1 - 900 / 6000)


class TestGitShim:
    """The baseline arm must be measured the same way as the treatment arm."""

    def test_forwards_output_verbatim(self, repo, tmp_path, monkeypatch, capsys):
        from gita.telemetry import shim

        monkeypatch.setenv("GITA_TELEMETRY", str(tmp_path / "t.jsonl"))
        code = shim.main(["-C", str(repo.root), "log", "--oneline"])
        captured = capsys.readouterr()
        assert code == 0
        assert "second" in captured.out

    def test_records_the_call(self, repo, tmp_path, monkeypatch, capsys):
        from gita.telemetry import shim

        path = tmp_path / "t.jsonl"
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        shim.main(["-C", str(repo.root), "diff", "HEAD^", "HEAD"])
        capsys.readouterr()

        events = load_events(path)
        assert len(events) == 1
        assert events[0]["arm"] == "git"
        assert events[0]["tool"] == "git diff"
        assert events[0]["output_tokens"] > 0

    def test_propagates_a_failing_exit_code(self, repo, tmp_path, monkeypatch, capsys):
        from gita.telemetry import shim

        monkeypatch.setenv("GITA_TELEMETRY", str(tmp_path / "t.jsonl"))
        code = shim.main(["-C", str(repo.root), "rev-parse", "nope123"])
        capsys.readouterr()
        assert code != 0

    def test_subcommand_extraction_skips_flags_and_their_values(self):
        from gita.telemetry import shim

        assert shim.subcommand(["-C", "/tmp", "diff", "HEAD"]) == "diff"
        assert shim.subcommand(["--no-pager", "log"]) == "log"
        assert shim.subcommand(["--git-dir", "/x/.git", "status"]) == "status"
        assert shim.subcommand(["-c", "user.name=x", "commit"]) == "commit"

    def test_install_writes_a_shim(self, tmp_path):
        from gita.telemetry import shim

        directory = shim.install(tmp_path / "shims")
        assert any(p.stem == "git" for p in directory.iterdir())


class TestCliEmitsTelemetry:
    def test_diff_records_an_event(self, repo, tmp_path, monkeypatch):
        import io

        from gita.cli import main

        path = tmp_path / "t.jsonl"
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        main(["-C", str(repo.root), "diff"], out=io.StringIO())

        events = load_events(path)
        assert events[0]["arm"] == "gita"
        assert events[0]["tool"] == "diff"
        assert events[0]["output_tokens"] > 0
        assert events[0]["layer"] == "L1"

    def test_show_is_recorded_as_the_expensive_layer(self, repo, tmp_path, monkeypatch):
        import io

        from gita.cli import main

        path = tmp_path / "t.jsonl"
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        main(["-C", str(repo.root), "show", "app.py::Store::get"], out=io.StringIO())
        assert load_events(path)[0]["layer"] == "L2"

    def test_telemetry_off_by_default(self, repo, tmp_path, monkeypatch):
        import io

        from gita.cli import main

        monkeypatch.delenv("GITA_TELEMETRY", raising=False)
        assert main(["-C", str(repo.root), "diff"], out=io.StringIO()) == 0
