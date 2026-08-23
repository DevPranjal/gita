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
from gita.context.answer import (DEFAULT_BUDGET, MAX_DETAILED, compose,
                                 material_patch)
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


class TestEveryLineIsBudgeted:
    """The unreferenced line was appended after the budget was spent.

    It is up to 133 tokens, so `gita diff --budget 120` emitted 211 -- the one
    guarantee the design rests on, broken by a line added as an afterthought.
    Found by installing the wheel and running it, not by the unit tests.
    """

    def repo_with_orphans(self, tmp_path):
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        (tmp_path / "m.py").write_bytes(b"def kept():\n    return 1\n")
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        body = b"def kept():\n    return 1\n"
        for i in range(6):
            body += (f"\n\ndef unreferenced_helper_with_a_long_name_{i}(argument):\n"
                     f"    return argument + {i}\n").encode()
        (tmp_path / "m.py").write_bytes(body)
        git("add", "-A")
        git("commit", "-q", "-m", "second")
        return Repo(tmp_path)

    @pytest.mark.parametrize("budget", [40, 60, 120, 240])
    def test_the_budget_survives_unreferenced_additions(self, tmp_path, budget):
        repo = self.repo_with_orphans(tmp_path)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=budget)
        assert answer.tokens <= answer.budget

    def test_the_finding_is_still_reported_when_it_fits(self, tmp_path):
        repo = self.repo_with_orphans(tmp_path)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=DEFAULT_BUDGET)
        assert "unreferenced" in answer.text

    def test_dropping_it_is_declared_as_truncation(self, tmp_path):
        repo = self.repo_with_orphans(tmp_path)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=40)
        assert answer.truncated


class TestAnIncompleteAnswerSaysSo:
    """Silent truncation is what sends the agent back to raw git.

    On a Flask dependency bump gita spent 2,111 of its 6,000-token budget,
    withheld fourteen of thirty-four changes because of MAX_DETAILED, and
    reported `truncated: False`. The agent could tell the answer had stopped
    early, did not believe gita's claim to be complete, and recovered the only
    way it was sure of: `git diff -- uv.lock`, 214,918 tokens. Every unit test
    passed throughout -- none of them asked whether a cap that is not the budget
    admits to binding.
    """

    def repo_with_many_entities(self, tmp_path, count, body_lines=1):
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        pad = "".join(f"    step_{j} = x + {j}\n" for j in range(body_lines))
        before = "".join(f"def fn_{i}(x):\n{pad}    return x + {i}\n\n"
                         for i in range(count))
        (tmp_path / "m.py").write_text(before)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        after = "".join(f"def fn_{i}(x):\n{pad}    return x * {i}\n\n"
                        for i in range(count))
        (tmp_path / "m.py").write_text(after)
        git("add", "-A")
        git("commit", "-q", "-m", "second")
        return Repo(tmp_path)

    def test_the_detail_cap_admits_to_binding(self, tmp_path):
        """MAX_DETAILED is not the budget, so it has to declare itself."""
        repo = self.repo_with_many_entities(tmp_path, MAX_DETAILED + 12)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=DEFAULT_BUDGET)
        assert len(answer.detailed) <= MAX_DETAILED
        assert answer.truncated, "withheld entities while claiming completeness"

    def test_it_names_a_cheaper_next_call_than_raw_git(self, tmp_path):
        """Naming the recovery is the point: the agent's own guess cost 214,918."""
        repo = self.repo_with_many_entities(tmp_path, MAX_DETAILED + 12)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        text = compose(repo, "HEAD^", "HEAD", changeset,
                       budget=DEFAULT_BUDGET).text
        assert "gita show" in text
        assert "--filter" in text

    def test_a_complete_answer_stays_quiet(self, tmp_path):
        """A notice on every answer is noise, and would train the agent past it."""
        repo = self.repo_with_many_entities(tmp_path, 2, body_lines=6)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=DEFAULT_BUDGET)
        assert not answer.truncated
        assert "gita show" not in answer.text

    def test_it_does_not_offer_a_budget_that_cannot_help(self, tmp_path):
        """The raw diff caps the budget, so raising it buys a turn and nothing else."""
        repo = self.repo_with_many_entities(tmp_path, MAX_DETAILED + 12)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=DEFAULT_BUDGET)
        assert answer.tokens < DEFAULT_BUDGET, "raw diff should be the cap here"
        assert "--budget" not in answer.text
        assert "gita show" in answer.text

    @pytest.mark.parametrize("budget", [40, 80, 150, 400, 1200, 6000])
    def test_the_notice_is_bought_with_detail_not_with_overrun(self, tmp_path,
                                                               budget):
        """The v1.0.0 bug was a line appended after the budget was spent."""
        repo = self.repo_with_many_entities(tmp_path, MAX_DETAILED + 12)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=budget)
        assert answer.tokens <= answer.budget


class TestNeverLargerThanGitAnywhere:
    """The invariant is measured against the text actually emitted.

    Summing the parts missed the blank line joining summary to sections, so one
    answer came out a single token over the raw diff it promises never to
    exceed. One token is still a broken promise.
    """

    def test_holds_on_this_repository(self):
        repo = Repo(".")
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw = count_tokens(repo.raw_diff("HEAD^", "HEAD", changeset.paths()))
        for budget in (40, 120, 400, 1200, DEFAULT_BUDGET):
            answer = compose(repo, "HEAD^", "HEAD", changeset, budget=budget)
            assert answer.tokens <= raw, f"budget {budget} exceeded the raw diff"
            assert answer.tokens <= answer.budget

    @pytest.mark.parametrize("budget", [1, 7, 33, 91, 512])
    def test_awkward_budgets_are_still_respected(self, budget):
        repo = Repo(".")
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=budget)
        assert answer.tokens <= answer.budget
