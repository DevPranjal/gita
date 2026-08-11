"""WS-4 context layers: clustering, ranking, rollup, and L0/L1/L2 assembly.

Written before the implementation. The behaviours asserted here are the spec.
"""

from __future__ import annotations

import subprocess

import pytest

from gita import ChangeKind, diff_trees, extract
from gita.context import (
    build_view,
    cluster_changes,
    count_tokens,
    entity_diff,
    expand,
    filtered_view,
    fit_lines,
    rollup_lines,
    score_change,
)
from gita.diff.changes import ChangeSet
from gita.vcs.git import Repo


def changes(previous: bytes, current: bytes, path: str = "m.py"):
    return diff_trees(extract(previous, path), extract(current, path))


def changeset(*pairs) -> ChangeSet:
    result = ChangeSet()
    for path, previous, current in pairs:
        result.files_changed += 1
        result.extend(changes(previous, current, path))
    return result


class TestTokens:
    def test_counts_are_positive(self):
        assert count_tokens("hello world") > 0

    def test_empty_string_is_zero(self):
        assert count_tokens("") == 0

    def test_deterministic(self):
        text = "src/app.py::Flask::handle  [body_changed]"
        assert count_tokens(text) == count_tokens(text)

    def test_longer_text_costs_more(self):
        assert count_tokens("a" * 400) > count_tokens("a")


class TestScoring:
    def base(self, kind: ChangeKind):
        pairs = {
            ChangeKind.SIGNATURE_CHANGED: (b"def f(a):\n    return a\n",
                                           b"def f(a, b):\n    return a\n"),
            ChangeKind.BODY_CHANGED: (b"def f(a):\n    return a\n",
                                      b"def f(a):\n    return a + 1\n"),
            ChangeKind.COSMETIC: (b"def f(a):\n    return a\n",
                                  b"def f( a ):\n    return  a\n"),
            ChangeKind.ADDED: (b"", b"def f(a):\n    return a\n"),
            ChangeKind.REMOVED: (b"def f(a):\n    return a\n", b""),
        }
        previous, current = pairs[kind]
        for change in changes(previous, current):
            if change.kind is kind:
                return change
        raise AssertionError(f"no {kind} produced")

    def test_interface_change_outranks_body_change(self):
        assert score_change(self.base(ChangeKind.SIGNATURE_CHANGED)) > \
               score_change(self.base(ChangeKind.BODY_CHANGED))

    def test_removal_outranks_cosmetic(self):
        assert score_change(self.base(ChangeKind.REMOVED)) > \
               score_change(self.base(ChangeKind.COSMETIC))

    def test_noise_scores_zero(self):
        assert score_change(self.base(ChangeKind.COSMETIC)) == 0

    def test_source_outranks_tests(self):
        source = changes(b"def f(a):\n    return a\n",
                         b"def f(a, b):\n    return a\n", "src/app.py")
        test = changes(b"def f(a):\n    return a\n",
                       b"def f(a, b):\n    return a\n", "tests/test_app.py")
        material = lambda cs: [c for c in cs if not c.is_noise][0]
        assert score_change(material(source)) > score_change(material(test))


class TestClustering:
    def test_siblings_group_under_their_top_level_entity(self):
        previous = b"class A:\n    def x(self):\n        return 1\n    def y(self):\n        return 2\n"
        current = b"class A:\n    def x(self):\n        return 9\n    def y(self):\n        return 8\n"
        clusters = cluster_changes(changes(previous, current))
        assert len(clusters) == 1
        assert clusters[0].title == "A"
        assert len(clusters[0].changes) == 2

    def test_separate_top_level_entities_are_separate_clusters(self):
        previous = b"def a():\n    return 1\n\n\ndef b():\n    return 2\n"
        current = b"def a():\n    return 9\n\n\ndef b():\n    return 8\n"
        assert len(cluster_changes(changes(previous, current))) == 2

    def test_noise_is_excluded(self):
        previous = b"def a(x,y):\n    return x+y\n"
        current = b"def a(x, y):\n    return x + y\n"
        assert cluster_changes(changes(previous, current)) == []

    def test_clusters_sort_by_score_descending(self):
        cs = changeset(
            ("tests/test_app.py", b"def t():\n    return 1\n", b"def t():\n    return 2\n"),
            ("src/app.py", b"def f(a):\n    return a\n", b"def f(a, b):\n    return a\n"),
        )
        clusters = cluster_changes(cs.changes)
        assert clusters[0].path == "src/app.py"

    def test_module_level_change_forms_its_own_cluster(self):
        previous = b"import os\n\n\ndef a():\n    return 1\n"
        current = b"import os\nimport sys\n\n\ndef a():\n    return 1\n"
        clusters = cluster_changes(changes(previous, current))
        assert [c.title for c in clusters] == ["<module>"]


