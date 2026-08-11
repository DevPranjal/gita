"""Errors that tell an agent what to do next.

An agent cannot ask a follow-up question. A message it does not understand costs
a whole turn of re-sent context, which is more expensive than anything the
message could have saved. So every failure states what happened, and where it is
knowable, what to run instead.

An audit of the failure paths found six that did not meet that bar. These tests
are the spec for fixing them.
"""

from __future__ import annotations

import io
import subprocess

import pytest

from gita.cli import main


def run(root, *args) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["-C", str(root), *args], out=out)
    return code, out.getvalue()


@pytest.fixture
def bare(tmp_path):
    """A directory that is not a git repository."""
    return tmp_path


class TestUnknownRevision:
    def test_names_the_revision_that_was_not_found(self, repo):
        _, output = run(repo.root, "diff", "nosuchref", "HEAD")
        assert "nosuchref" in output

    def test_does_not_leak_the_git_command_we_ran(self, repo):
        """Our plumbing is not the agent's problem."""
        _, output = run(repo.root, "diff", "nosuchref", "HEAD")
        assert "--name-status" not in output
        assert "-M" not in output

    def test_does_not_repeat_git_advice_text(self, repo):
        """`Use '--' to separate paths from revisions` is the exact noise we exist to remove."""
        _, output = run(repo.root, "diff", "nosuchref", "HEAD")
        assert "separate paths from revisions" not in output
        assert len(output.strip().splitlines()) <= 2

    def test_is_a_distinct_exit_code(self, repo):
        code, _ = run(repo.root, "diff", "nosuchref", "HEAD")
        assert code != 0


class TestNotARepository:
    def test_says_so_rather_than_blaming_the_revision(self, bare):
        """"unknown revision: HEAD" sent the agent looking for a branch."""
        code, output = run(bare, "diff")
        assert code != 0
        assert "not a git repository" in output.lower()
        assert "unknown revision" not in output.lower()


class TestEntityNotFound:
    def test_distinguishes_unchanged_from_nonexistent(self, repo):
        """`show handle` on a range where it did not change is not "not found".

        The entity exists. Saying it does not sends an agent hunting for a
        renamed or deleted function that is sitting right there.
        """
        _, output = run(repo.root, "show", "Store::put")
        assert "not found" not in output.lower()
        assert "did not change" in output.lower()

    def test_a_genuinely_absent_name_says_so(self, repo):
        code, output = run(repo.root, "show", "totally_not_a_function")
        assert code != 0
        assert "totally_not_a_function" in output

    def test_offers_the_command_that_lists_what_did_change(self, repo):
        _, output = run(repo.root, "show", "totally_not_a_function")
        assert "gita diff" in output

    def test_history_of_an_unknown_name_offers_a_next_step(self, repo):
        _, output = run(repo.root, "history", "totally_not_a_function")
        assert "gita diff" in output


class TestBudgetTooSmall:
    def test_never_exits_zero_with_no_output(self, repo):
        """Silence that looks like success is the worst answer available.

        An agent cannot tell "nothing changed" from "your budget bought nothing",
        and both look like a working tool returning an empty result.
        """
        code, output = run(repo.root, "diff", "HEAD^", "HEAD", "--budget", "0")
        assert not (code == 0 and output.strip() == "")

    def test_says_the_budget_was_the_problem(self, repo):
        _, output = run(repo.root, "diff", "HEAD^", "HEAD", "--budget", "0")
        assert "budget" in output.lower()

    def test_a_workable_budget_still_answers(self, repo):
        code, output = run(repo.root, "diff", "HEAD^", "HEAD", "--budget", "400")
        assert code == 0 and "handle" in output


class TestUsage:
    def test_a_bad_flag_prints_usage_once(self, repo, capsys):
        """It was printed twice: once by argparse on stderr, once by us on stdout.

        Capturing only one stream hides that, which is how it survived.
        """
        out = io.StringIO()
        main(["-C", str(repo.root), "diff", "--oops"], out=out)
        combined = out.getvalue() + capsys.readouterr().err
        assert combined.lower().count("usage:") <= 1

    def test_a_bad_flag_names_the_flag(self, repo, capsys):
        out = io.StringIO()
        main(["-C", str(repo.root), "diff", "--oops"], out=out)
        combined = out.getvalue() + capsys.readouterr().err
        assert "--oops" in combined
