"""WS-6 CLI. Written before the implementation; these behaviours are the spec."""

from __future__ import annotations

import io
import json

import pytest

from gita.cli import main


def run(repo, *args) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["-C", str(repo.root), *args], out=out)
    return code, out.getvalue()


class TestDiff:
    def test_reports_changed_entities(self, repo):
        code, output = run(repo, "diff")
        assert code == 0
        assert "handle" in output
        assert "Store::get" in output

    def test_defaults_to_last_commit(self, repo):
        assert run(repo, "diff")[1] == run(repo, "diff", "HEAD^", "HEAD")[1]

    def test_accepts_explicit_revisions(self, repo):
        code, output = run(repo, "diff", "HEAD^", "HEAD")
        assert code == 0 and output.strip()

    def test_suppresses_noise_by_default(self, repo):
        _, output = run(repo, "diff")
        assert "unchanged" not in output
        assert "cosmetic" not in output

    def test_headline_precedes_detail(self, repo):
        _, output = run(repo, "diff")
        assert output.index("file") < output.index("app.py::handle")

    def test_alias_darshan_matches_diff(self, repo):
        assert run(repo, "darshan")[1] == run(repo, "diff")[1]


class TestBudget:
    def test_budget_is_reported_and_honoured(self, repo):
        _, output = run(repo, "diff", "--budget", "40", "--json")
        payload = json.loads(output)
        assert payload["tokens"] <= 40
        assert payload["budget"] == 40

    @pytest.mark.parametrize("budget", ["0", "5", "25", "100", "4000"])
    def test_any_budget_holds(self, repo, budget):
        payload = json.loads(run(repo, "diff", "--budget", budget, "--json")[1])
        assert payload["tokens"] <= int(budget)

    def test_generous_budget_is_not_truncated(self, repo):
        payload = json.loads(run(repo, "diff", "--budget", "4000", "--json")[1])
        assert payload["truncated"] is False


class TestJson:
    def test_emits_valid_json(self, repo):
        payload = json.loads(run(repo, "diff", "--json")[1])
        assert set(payload) >= {"base", "head", "l0", "l1", "tokens", "changes"}

    def test_changes_carry_entity_ids(self, repo):
        payload = json.loads(run(repo, "diff", "--json")[1])
        ids = [c["id"] for c in payload["changes"]]
        assert "app.py::handle" in ids
        assert all("kind" in c for c in payload["changes"])

    def test_entity_ids_are_usable_with_show(self, repo):
        payload = json.loads(run(repo, "diff", "--json")[1])
        entity_id = next(c["id"] for c in payload["changes"] if "handle" in c["id"])
        code, output = run(repo, "show", entity_id)
        assert code == 0 and output.strip()


class TestShow:
    def test_prints_hunks_for_one_entity(self, repo):
        code, output = run(repo, "show", "app.py::Store::get")
        assert code == 0
        assert "self.data[key]" in output
        assert "def handle" not in output

    def test_unknown_entity_exits_non_zero(self, repo):
        code, output = run(repo, "show", "app.py::missing")
        assert code != 0
        assert "not found" in output.lower()

    def test_alias_shloka(self, repo):
        assert run(repo, "shloka", "app.py::Store::get")[1] == \
               run(repo, "show", "app.py::Store::get")[1]


class TestAsk:
    def test_narrows_to_the_question(self, repo):
        code, output = run(repo, "ask", "handle")
        assert code == 0
        assert "handle" in output

    def test_question_is_echoed(self, repo):
        _, output = run(repo, "ask", "handle")
        assert "handle" in output

    def test_alias_prashna(self, repo):
        assert run(repo, "prashna", "handle")[1] == run(repo, "ask", "handle")[1]


class TestExpand:
    def test_lists_children_of_an_entity(self, repo):
        code, output = run(repo, "expand", "app.py::Store")
        assert code == 0
        assert "Store::get" in output

    def test_unknown_parent_exits_non_zero(self, repo):
        assert run(repo, "expand", "app.py::nothing")[0] != 0

    def test_alias_vistaar(self, repo):
        assert run(repo, "vistaar", "app.py::Store")[1] == \
               run(repo, "expand", "app.py::Store")[1]


class TestSavings:
    def test_compares_against_raw_diff(self, repo):
        code, output = run(repo, "savings")
        assert code == 0
        assert "raw" in output.lower()
        assert "%" in output

    def test_json_carries_the_numbers(self, repo):
        payload = json.loads(run(repo, "savings", "--json")[1])
        assert payload["raw_tokens"] > payload["l1_tokens"] > 0
        assert 0 < payload["reduction"] < 1


class TestErrors:
    def test_no_command_prints_help(self, repo):
        code, output = run(repo)
        assert code != 0
        assert "usage" in output.lower()

    def test_unknown_command_exits_non_zero(self, repo):
        assert run(repo, "wat")[0] != 0

    def test_bad_revision_is_reported_cleanly(self, repo):
        code, output = run(repo, "diff", "nope123", "HEAD")
        assert code != 0
        assert "nope123" in output or "revision" in output.lower()

    def test_non_repo_path_is_reported(self, tmp_path):
        out = io.StringIO()
        code = main(["-C", str(tmp_path), "diff"], out=out)
        assert code != 0
        assert "git" in out.getvalue().lower()