class TestRollup:
    """Spike A finding 2: depth policy dominates compression."""

    NESTED = [
        "t.js::describe('R')::it('a')",
        "t.js::describe('R')::it('b')",
        "t.js::describe('R')::it('c')",
    ]

    def nested_changes(self):
        previous = (b"describe('R', () => {\n"
                    b"  it('a', () => { expect(1).toBe(1); });\n"
                    b"  it('b', () => { expect(2).toBe(2); });\n"
                    b"  it('c', () => { expect(3).toBe(3); });\n"
                    b"});\n")
        current = previous.replace(b"toBe(1)", b"toBe(9)") \
                          .replace(b"toBe(2)", b"toBe(8)") \
                          .replace(b"toBe(3)", b"toBe(7)")
        return [c for c in changes(previous, current, "t.js") if not c.is_noise]

    def test_depth_one_collapses_to_a_single_line(self):
        lines = rollup_lines(self.nested_changes(), depth=1)
        assert len(lines) == 1
        assert "nested" in lines[0]

    def test_full_depth_lists_every_entity(self):
        lines = rollup_lines(self.nested_changes(), depth=4)
        assert len(lines) >= 3

    def test_rolling_up_reduces_tokens(self):
        deep = "\n".join(rollup_lines(self.nested_changes(), depth=4))
        flat = "\n".join(rollup_lines(self.nested_changes(), depth=1))
        assert count_tokens(flat) < count_tokens(deep)


class TestTestChurnRollup:
    """Bulk test churn is context, not the answer.

    In `got-new-option` a new option was added alongside 100+ tests whose names
    all restate the option. Listing each one buried the API change under 20
    near-identical lines and the agent went back to reading files by hand.
    """

    def bulk(self, files: int = 6) -> ChangeSet:
        source = ("source/options.ts",
                  b"export const defaults = {retry: 2};\n",
                  b"export const defaults = {retry: 2, allowAbsoluteUrls: true};\n")
        pairs = [source]
        for index in range(files):
            current = (
                "describe('r', () => {\n"
                f"  it('allowAbsoluteUrls case {index} rejects an absolute URL',"
                " () => { expect(1).toBe(1); });\n"
                "});\n"
            ).encode()
            pairs.append((f"test/case{index}.ts", b"describe('r', () => {\n});\n", current))
        return changeset(*pairs)

    def test_each_test_file_collapses_to_one_line(self):
        lines = rollup_lines(self.bulk().material(), depth=6)
        test_lines = [line for line in lines if line.startswith("test/")]
        assert len(test_lines) == 6

    def test_the_source_change_is_listed_before_test_churn(self):
        lines = rollup_lines(self.bulk().material(), depth=6)
        assert lines[0].startswith("source/options.ts")

    def test_rolled_up_line_says_how_many_and_what_kind(self):
        line = next(line for line in rollup_lines(self.bulk().material(), depth=6)
                    if line.startswith("test/case0.ts"))
        assert "tests:" in line and "1 added" in line

    def test_a_test_only_change_is_still_listed_in_full(self):
        """When tests are the whole story, rolling them up would hide it."""
        pairs = [(f"test/case{i}.ts", b"describe('r', () => {\n});\n",
                  ("describe('r', () => {\n"
                   f"  it('case {i}', () => {{ expect(1).toBe(1); }});\n"
                   "});\n").encode()) for i in range(6)]
        lines = rollup_lines(changeset(*pairs).material(), depth=6)
        assert any("::" in line for line in lines)

    def test_a_handful_of_tests_is_not_worth_rolling_up(self):
        lines = rollup_lines(self.bulk(files=2).material(), depth=6)
        assert any(line.startswith("test/") and "::" in line for line in lines)


