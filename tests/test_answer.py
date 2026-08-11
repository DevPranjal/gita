"""One-shot answers.

Iteration 1 and 2 both showed gita costing about +1.25 turns per task. At roughly
126,000 tokens of context per turn, a turn costs ~100x more than gita's entire
output. Progressive disclosure optimises bytes per call; an agent loop is billed
by the turn. Making the agent drill is therefore a bad trade unless the diff is
enormous.

These tests specify the fix: answer completely in one call, and never cost more
than the raw diff would have.
"""

from __future__ import annotations

import subprocess

import pytest

from gita import diff_revisions
from gita.context import count_tokens
from gita.context.answer import DEFAULT_BUDGET, compose, material_patch
from gita.vcs.git import Repo


def answer(repo, base="HEAD^", head="HEAD", **kwargs):
    changeset = diff_revisions(repo, base, head)
    return compose(repo, base, head, changeset, **kwargs)


class TestCompleteness:
    def test_includes_the_headline(self, repo):
        assert "file" in answer(repo).text.splitlines()[0]

    def test_clean_working_tree_says_what_to_run_instead(self, repo):
        """Without a next step the agent must guess, and a guess costs a turn."""
        text = answer(repo, base="HEAD", head=None).text
        assert "clean" in text.lower()
        assert "HEAD^" in text

    def test_names_the_changed_files(self, repo):
        """Iteration 2 lost recall on a file-level question: entities are not enough."""
        text = answer(repo).text
        assert "app.py" in text
        assert "test_app.py" in text

    def test_lists_changed_entities(self, repo):
        text = answer(repo).text
        assert "handle" in text
        assert "Store::get" in text

    def test_includes_actual_code_without_a_second_call(self, repo):
        """The drill-down turn is what costs 126k tokens. Pre-empt it."""
        text = answer(repo).text
        assert "self.data[key]" in text or "def handle" in text
        assert "@@" in text

    def test_reports_which_entities_were_detailed(self, repo):
        result = answer(repo)
        assert result.detailed
        assert all("::" in e for e in result.detailed)


class TestBudget:
    @pytest.mark.parametrize("budget", [0, 40, 200, 1500, 20000])
    def test_never_exceeds_its_budget(self, repo, budget):
        assert answer(repo, budget=budget).tokens <= budget

    def test_larger_budget_yields_more_detail(self, repo):
        small = answer(repo, budget=150)
        large = answer(repo, budget=20000)
        assert len(large.detailed) >= len(small.detailed)
        assert large.tokens >= small.tokens

    def test_default_budget_is_generous_enough_to_avoid_a_second_call(self):
        # a turn costs ~126k tokens; being stingy here is a false economy
        assert DEFAULT_BUDGET >= 4000


class TestNeverWorseThanGit:
    """The invariant that makes gita safe to adopt."""

    def test_output_never_exceeds_the_raw_diff(self, repo):
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw = repo.raw_diff("HEAD^", "HEAD", changeset.paths())
        result = compose(repo, "HEAD^", "HEAD", changeset, budget=100000)
        assert result.tokens <= count_tokens(raw)

    def test_tiny_diff_still_respects_the_invariant(self, repo):
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw_tokens = count_tokens(repo.raw_diff("HEAD^", "HEAD", changeset.paths()))
        assert compose(repo, "HEAD^", "HEAD", changeset,
                       budget=raw_tokens * 10).tokens <= raw_tokens


class TestBriefMode:
    def test_brief_omits_hunks(self, repo):
        result = answer(repo, detail=False)
        assert "@@" not in result.text
        assert result.detailed == []

    def test_brief_is_cheaper_than_full(self, repo):
        assert answer(repo, detail=False).tokens < answer(repo).tokens


class TestMaterialPatch:
    """A unified diff with the noise removed: familiar format, zero learning cost."""

    def test_is_a_unified_diff(self, repo):
        patch = material_patch(repo, "HEAD^", "HEAD",
                               diff_revisions(repo, "HEAD^", "HEAD"))
        assert "@@" in patch
        assert patch.startswith("---") or "--- a/" in patch

    def test_contains_the_material_change(self, repo):
        patch = material_patch(repo, "HEAD^", "HEAD",
                               diff_revisions(repo, "HEAD^", "HEAD"))
        assert "self.data[key]" in patch

    def test_is_not_larger_than_the_raw_diff(self, repo):
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw = repo.raw_diff("HEAD^", "HEAD", changeset.paths())
        patch = material_patch(repo, "HEAD^", "HEAD", changeset)
        assert count_tokens(patch) <= count_tokens(raw)

    def test_respects_a_budget(self, repo):
        patch = material_patch(repo, "HEAD^", "HEAD",
                               diff_revisions(repo, "HEAD^", "HEAD"), budget=30)
        assert count_tokens(patch) <= 30


class TestRanking:
    def test_interface_changes_are_detailed_before_body_changes(self, repo):
        text = answer(repo, budget=20000).text
        detail = text.split("---", 1)[1] if "---" in text else text
        # handle's signature changed; Store::get only changed its body
        assert detail.index("handle") < detail.index("Store::get")


class TestSmallChangesStillNameWhatChanged:
    """"3 changes" followed by nothing is not an answer.

    The budget is capped by the raw `git diff`, and on a small diff the headline
    plus file list consumed all of it, so the entity lines -- the actual answer --
    were squeezed out. The agent was told how many things changed but not what.
    """

    def tiny(self, tmp_path):
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        (tmp_path / "m.py").write_bytes(b"def a():\n    return 1\n")
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        (tmp_path / "m.py").write_bytes(b"def a():\n    return 99\n")
        git("commit", "-qam", "second")
        return Repo(tmp_path)

    def test_the_changed_entity_is_named(self, tmp_path):
        repo = self.tiny(tmp_path)
        answer = compose(repo, "HEAD^", "HEAD", diff_revisions(repo, "HEAD^", "HEAD"))
        assert "m.py::a" in answer.text

    def test_still_never_larger_than_git(self, tmp_path):
        repo = self.tiny(tmp_path)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset)
        raw = count_tokens(repo.raw_diff("HEAD^", "HEAD", changeset.paths()))
        assert answer.tokens <= raw
