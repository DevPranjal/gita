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
from gita.context.rollup import MAX_DEPTH, fit_lines, rollup_lines
from gita.vcs.git import Repo


def answer(repo, base="HEAD^", head="HEAD", **kwargs):
    changeset = diff_revisions(repo, base, head)
    return compose(repo, base, head, changeset, **kwargs)


def detail_sections(text):
    sections = {}
    path = entity_id = None
    for line in text.splitlines():
        if line.startswith("--- "):
            _, _, name = line.split(" ", 2)
            if name.startswith("::"):
                entity_id = path if name == "::<module>" else path + name
            else:
                entity_id = name
                path = name.partition("::")[0]
            sections[entity_id] = []
        elif entity_id:
            sections[entity_id].append(line)
    return {key: "\n".join(lines) for key, lines in sections.items()}


class TestNestedDetail:
    @pytest.fixture
    def nested_repo(self, tmp_path):
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)

        before = (
            "import old_dependency\n\n"
            "def outer():\n"
            "    limit = 1\n"
            "    def inner():\n"
            "        return 'old_value'\n"
            "    return inner()\n"
        )
        (tmp_path / "m.py").write_text(before)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "before")
        after = before.replace("old_dependency", "new_dependency") \
                      .replace("limit = 1", "limit = 2") \
                      .replace("inner():", "inner(value=0):") \
                      .replace("old_value", "new_value")
        (tmp_path / "m.py").write_text(after)
        git("commit", "-qam", "after")
        return Repo(tmp_path)

    def test_nested_edits_appear_once_without_losing_parent_edits(self, nested_repo):
        result = answer(nested_repo, budget=20000, respect_raw_diff=False)
        for edit in ("+import new_dependency", "+    limit = 2",
                     "+    def inner(value=0):", "+        return 'new_value'"):
            assert result.text.count(edit) == 1
        assert not result.truncated
        assert "signature_changed outer::inner" in result.text
        assert set(result.detailed) == {"m.py", "m.py::outer", "m.py::outer::inner"}
        sections = detail_sections(result.text)
        module, outer, inner = (sections[key] for key in
                                ("m.py", "m.py::outer", "m.py::outer::inner"))
        assert "new_dependency" in module and "new_value" not in module
        assert "limit = 2" in outer and "new_value" not in outer
        assert "new_value" in inner and "limit = 2" not in inner

    def test_filtering_to_a_child_still_shows_its_diff(self, nested_repo):
        from gita.context.navigate import focus

        cs = focus(diff_revisions(nested_repo, "HEAD^", "HEAD"), "inner")
        result = compose(nested_repo, "HEAD^", "HEAD", cs,
                         budget=20000, respect_raw_diff=False)
        assert "+        return 'new_value'" in result.text
        assert result.detailed == ["m.py::outer::inner"]

    @pytest.mark.parametrize("removed", [False, True])
    def test_whole_file_addition_or_removal_is_not_repeated(self, nested_repo, removed):
        from gita.vcs.git import EMPTY_TREE

        base, head = ("HEAD", EMPTY_TREE) if removed else (EMPTY_TREE, "HEAD")
        result = answer(nested_repo, base, head, budget=20000,
                        respect_raw_diff=False, check_references=False)
        sign = "-" if removed else "+"
        assert result.text.count(f"{sign}        return 'new_value'") == 1
        assert not result.truncated

    def test_oversized_parent_falls_back_to_child_and_reports_missing_code(
            self, nested_repo):
        (nested_repo.root / "m.py").write_text(
            "import new_dependency\n" +
            "".join(f"setting_{i} = {i}\n" for i in range(300)) +
            "\ndef outer():\n"
            "    limit = 1\n"
            "    def inner():\n"
            "        return 'small_edit'\n"
            "    return inner()\n"
        )
        cs = diff_revisions(nested_repo, "HEAD^", None)
        result = compose(nested_repo, "HEAD^", None, cs, budget=500,
                         respect_raw_diff=False)
        assert "+        return 'small_edit'" in result.text
        assert result.text.count("+        return 'small_edit'") == 1
        assert result.truncated
        assert "code not shown for 1" in result.text
        assert result.tokens <= result.budget
        patch = material_patch(nested_repo, "HEAD^", None, cs, budget=500,
                               respect_raw_diff=False).patch
        assert "+        return 'small_edit'" in patch
        assert count_tokens(patch) <= 500
    def test_identical_edits_at_distinct_locations_are_kept(self, nested_repo):
        before = ("def first():\n    return 1\n\n"
                  "def second():\n    return 1\n")
        (nested_repo.root / "m.py").write_text(before)
        nested_repo._run("commit", "-qam", "two siblings")
        (nested_repo.root / "m.py").write_text(before.replace("return 1", "return 2"))
        result = answer(nested_repo, "HEAD", None, budget=20000,
                        respect_raw_diff=False)
        assert result.text.count("+    return 2") == 2
        assert set(result.detailed) == {"m.py::first", "m.py::second"}

    def test_added_mock_keeps_its_own_closing_delimiter(self, nested_repo):
        before = (
            "vi.mock('../../utils/uatTriageWorkbookExport', () => ({\n"
            "  exportUatTriageWorkbook: testState.exportWorkbook,\n"
            "}));\n"
        )
        added = (
            "vi.mock('../../telemetry/clarity', () => ({\n"
            "  trackClarityEvent: testState.trackClarityEvent,\n"
            "}));\n"
        )
        path = nested_repo.root / "m.ts"
        path.write_text(before)
        nested_repo._run("add", "m.ts")
        nested_repo._run("commit", "-qm", "existing mock")
        path.write_text(before + "\n" + added)

        result = answer(nested_repo, "HEAD", None, budget=20000,
                        respect_raw_diff=False, check_references=False)
        assert "+vi.mock('../../telemetry/clarity', () => ({\n" in result.text
        assert "+  trackClarityEvent: testState.trackClarityEvent,\n+}));" in result.text
        assert "exportUatTriageWorkbook: testState.exportWorkbook" not in result.text
        assert result.text.count("+}));") == 1

    def test_parent_filter_does_not_pull_in_unselected_child_edits(self, nested_repo):
        from gita.diff.changes import ChangeSet

        cs = diff_revisions(nested_repo, "HEAD^", "HEAD")
        selected = ChangeSet([c for c in cs.material() if c.id == "m.py"], files_changed=1)
        result = compose(nested_repo, "HEAD^", "HEAD", selected,
                         budget=20000, respect_raw_diff=False)
        assert "+import new_dependency" in result.text
        assert "limit = 2" not in result.text
        assert "new_value" not in result.text

    def test_cosmetic_child_edit_is_not_printed_with_material_parent(self, nested_repo):
        original = (nested_repo.root / "m.py").read_text()
        (nested_repo.root / "m.py").write_text(
            original.replace("new_dependency", "another_dependency")
                    .replace("return 'new_value'", "return  'new_value'  # cosmetic")
        )
        result = answer(nested_repo, "HEAD", None, budget=20000,
                        respect_raw_diff=False)
        assert "+import another_dependency" in result.text
        assert "# cosmetic" not in result.text
        assert "new_value" not in result.text

    def test_changed_arguments_outside_unchanged_callback_are_not_lost(self, nested_repo):
        before = "const cb = useCallback(() => {\n  return 1;\n}, [oldDep]);\n"
        path = nested_repo.root / "m.ts"
        path.write_text(before)
        nested_repo._run("add", "m.ts")
        nested_repo._run("commit", "-qm", "callback")
        path.write_text(before.replace("oldDep", "newDep"))
        result = answer(nested_repo, "HEAD", None, budget=20000,
                        respect_raw_diff=False)
        assert "-}, [oldDep]);" in result.text
        assert "+}, [newDep]);" in result.text
        assert result.text.count("+}, [newDep]);") == 1


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
        assert set(result.detailed) == set(detail_sections(result.text))