class TestFit:
    nested_changes = TestRollup.nested_changes

    def test_fit_respects_a_tight_budget(self):
        lines, _ = fit_lines(self.nested_changes(), budget=12)
        assert count_tokens("\n".join(lines)) <= 12

    def test_fit_uses_depth_when_budget_allows(self):
        tight, tight_depth = fit_lines(self.nested_changes(), budget=12)
        loose, loose_depth = fit_lines(self.nested_changes(), budget=4000)
        assert loose_depth >= tight_depth
        assert len(loose) >= len(tight)

    def test_impossible_budget_still_terminates(self):
        lines, _ = fit_lines(self.nested_changes(), budget=1)
        assert isinstance(lines, list)


class TestLayers:
    def sample(self) -> ChangeSet:
        return changeset(
            ("src/app.py",
             b"def handle(req):\n    return req\n",
             b"def handle(req, ctx):\n    return req\n\n\ndef boot():\n    return 1\n"),
            ("tests/test_app.py",
             b"def test_handle():\n    assert handle(1)\n",
             b"def test_handle():\n    assert handle(1, 2)\n"),
        )

    def test_l0_reports_totals(self):
        view = build_view(self.sample(), budget=800)
        assert "2 files" in view.l0
        assert view.l0.count("\n") <= 6, "L0 must stay a headline"

    def test_l0_is_far_cheaper_than_l1(self):
        view = build_view(self.sample(), budget=800)
        assert count_tokens(view.l0) < count_tokens(view.l1)

    def test_l1_lists_changed_entities(self):
        view = build_view(self.sample(), budget=800)
        assert "handle" in view.l1
        assert "boot" in view.l1

    def test_l1_respects_budget(self):
        view = build_view(self.sample(), budget=40)
        assert view.tokens <= 40
        assert view.truncated

    def test_generous_budget_is_not_truncated(self):
        view = build_view(self.sample(), budget=4000)
        assert not view.truncated

    def test_interface_changes_are_surfaced_first(self):
        view = build_view(self.sample(), budget=800)
        assert view.l1.index("src/app.py") < view.l1.index("tests/test_app.py")

    def test_empty_changeset_is_handled(self):
        view = build_view(ChangeSet(), budget=800)
        assert view.clusters == []
        assert "nothing to compare" in view.l0.lower()

    def test_empty_diff_is_distinguished_from_all_noise(self):
        """An ambiguous "no material changes" hid a harness bug for a whole run."""
        noisy = changeset(("m.py", b"def a(x,y):\n    return x+y\n",
                           b"def a(x, y):\n    return x + y\n"))
        assert "all noise" in build_view(noisy, budget=800).l0.lower()
        assert "nothing to compare" in build_view(ChangeSet(), budget=800).l0.lower()


@pytest.fixture
def tiny_repo(tmp_path):
    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "m.py").write_bytes(
        b"def keep():\n    return 1\n\n\ndef target(a):\n    total = 0\n    return total\n")
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("add", "-A")
    run("commit", "-q", "-m", "first")

    (tmp_path / "m.py").write_bytes(
        b"def keep():\n    return 1\n\n\ndef target(a):\n    total = a * 2\n    return total\n")
    run("add", "-A")
    run("commit", "-q", "-m", "second")
    return Repo(tmp_path)


class TestL2:
    def test_entity_diff_is_scoped_to_one_entity(self, tiny_repo):
        patch = entity_diff(tiny_repo, "HEAD^", "HEAD", "m.py::target")
        assert "total = a * 2" in patch
        assert "def keep" not in patch

    def test_entity_diff_is_a_unified_diff(self, tiny_repo):
        patch = entity_diff(tiny_repo, "HEAD^", "HEAD", "m.py::target")
        assert patch.startswith("---") or patch.startswith("@@") or "@@" in patch

    def test_unknown_entity_returns_empty(self, tiny_repo):
        assert entity_diff(tiny_repo, "HEAD^", "HEAD", "m.py::nope") == ""

    def test_l2_costs_more_than_l1_for_the_same_entity(self, tiny_repo):
        from gita import diff_revisions

        view = build_view(diff_revisions(tiny_repo, "HEAD^", "HEAD"), budget=800)
        patch = entity_diff(tiny_repo, "HEAD^", "HEAD", "m.py::target")
        assert count_tokens(patch) > count_tokens(view.l0)

    def test_a_shared_cache_reads_each_revision_once(self, tiny_repo):
        """A large diff asks for many entities from one file.

        Re-reading and re-parsing per entity made the biggest task in the corpus
        a ten-second call, and a ten-second call is one an agent works around.
        """
        reads: list[tuple] = []
        original = tiny_repo.blob
        tiny_repo.blob = lambda rev, path: (reads.append((rev, path)),
                                            original(rev, path))[1]
        cache: dict = {}
        for entity_id in ("m.py::target", "m.py::keep", "m.py::target"):
            entity_diff(tiny_repo, "HEAD^", "HEAD", entity_id, cache=cache)
        assert len(reads) == 2  # one per revision, not one per entity


