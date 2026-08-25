"""WS-8 telemetry: event capture and metric aggregation.

Written before the implementation. What is asserted here is what the dashboard
is allowed to claim.
"""

from __future__ import annotations

import json
import os

import pytest

from gita.telemetry import events

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


class TestCallerAttribution:
    """Usage is only interesting once agent calls can be told from manual ones."""

    def test_an_explicit_marker_wins(self, monkeypatch):
        monkeypatch.setenv("GITA_VIA", "vscode-mcp")
        assert events.caller() == "vscode-mcp"

    def test_copilot_cli_is_recognised(self, monkeypatch):
        monkeypatch.delenv("GITA_VIA", raising=False)
        monkeypatch.setenv("COPILOT_AGENT_ID", "x")
        assert events.caller() == "copilot-cli"

    def test_vscode_is_recognised(self, monkeypatch):
        monkeypatch.delenv("GITA_VIA", raising=False)
        for key in list(os.environ):
            if key.startswith(("COPILOT_", "GH_COPILOT", "GITHUB_COPILOT")):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("TERM_PROGRAM", "vscode")
        assert events.caller() == "vscode"

    def test_a_plain_shell_is_the_default(self, monkeypatch):
        for key in list(os.environ):
            if key.startswith(("COPILOT_", "GH_COPILOT", "GITHUB_COPILOT", "VSCODE_")):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv("GITA_VIA", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        assert events.caller() == "shell"

    def test_every_event_carries_who_and_where(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITA_VIA", "test-harness")
        sink = tmp_path / "usage.jsonl"
        events.record({"arm": "gita", "tool": "diff", "output_tokens": 10}, path=sink)
        written = json.loads(sink.read_text(encoding="utf8").strip())
        assert written["via"] == "test-harness"
        assert written["cwd"]

class TestAnAnswerDescribesItself:
    """A report could not tell "gita was concise" from "gita gave up".

    In the evaluation, 87% of the treatment arm's tool output came from falling
    back to raw git after an answer stopped early. Nothing in the log said an
    answer had stopped early, so the same question could not be asked of real
    usage.
    """

    def run(self, repo, path, monkeypatch, *argv):
        import io

        from gita.cli import main
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        main(["-C", str(repo.root), *argv], out=io.StringIO())
        return load_events(path)[-1]

    def test_a_diff_records_whether_it_was_complete(self, repo, tmp_path, monkeypatch):
        """A clean tree has nothing to leave out."""
        written = self.run(repo, tmp_path / "t.jsonl", monkeypatch, "diff")
        assert written["truncated"] is False

    def test_a_starved_diff_records_that_it_was_not(self, repo, tmp_path, monkeypatch):
        written = self.run(repo, tmp_path / "t.jsonl", monkeypatch,
                           "diff", "HEAD^", "HEAD", "--budget", "60")
        assert written["truncated"] is True

    def test_a_diff_records_its_shape(self, repo, tmp_path, monkeypatch):
        written = self.run(repo, tmp_path / "t.jsonl", monkeypatch,
                           "diff", "HEAD^", "HEAD")
        assert written["files"] >= 1
        assert written["changes"] >= 1
        assert "noise_filtered" in written

    def test_annotations_do_not_leak_into_the_next_event(self, repo, tmp_path,
                                                        monkeypatch):
        """A stale `truncated` would silently misattribute the following call."""
        path = tmp_path / "t.jsonl"
        self.run(repo, path, monkeypatch, "diff", "--budget", "60")
        written = self.run(repo, path, monkeypatch, "show", "app.py::Store::get")
        assert written["tool"] == "show"
        assert "truncated" not in written or written["truncated"] is None


class TestFailuresSayWhy:
    """`ok: false` alone turned twenty recorded failures into a mystery."""

    def run(self, path, monkeypatch, *argv):
        import io

        from gita.cli import main
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        code = main(list(argv), out=io.StringIO())
        return code, load_events(path)[-1]

    def test_a_bad_revision_is_named(self, repo, tmp_path, monkeypatch):
        _code, written = self.run(tmp_path / "t.jsonl", monkeypatch,
                                  "-C", str(repo.root), "diff", "nope123")
        assert written["ok"] is False
        assert written["error"] == "bad-revision-or-repo"

    def test_a_starved_budget_is_named(self, repo, tmp_path, monkeypatch):
        """`show` has no raw-diff cap to fall back on, so the budget really binds."""
        _code, written = self.run(tmp_path / "t.jsonl", monkeypatch,
                                  "-C", str(repo.root), "show", "nosuchentity")
        assert written["ok"] is False
        assert written["error"]

    def test_success_carries_no_error(self, repo, tmp_path, monkeypatch):
        _code, written = self.run(tmp_path / "t.jsonl", monkeypatch,
                                  "-C", str(repo.root), "diff")
        assert written["ok"] is True
        assert written["error"] is None


class TestWhoConsumedTheOutput:
    """`via` is inferred from environment markers, which are process-wide, so a
    hand-typed command in an agent's terminal was logged as agent traffic."""

    def test_a_redirected_stream_is_not_interactive(self, tmp_path, monkeypatch):
        sink = tmp_path / "t.jsonl"
        events.record({"arm": "gita", "tool": "diff"}, path=sink)
        assert json.loads(sink.read_text(encoding="utf8").strip())["interactive"] is False

    def test_options_used_are_recorded(self, repo, tmp_path, monkeypatch):
        """`savings` was retired on 0 uses in 1,209 calls; nothing else can be."""
        import io

        from gita.cli import main
        path = tmp_path / "t.jsonl"
        monkeypatch.setenv("GITA_TELEMETRY", str(path))
        main(["-C", str(repo.root), "diff", "--interface-only"], out=io.StringIO())
        assert "interface_only" in load_events(path)[-1]["options"]


class TestTheLogCanBeReadLater:
    def test_every_event_declares_its_schema(self, tmp_path):
        sink = tmp_path / "t.jsonl"
        events.record({"arm": "gita", "tool": "diff"}, path=sink)
        assert json.loads(sink.read_text(encoding="utf8").strip())["v"] == events.SCHEMA

    def test_events_are_ordered_within_a_session(self, tmp_path):
        """Timestamps collide at millisecond resolution on fast calls."""
        sink = tmp_path / "t.jsonl"
        for _ in range(3):
            events.record({"arm": "gita", "tool": "diff"}, path=sink)
        seqs = [e["seq"] for e in load_events(sink)]
        assert seqs == sorted(seqs) and len(set(seqs)) == 3