class TestBudget:
    @pytest.mark.parametrize("budget", [0, 40, 200, 1500, 20000])
    def test_never_exceeds_its_budget(self, repo, budget):
        assert answer(repo, budget=budget).tokens <= budget

    def test_larger_budget_yields_more_detail(self, repo):
        small = answer(repo, budget=150)
        large = answer(repo, budget=20000)
        assert len(large.detailed) >= len(small.detailed)
        assert large.budget >= small.budget

    def test_default_budget_is_generous_enough_to_avoid_a_second_call(self):
        # a turn costs ~126k tokens; being stingy here is a false economy
        assert DEFAULT_BUDGET >= 4000


class TestTheBudgetIsSpentBeforeAnythingIsWithheld:
    """A reservation nobody claimed is not a reason to withhold the answer.

    SUMMARY_SHARE holds most of the budget back for hunks so that entity names
    cannot crowd them out. It was being enforced as a ceiling rather than a
    floor. When line ranges made each summary line about 35% more expensive, the
    list overflowed its 35% share and entities were dropped -- while the hunks
    declined 53 of the 121 tokens reserved for them. On this fixture the answer
    shrank from 176 tokens to 133 against the same 186-token budget and named
    two of the four changed entities. Spending less to say less is the one
    trade this module exists to refuse.
    """

    def test_nothing_is_withheld_while_the_reservation_goes_unspent(self, repo):
        result = answer(repo)
        unspent = result.budget - result.tokens
        assert "more changed entities not listed" not in result.text, (
            f"withheld entity names with {unspent} tokens of budget unspent")

    def test_a_brief_answer_may_use_the_whole_budget(self, repo):
        """With no hunks to protect, the share has nothing left to protect from."""
        assert "more changed entities not listed" not in answer(
            repo, detail=False).text

    # The re-flow hands leftover budget back to the summary, so the cap has to
    # hold across the whole range where that trade is live, not just at the ends.
    @pytest.mark.parametrize("budget", range(60, 420, 20))
    def test_the_cap_still_holds_wherever_the_reflow_applies(self, repo, budget):
        assert answer(repo, budget=budget).tokens <= budget