def sample_changeset() -> ChangeSet:
    return changeset(
        ("src/app.py",
         b"def handle(req):\n    return req\n",
         b"def handle(req, ctx):\n    return req\n\n\ndef boot():\n    return 1\n"),
        ("tests/test_app.py",
         b"def test_handle():\n    assert handle(1)\n",
         b"def test_handle():\n    assert handle(1, 2)\n"),
    )


def nested_changes():
    previous = (b"describe('R', () => {\n"
                b"  it('a', () => { expect(1).toBe(1); });\n"
                b"  it('b', () => { expect(2).toBe(2); });\n"
                b"  it('c', () => { expect(3).toBe(3); });\n"
                b"});\n")
    current = previous.replace(b"toBe(1)", b"toBe(9)") \
                      .replace(b"toBe(2)", b"toBe(8)") \
                      .replace(b"toBe(3)", b"toBe(7)")
    return changes(previous, current, "t.js")


class TestBudgetContract:
    """A budget an agent cannot rely on is not a budget."""

    @pytest.mark.parametrize("budget", [0, 1, 3, 8, 15, 40, 200, 2000])
    def test_view_never_exceeds_its_budget(self, budget):
        assert build_view(sample_changeset(), budget=budget).tokens <= budget

    def test_zero_budget_produces_nothing(self):
        view = build_view(sample_changeset(), budget=0)
        assert view.tokens == 0
        assert view.truncated

    def test_a_tiny_budget_still_says_something(self):
        view = build_view(sample_changeset(), budget=15)
        assert view.l0.strip()

    def test_empty_changeset_respects_budget(self):
        assert build_view(ChangeSet(), budget=2).tokens <= 2


class TestExpand:
    """The middle of the protocol: L1 rolls up, expand drills into one line."""

    def test_expands_children_of_a_rolled_entity(self):
        lines = expand(nested_changes(), "t.js::describe('R')", budget=400)
        assert len(lines) == 3
        assert any("it('a')" in line for line in lines)

    def test_parent_itself_is_not_repeated(self):
        lines = expand(nested_changes(), "t.js::describe('R')", budget=400)
        assert not any(line.endswith("describe('R')") for line in lines)

    def test_unknown_entity_expands_to_nothing(self):
        assert expand(nested_changes(), "t.js::nope", budget=400) == []

    def test_expansion_respects_budget(self):
        lines = expand(nested_changes(), "t.js::describe('R')", budget=5)
        assert count_tokens("\n".join(lines)) <= 5

    def test_accepts_a_changeset(self):
        cs = ChangeSet()
        cs.extend(nested_changes())
        assert expand(cs, "t.js::describe('R')", budget=400)


class TestFiltering:
    """Filtering is exact. Question answering is deliberately absent until WS-3."""

    def test_filter_narrows_to_matching_entities(self):
        view = filtered_view(sample_changeset(), term="boot", budget=800)
        assert "boot" in view.l1
        assert "test_handle" not in view.l1

    def test_interface_only_uses_computed_facts_not_keywords(self):
        view = filtered_view(sample_changeset(), interface_only=True, budget=800)
        assert "handle" in view.l1

    def test_an_unmatched_filter_stays_empty(self):
        view = filtered_view(sample_changeset(), term="kubernetes", budget=800)
        assert view.l1.strip() == ""

    def test_filter_is_recorded_in_the_headline(self):
        view = filtered_view(sample_changeset(), term="boot", budget=800)
        assert "boot" in view.l0

    @pytest.mark.parametrize("budget", [0, 5, 30, 800])
    def test_filtered_view_respects_budget(self, budget):
        assert filtered_view(sample_changeset(), term="boot",
                             budget=budget).tokens <= budget

    def test_no_filter_behaves_like_a_plain_view(self):
        plain = build_view(sample_changeset(), budget=800)
        assert filtered_view(sample_changeset(), budget=800).l1 == plain.l1

    def test_path_terms_match_too(self):
        view = filtered_view(sample_changeset(), term="tests/", budget=800)
        assert "tests/test_app.py" in view.l1
        assert "src/app.py" not in view.l1
