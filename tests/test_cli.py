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
        code, output = run(repo, "diff", "HEAD^", "HEAD")
        assert code == 0
        assert "handle" in output
        assert "Store::get" in output

    def test_defaults_to_the_working_tree_like_git(self, repo):
        """Bare `gita diff` must mean what bare `git diff` means.

        Defaulting to HEAD^..HEAD silently answered the wrong question when an
        agent asked about uncommitted work, costing 12 turns in evaluation.
        """
        code, output = run(repo, "diff")
        assert code == 0
        assert "clean" in output.lower()
        assert "HEAD^" in output          # and it says what to run instead

    def test_accepts_explicit_revisions(self, repo):
        code, output = run(repo, "diff", "HEAD^", "HEAD")
        assert code == 0 and "handle" in output

    def test_suppresses_noise_by_default(self, repo):
        _, output = run(repo, "diff", "HEAD^", "HEAD")
        assert "unchanged" not in output
        assert "cosmetic" not in output

    def test_headline_precedes_detail(self, repo):
        _, output = run(repo, "diff", "HEAD^", "HEAD")
        assert output.index("file") < output.index("app.py::handle")

    def test_alias_darshan_matches_diff(self, repo):
        assert run(repo, "darshan", "HEAD^", "HEAD")[1] == run(repo, "diff", "HEAD^", "HEAD")[1]


class TestBudget:
    def test_budget_is_reported_and_honoured(self, repo):
        _, output = run(repo, "diff", "HEAD^", "HEAD", "--budget", "40", "--json")
        payload = json.loads(output)
        assert payload["tokens"] <= 40
        assert payload["budget"] == 40

    @pytest.mark.parametrize("budget", ["5", "25", "100", "4000"])
    def test_any_budget_holds(self, repo, budget):
        payload = json.loads(run(repo, "diff", "HEAD^", "HEAD", "--budget", budget, "--json")[1])
        assert payload["tokens"] <= int(budget)

    def test_a_budget_too_small_to_answer_says_so(self, repo):
        """The budget bounds the answer, and a diagnostic is not an answer.

        Returning nothing at all and exiting zero was indistinguishable from
        "nothing changed", which is a different fact entirely.
        """
        code, output = run(repo, "diff", "HEAD^", "HEAD", "--budget", "0", "--json")
        assert code != 0
        assert json.loads(output)["error"] == "budget too small"

    def test_output_never_exceeds_a_raw_git_diff(self, repo):
        from gita import diff_revisions
        from gita.context import count_tokens

        payload = json.loads(run(repo, "diff", "HEAD^", "HEAD", "--budget", "40000", "--json")[1])
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw = repo.raw_diff("HEAD^", "HEAD", changeset.paths())
        assert payload["tokens"] <= count_tokens(raw)


class TestJson:
    def test_emits_valid_json(self, repo):
        payload = json.loads(run(repo, "diff", "HEAD^", "HEAD", "--json")[1])
        assert set(payload) >= {"base", "head", "text", "tokens", "changes"}

    def test_changes_carry_entity_ids(self, repo):
        payload = json.loads(run(repo, "diff", "HEAD^", "HEAD", "--json")[1])
        ids = [c["id"] for c in payload["changes"]]
        assert "app.py::handle" in ids
        assert all("kind" in c for c in payload["changes"])

    def test_entity_ids_are_usable_with_show(self, repo):
        payload = json.loads(run(repo, "diff", "HEAD^", "HEAD", "--json")[1])
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
        assert "app.py::missing" in output
        assert "gita diff" in output          # and what to run instead

    def test_alias_shloka(self, repo):
        assert run(repo, "shloka", "app.py::Store::get")[1] == \
               run(repo, "show", "app.py::Store::get")[1]


class TestFilters:
    def test_filter_narrows_output(self, repo):
        code, output = run(repo, "diff", "HEAD^", "HEAD", "--filter", "handle")
        assert code == 0
        assert "handle" in output
        assert "Store::put" not in output

    def test_brief_omits_code(self, repo):
        _, output = run(repo, "diff", "HEAD^", "HEAD", "--brief")
        assert "handle" in output
        assert "@@" not in output

    def test_patch_mode_is_a_diff(self, repo):
        _, output = run(repo, "diff", "HEAD^", "HEAD", "--patch")
        assert "@@" in output
        assert "self.data[key]" in output

    def test_interface_only_is_computed_not_guessed(self, repo):
        payload = json.loads(run(repo, "diff", "HEAD^", "HEAD", "--interface-only", "--json")[1])
        assert payload["interface_only"] is True
        assert all(c["interface"] for c in payload["changes"])
        assert "handle" in payload["text"]          # signature changed
        assert "Store::get" not in payload["text"]  # body only

    def test_unmatched_filter_returns_no_entities(self, repo):
        _, output = run(repo, "diff", "HEAD^", "HEAD", "--filter", "kubernetes")
        assert "app.py::handle" not in output


class TestExpand:
    def test_lists_children_of_an_entity(self, repo):
        code, output = run(repo, "expand", "app.py::Store", "HEAD^", "HEAD")
        assert code == 0
        assert "Store::get" in output

    def test_accepts_a_bare_name(self, repo):
        """Agents type `handle`, not `app.py::handle`."""
        code, output = run(repo, "show", "handle", "HEAD^", "HEAD")
        assert code == 0
        assert "def handle" in output

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
    def test_no_command_prints_short_usage(self, repo):
        code, output = run(repo)
        assert code != 0
        assert "gita diff" in output
        # orientation should be cheap: an agent reading this pays for every line
        assert len(output.splitlines()) <= 14

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