class TestLineRangesAreShedBeforeEntities:
    """Naming what changed outranks saying where to find it.

    A range tells a reader where to look; the name tells them what to look at.
    An entity that is never named cannot be looked up at all, so under pressure
    the range goes first. Line ranges used to be unsheddable, which inverted
    this: a list that no longer fit lost whole entities instead.
    """

    def test_a_squeezed_list_drops_ranges_rather_than_names(self, repo):
        changes = diff_revisions(repo, "HEAD^", "HEAD").material()
        spanned = rollup_lines(changes, MAX_DEPTH)
        plain = rollup_lines(changes, MAX_DEPTH, spans=False)

        budget = count_tokens("\n".join(plain))
        assert count_tokens("\n".join(spanned)) > budget, \
            "fixture no longer squeezes; the test proves nothing"

        assert fit_lines(changes, budget)[0] == plain


class TestTruthfulSummaryCoverage:
    def sample(self, both=False):
        from gita import ChangeSet, diff_trees, extract

        path = "src/controllers/HealthController.cs"
        old = (
            b"namespace Example.Application.Controllers;\n"
            b"class HealthController {\n"
            b"    bool DeepHealthCheck() {\n"
            b"        var healthy = true;\n"
            b"        return healthy;\n"
            b"    }\n"
            b"    bool SmokeTest() { return true; }\n"
            b"}\n"
        )
        new = old.replace(b"healthy = true", b"healthy = false")
        if both:
            new = new.replace(b"SmokeTest() { return true", b"SmokeTest() { return false")

        class Source:
            def blob(self, rev, requested_path):
                assert requested_path == path
                return old if rev == "old" else new

        return Source(), ChangeSet(diff_trees(extract(old, path), extract(new, path)),
                                   files_changed=1)

    def test_showing_the_only_hunk_does_not_require_a_second_summary_entry(self):
        source, changes = self.sample()
        shown = 0
        for budget in range(80, 201, 5):
            result = compose(source, "old", "new", changes, budget=budget,
                             respect_raw_diff=False, check_references=False)
            if result.detailed:
                shown += 1
                assert not result.truncated, (budget, result.text)
                assert "more changed entities" not in result.text
                assert "output cut" not in result.text
            assert result.tokens <= budget
        assert shown

    def test_brief_rollup_or_span_shedding_is_not_truncation(self):
        from gita.context.answer import _headline

        source, changes = self.sample(both=True)
        short = rollup_lines(changes.material(), 1, group_paths=True, spans=False)
        assert len(short) == 1 and len(changes.material()) == 2
        budget = count_tokens("\n\n".join([_headline(changes, changes.material()), *short]))
        result = compose(source, "old", "new", changes, budget=budget, detail=False,
                         respect_raw_diff=False, check_references=False)
        assert not result.truncated
        assert "(+1 nested)" in result.text
        assert result.tokens <= budget

    def test_dropped_group_counts_entities_not_rendered_rows(self, monkeypatch):
        import gita.context.answer as module

        source, changes = self.sample(both=True)
        notices = []
        original = module._notice

        def record(names_hidden, *args, **kwargs):
            notices.append(names_hidden)
            return original(names_hidden, *args, **kwargs)

        monkeypatch.setattr(module, "_notice", record)
        budget = count_tokens(module._headline(changes, changes.material()))
        result = compose(source, "old", "new", changes, budget=budget, detail=False,
                         respect_raw_diff=False, check_references=False)
        assert result.truncated
        assert notices == [2]

    def test_pure_moves_still_need_summary_coverage(self):
        from gita import ChangeSet, diff_trees, extract

        code = b"namespace Example;\nclass Moved { int Run() { return 1; } }\n"
        changes = ChangeSet(diff_trees(extract(code, "old.cs"), extract(code, "new.cs")),
                            files_changed=1)

        class Source:
            def blob(self, rev, path):
                return code if (rev, path) in (("old", "old.cs"), ("new", "new.cs")) else None

        small = compose(Source(), "old", "new", changes, budget=20,
                        respect_raw_diff=False, check_references=False)
        full = compose(Source(), "old", "new", changes, budget=1000,
                       respect_raw_diff=False, check_references=False)
        assert small.truncated and not small.detailed
        assert not full.truncated and not full.detailed

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
                               diff_revisions(repo, "HEAD^", "HEAD")).patch
        assert "@@" in patch
        assert patch.startswith("---") or "--- a/" in patch

    def test_contains_the_material_change(self, repo):
        patch = material_patch(repo, "HEAD^", "HEAD",
                               diff_revisions(repo, "HEAD^", "HEAD")).patch
        assert "self.data[key]" in patch

    def test_is_not_larger_than_the_raw_diff(self, repo):
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw = repo.raw_diff("HEAD^", "HEAD", changeset.paths())
        patch = material_patch(repo, "HEAD^", "HEAD", changeset).patch
        assert count_tokens(patch) <= count_tokens(raw)

    def test_respects_a_budget(self, repo):
        patch = material_patch(repo, "HEAD^", "HEAD",
                               diff_revisions(repo, "HEAD^", "HEAD"), budget=30).patch
        assert count_tokens(patch) <= 30


class TestMaterialPatchOutcomes:
    def sample(self):
        from gita import ChangeSet, diff_trees, extract

        old = b"def large():\n    return 1\n\ndef small():\n    return 1\n"
        new = ("def large():\n" + "".join(f"    value_{i} = {i}\n" for i in range(200))
               + "    return 2\n\ndef small():\n    return 2\n").encode()

        class Source:
            def blob(self, rev, path):
                return old if rev == "old" else new

            def raw_diff(self, *args):
                return "tiny"

        return Source(), ChangeSet(diff_trees(extract(old, "m.py"), extract(new, "m.py")),
                                   files_changed=1)

    @pytest.mark.parametrize("budget,status,omitted", [
        (10000, "complete", []),
        (100, "partial", ["m.py::large"]),
        (0, "nothing_fits", ["m.py::large", "m.py::small"]),
    ])
    def test_status_and_omitted_entities_match_emitted_content(self, budget, status, omitted):
        repo, changes = self.sample()
        result = material_patch(repo, "old", "new", changes, budget=budget,
                                respect_raw_diff=False)
        assert result.status == status
        assert result.omitted == omitted
        assert result.truncated == bool(omitted)
        assert result.budget == result.requested_budget == budget
        assert result.tokens == count_tokens(result.patch) <= budget
        if status == "partial":
            assert result.detailed == ["m.py::small"]
            assert "+    return 2" in result.patch
        if status == "nothing_fits":
            assert result.patch == "" and result.detailed == []

    def test_raw_cap_is_reported_without_changing_policy(self):
        repo, changes = self.sample()
        result = material_patch(repo, "old", "new", changes, budget=10000)
        assert result.budget == count_tokens("tiny")
        assert result.requested_budget == 10000
        assert result.status == "nothing_fits"
        assert result.omitted == ["m.py::large", "m.py::small"]

    def test_empty_changes_are_complete_even_with_zero_budget(self):
        from gita import ChangeSet

        repo, _ = self.sample()
        result = material_patch(repo, "old", "new", ChangeSet(), budget=0)
        assert result.status == "complete"
        assert result.patch == "" and result.omitted == []


class TestRanking:
    def test_interface_changes_are_detailed_before_body_changes(self, repo):
        text = answer(repo, budget=20000).text
        detail = text.split("---", 1)[1] if "---" in text else text
        assert "handle" in detail


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

    def repo_with_many_entities(self, tmp_path, count, body_lines=1, cosmetic=0):
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        pad = "".join(f"    step_{j} = x + {j}\n" for j in range(body_lines))
        before = "".join(f"def fn_{i}(x):\n{pad}    return x + {i}\n\n"
                         for i in range(count))
        before += "".join(f"def note_{i}(x):\n{pad}    return x - {i}\n\n"
                          for i in range(cosmetic))
        (tmp_path / "m.py").write_text(before)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        after = "".join(f"def fn_{i}(x):\n{pad}    return x * {i}\n\n"
                        for i in range(count))
        # Comment-only edits: git prints them in full, gita filters them as
        # cosmetic. That gap is exactly where gita's headroom comes from.
        after += "".join(f"def note_{i}(x):\n    # explain step {i}\n{pad}"
                         f"    return x - {i}\n\n"
                         for i in range(cosmetic))
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
        """A notice on every answer is noise, and would train the agent past it.

        The raw diff is the cap, so a change with no noise in it leaves gita
        almost no room: this case used to fit in 175 of 176 tokens, and any
        addition at all -- entity line ranges, as it turned out -- spent the
        last of it and forced a hunk out. A one-token margin tests arithmetic,
        not the behaviour. The comment-only edits below are what gita is for:
        git prints them, gita drops them, and that gap is the headroom.
        """
        repo = self.repo_with_many_entities(tmp_path, 2, body_lines=6, cosmetic=2)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=DEFAULT_BUDGET)
        assert not answer.truncated
        assert "gita show" not in answer.text

    def test_it_does_not_offer_a_budget_that_cannot_help(self, tmp_path):
        """The raw diff caps the budget, so raising it buys a turn and nothing else.

        Generated by AI (UnitTest MCP).
        """
        repo = self.repo_with_many_entities(tmp_path, MAX_DETAILED + 12)
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw_tokens = count_tokens(repo.raw_diff("HEAD^", "HEAD", changeset.paths()))
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=raw_tokens * 2)
        assert answer.budget == raw_tokens, "raw diff should be the cap here"
        assert answer.tokens <= raw_tokens
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

    def test_holds_on_disposable_repository(self, repo):
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        raw = count_tokens(repo.raw_diff("HEAD^", "HEAD", changeset.paths()))
        for budget in (40, 120, 400, 1200, DEFAULT_BUDGET):
            answer = compose(repo, "HEAD^", "HEAD", changeset, budget=budget)
            assert answer.tokens <= raw, f"budget {budget} exceeded the raw diff"
            assert answer.tokens <= answer.budget

    @pytest.mark.parametrize("budget", [1, 7, 33, 91, 512])
    def test_awkward_budgets_are_still_respected(self, repo, budget):
        changeset = diff_revisions(repo, "HEAD^", "HEAD")
        answer = compose(repo, "HEAD^", "HEAD", changeset, budget=budget)
        assert answer.tokens <= answer.budget
