"""WS-4 context layers: clustering, ranking, rollup, and L0/L1/L2 assembly.

Written before the implementation. The behaviours asserted here are the spec.
"""

from __future__ import annotations

import subprocess

import pytest

from gita import ChangeKind, diff_trees, extract
from gita.context import (
    Intent,
    build_view,
    cluster_changes,
    count_tokens,
    entity_diff,
    expand,
    filtered_view,
    fit_lines,
    line_span,
    rollup_lines,
    score_change,
)
from gita.context import tokens
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


class TestChangeRendering:
    def test_prefixes_change_kind_in_summary_and_detail(self, tiny_repo):
        from gita.context.answer import _detail
        from gita import diff_revisions

        added = next(c for c in diff_trees(
            None, extract(b"def added():\n    return 1\n", "added.py"))
            if c.kind is ChangeKind.ADDED)
        change = diff_revisions(tiny_repo, "HEAD^", "HEAD").material()[0]

        assert str(added).startswith("added added.py")
        assert _detail(tiny_repo, "HEAD^", "HEAD", change, {}) \
            .startswith(f"--- body_changed {change.entity.id}\n")


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

    def test_reuses_a_file_path_for_its_nested_entities(self):
        lines = rollup_lines(self.nested_changes(), depth=4, group_paths=True)

        assert len(lines) == 1
        assert lines[0].startswith("t.js\n  ")


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
        test_lines = [line for line in lines if "case" in line]
        assert len(test_lines) == 6

    def test_the_source_change_is_listed_before_test_churn(self):
        lines = rollup_lines(self.bulk().material(), depth=6)
        assert "options.ts" in lines[0]

    def test_rolled_up_line_says_how_many_and_what_kind(self):
        line = next(line for line in rollup_lines(self.bulk().material(), depth=6)
                    if "case0.ts" in line)
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
        assert any("case" in line and "::" in line for line in lines)


class TestParallelFileRollup:
    """The same edit repeated across sibling files is one fact, not N.

    On `flask-dependency-update` an isort block added to four `examples/*/
    pyproject.toml` files produced 25 mentions of `pyproject.toml` against two
    of `uv.lock`. One repetition drowned out the other half of the answer, and
    a repetition scored recall 0.5 by naming only the loud one.
    """

    def parallel(self, files: int = 4) -> ChangeSet:
        before = b"[tool.ruff]\nline-length = 88\n"
        after = (b"[tool.ruff]\nline-length = 88\n\n"
                 b"[tool.ruff.lint.isort]\nforce-single-line = true\n")
        pairs = [(f"examples/app{i}/pyproject.toml", before, after)
                 for i in range(files)]
        pairs.append(("lockfile.toml", b"[package]\nflask = \"3.0\"\n",
                      b"[package]\nflask = \"3.1\"\n"))
        return changeset(*pairs)

    def test_the_repeated_change_is_stated_once(self):
        """Once per entity, not once per file."""
        lines = rollup_lines(self.parallel().material(), depth=6)
        isort = [line for line in lines if "isort" in line]
        assert all("in 4 files" in line for line in isort)
        assert len(isort) < 4

    def test_it_says_how_many_files_and_names_them(self):
        line = next(line for line in rollup_lines(self.parallel().material(), depth=6)
                    if "isort" in line)
        assert "4 files" in line
        assert "examples/app0/pyproject.toml" in line

    def test_the_quiet_file_is_not_crowded_out(self):
        lines = rollup_lines(self.parallel().material(), depth=6)
        assert any("lockfile.toml" in line for line in lines)

    def test_two_files_are_not_a_pattern(self):
        """Grouping a pair hides as much as it saves."""
        lines = rollup_lines(self.parallel(files=2).material(), depth=6)
        assert len([line for line in lines if "isort" in line]) >= 2

    def test_grouping_costs_fewer_tokens(self):
        grouped = "\n".join(rollup_lines(self.parallel(files=6).material(), depth=6))
        singly = "\n".join(rollup_lines(self.parallel(files=2).material(), depth=6))
        assert count_tokens(grouped) < count_tokens(singly) * 3


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

    def test_fitted_rows_retain_group_membership_without_changing_text(self):
        from gita.context.rollup import fit_rows

        changes = self.nested_changes()
        for budget in (0, 20, 80, 4000):
            rows, depth = fit_rows(changes, budget, group_paths=True)
            lines, old_depth = fit_lines(changes, budget, group_paths=True)
            assert [row.text for row in rows] == lines
            assert depth == old_depth
            assert all(row.entity_ids for row in rows)
        rows, _ = fit_rows(changes, 4000, group_paths=True)
        assert {key for row in rows for key in row.entity_ids} == {
            c.id for c in changes if not c.is_noise}

    def test_parallel_rollups_retain_even_unprinted_file_members(self):
        from gita.context.rollup import fit_rows

        changes = TestParallelFileRollup().parallel(files=12).material()
        rows, _ = fit_rows(changes, 10000)
        assert any("more" in row.text for row in rows)
        assert {key for row in rows for key in row.entity_ids} == {c.id for c in changes}


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
        assert count_tokens(view.l0) <= count_tokens(view.l1)

    def test_l1_lists_changed_entities(self):
        view = build_view(self.sample(), budget=800)
        assert "handle" in view.l1
        assert "boot" in view.l1

    def test_l1_respects_budget(self):
        view = build_view(self.sample(), budget=40)
        assert view.tokens <= 40

    def test_generous_budget_is_not_truncated(self):
        view = build_view(self.sample(), budget=4000)
        assert not view.truncated

    def test_shallow_summary_covering_every_entity_is_not_truncated(self):
        from gita.context.layers import _headline
        from gita.context.cluster import cluster_changes

        changes = ChangeSet(TestRollup().nested_changes(), files_changed=1)
        material = changes.material()
        headline = _headline(changes, material, cluster_changes(list(changes)), 1000)
        shallow = rollup_lines(material, 1, spans=False)
        budget = count_tokens(headline) + count_tokens("\n".join(shallow))
        view = build_view(changes, budget=budget)
        assert not view.truncated
        assert view.tokens <= budget

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
    @pytest.mark.parametrize("head", [None, "STAGED", "HEAD"])
    def test_checkout_line_endings_do_not_inflate_entity_patches(self, tmp_path, head):
        from gita import diff_revisions
        from gita.context.answer import material_patch
        from gita.vcs.git import STAGED

        def git(*args):
            return subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                                  capture_output=True).stdout.decode().strip()

        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        git("config", "core.autocrlf", "true")
        git("config", "core.safecrlf", "false")
        source = b"export function allowed() {\n  return true;\n}\n"
        (tmp_path / "m.ts").write_bytes(source)
        git("add", "m.ts")
        git("commit", "-qm", "before")
        base = git("rev-parse", "HEAD")
        (tmp_path / "m.ts").write_bytes(
            source.replace(b"true", b"false").replace(b"\n", b"\r\n"))
        revision = STAGED if head == "STAGED" else head
        if revision is not None:
            git("add", "m.ts")
        if revision == "HEAD":
            git("commit", "-qm", "after")

        repo = Repo(tmp_path)
        assert b"\r\n" not in repo.blob(base, "m.ts")
        assert b"\r\n" in repo.blob(None, "m.ts")
        patch = material_patch(repo, base, revision,
                               diff_revisions(repo, base, revision)).patch
        assert "-  return true;" in patch and "+  return false;" in patch
        changed = [line for line in patch.splitlines()
                   if line.startswith(("+", "-")) and not line.startswith("---")]
        assert len(changed) == 2

    def test_added_numbered_sibling_has_no_old_hunk(self, tmp_path):
        from gita import diff_revisions
        from gita.context.patch import change_diff

        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)

        before = (
            "const first = useCallback((_e, next) => {\n"
            "  return next;\n"
            "}, []);\n"
            "const second = useCallback((_e, next) => {\n"
            "  return next + 1;\n"
            "}, []);\n"
        )
        after = (
            "const handleExport = useCallback(async () => {\n"
            "  return exportWorkbook();\n"
            "}, []);\n"
            + before
        )
        (tmp_path / "callbacks.tsx").write_text(before)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        (tmp_path / "callbacks.tsx").write_text(after)
        git("add", "-A")
        git("commit", "-q", "-m", "second")

        repo = Repo(tmp_path)
        change = next(c for c in diff_revisions(repo, "HEAD^", "HEAD").material()
                      if c.kind is ChangeKind.ADDED and "useCallback" in c.entity.id)
        patch = change_diff(repo, "HEAD^", "HEAD", change)

        assert "handleExport" in patch
        assert "return next;" not in patch
        assert "return next + 1;" not in patch

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


class TestLineLocations:
    """Naming an entity says what changed; the range says where to look.

    Without it the agent has to search the file for a name it was just given,
    and the hunk headers were worse than silent: difflib numbers from 1 within
    the slice it is handed, so an entity at line 5 reported `@@ -1,3 +1,3 @@`
    and pointed at the top of the file.
    """

    #: `keep` occupies lines 1-2, `target` lines 5-7.
    SOURCE = (b"def keep():\n    return 1\n\n\n"
              b"def target(a):\n    total = 0\n    return total\n")

    @staticmethod
    def commit(tmp_path, before: str, after: str) -> Repo:
        def git(*args):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                           capture_output=True)
        (tmp_path / "m.py").write_text(before)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "first")
        (tmp_path / "m.py").write_text(after)
        git("add", "-A")
        git("commit", "-q", "-m", "second")
        return Repo(tmp_path)

    def edited(self) -> bytes:
        return self.SOURCE.replace(b"total = 0", b"total = a * 2")

    def line_for(self, current: bytes, name: str) -> str:
        return next(line for line in rollup_lines(changes(self.SOURCE, current),
                                                  depth=6)
                    if name in line)

    def test_a_summary_line_carries_the_range(self):
        assert "L5-7" in self.line_for(self.edited(), "target")

    def test_the_range_is_the_position_in_the_file_not_in_the_entity(self):
        """The bug this exists to prevent: a range that restarts per entity."""
        line = self.line_for(self.edited(), "target")
        assert "L1-" not in line

    def test_a_removed_entity_says_which_revision_it_lived_in(self):
        """Its lines are gone, so a bare range would point at whatever is there now."""
        gone = b"def keep():\n    return 1\n"
        assert "L5-7 (before)" in self.line_for(gone, "target")

    def test_a_single_line_entity_is_not_written_as_a_range(self):
        one_liner = [c for c in changes(b"def f(): return 1\n",
                                        b"def f(): return 2\n")
                     if not c.is_noise and c.entity.id.endswith("::f")]
        assert line_span(one_liner).strip() == "L1"

    def test_hunk_headers_are_absolute_file_positions(self, tmp_path):
        repo = self.commit(tmp_path, self.SOURCE.decode(),
                           self.edited().decode())
        patch = entity_diff(repo, "HEAD^", "HEAD", "m.py::target")
        header = next(line for line in patch.splitlines()
                      if line.startswith("@@"))
        assert "-5" in header and "+5" in header

    def test_change_diff_hunk_headers_are_absolute_file_positions(self, tmp_path):
        from gita import diff_revisions
        from gita.context.patch import change_diff

        repo = self.commit(tmp_path, self.SOURCE.decode(),
                           self.edited().decode())
        change = next(change for change in diff_revisions(repo, "HEAD^", "HEAD").material()
                      if change.entity.id == "m.py::target")
        header = next(line for line in change_diff(repo, "HEAD^", "HEAD", change).splitlines()
                      if line.startswith("@@"))
        assert "-5" in header and "+5" in header

    def test_an_added_entity_keeps_its_empty_side_empty(self, tmp_path):
        """`-0,0` is unified-diff for "nothing here", not a position to shift."""
        repo = self.commit(
            tmp_path,
            "def keep():\n    return 1\n",
            "def keep():\n    return 1\n\n\ndef fresh():\n    return 2\n")
        patch = entity_diff(repo, "HEAD^", "HEAD", "m.py::fresh")
        header = next(line for line in patch.splitlines()
                      if line.startswith("@@"))
        assert "-0,0" in header
        assert "+5" in header


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


class TestBudgetHoldsWithoutTiktoken:
    """`--budget N` is documented as a hard cap, and a cap cannot be honoured by
    an estimate that guesses low.

    tiktoken was an optional extra, so a plain `pip install gita` produced a
    build where budget 60 emitted 79 real tokens. Measured over 59 corpus files,
    the old divisor of 4.0 chars/token underestimated 61% of the time.
    """

    def sample(self):
        return ("def handle(request, timeout=5):\n"
                "    result = fetch(request.url, timeout=timeout)\n"
                "    return result.json()\n") * 6

    def test_the_estimate_never_guesses_low(self):
        text = self.sample()
        exact = tokens.count_tokens(text)
        estimated = tokens.estimate_tokens(text)
        assert estimated >= exact, "an estimate used as a cap must err high"

    def test_it_does_not_err_absurdly_high(self):
        """Conservative, not useless: answers would shrink to nothing."""
        text = self.sample()
        assert tokens.estimate_tokens(text) <= tokens.count_tokens(text) * 3

    @pytest.mark.parametrize("text", [
        "x = 1\n",
        "{\"a\": [1, 2, 3], \"b\": {\"c\": true}}",
        "# Heading\n\nSome prose about the change.\n",
        "func F(a int) (int, error) { return a + 1, nil }\n",
    ])
    def test_holds_across_content_shapes(self, text):
        assert tokens.estimate_tokens(text) >= tokens.count_tokens(text)

    def test_the_method_in_use_is_reportable(self):
        """An agent must be able to tell an exact budget from an approximate one."""
        assert tokens.token_method() in ("tiktoken/cl100k_base", "approx")


class TestIntentParsing:
    """Intent comes from text the caller already has, never from the diff."""

    def test_drops_request_scaffolding(self):
        assert Intent.parse("please fix the bug in the parser").terms == ("parser",)

    def test_keeps_domain_words_in_order(self):
        assert Intent.parse("cache eviction in the token store").terms == (
            "cache", "eviction", "token", "store")

    def test_splits_identifiers_and_camel_case(self):
        assert Intent.parse("parseTokens in parser_state.py").terms == (
            "parse", "tokens", "parser", "state")

    def test_repeated_words_count_once(self):
        assert Intent.parse("parser parser parser").terms == ("parser",)

    def test_empty_and_contentless_intents_are_falsy(self):
        assert not Intent.parse("")
        assert not Intent.parse(None)
        assert not Intent.parse("please fix the bug")


class TestIntentRanking:
    """Ranking answers "what is this agent here for", not only "what is severe"."""

    def one(self, path, previous, current, kind=None):
        material = [c for c in changes(previous, current, path) if not c.is_noise]
        if kind is not None:
            material = [c for c in material if c.kind is kind]
        assert material, f"no material change produced for {path}"
        return material[0]

    def body_change(self, path="src/parser.py", name="parse_tokens"):
        return self.one(path,
                        f"def {name}(a):\n    return a\n".encode(),
                        f"def {name}(a):\n    return a + 1\n".encode(),
                        ChangeKind.BODY_CHANGED)

    def signature_change(self, path="src/other.py", name="unrelated_helper"):
        return self.one(path,
                        f"def {name}(a):\n    return a\n".encode(),
                        f"def {name}(a, b):\n    return a\n".encode(),
                        ChangeKind.SIGNATURE_CHANGED)

    def test_absent_intent_scores_exactly_as_before(self):
        change = self.body_change()
        assert score_change(change, Intent()) == score_change(change)
        assert score_change(change, None) == score_change(change)

    def test_an_intent_matching_nothing_changes_nothing(self):
        change = self.body_change()
        unrelated = Intent.parse("kubernetes ingress certificates")
        assert score_change(change, unrelated) == score_change(change)

    def test_the_named_entity_outranks_a_severer_unrelated_change(self):
        named = self.body_change()
        severe = self.signature_change()
        assert score_change(named) < score_change(severe), "baseline assumption"

        intent = Intent.parse("parse_tokens returns the wrong count")
        assert score_change(named, intent) > score_change(severe, intent)

    def test_a_path_match_alone_cannot_outrank_a_signature_change(self):
        """Naming the area is weaker evidence than naming the thing."""
        body = self.body_change(path="src/parser.py", name="unrelated_name")
        severe = self.signature_change()
        intent = Intent.parse("something in src/parser.py")
        assert score_change(body, intent) < score_change(severe, intent)

    def test_intent_never_revives_filtered_noise(self):
        cosmetic = [c for c in changes(b"def parse_tokens(a):\n    return a\n",
                                       b"def parse_tokens( a ):\n    return  a\n",
                                       "src/parser.py") if c.is_noise]
        assert cosmetic, "expected a cosmetic change"
        assert score_change(cosmetic[0], Intent.parse("parse_tokens")) == 0.0

    def test_a_matching_test_file_is_not_demoted(self):
        """"Why is this test failing" is a request about tests."""
        test = self.signature_change(path="tests/test_helper.py", name="helper")
        source = self.signature_change(path="src/other.py", name="unrelated")
        assert score_change(test) < score_change(source), "baseline assumption"

        intent = Intent.parse("the helper assertion is wrong")
        assert score_change(test, intent) > score_change(source, intent)

    def test_intent_reorders_the_rolled_up_lines(self):
        material = [self.body_change(), self.signature_change()]

        plain = rollup_lines(material, 1)
        assert "other.py" in plain[0]

        guided = rollup_lines(material, 1, Intent.parse("parse_tokens is wrong"))
        assert "parser.py" in guided[0]

    def test_intent_reorders_clusters(self):
        material = [self.body_change(), self.signature_change()]

        assert cluster_changes(material)[0].path == "src/other.py"
        guided = cluster_changes(material, Intent.parse("parse_tokens is wrong"))
        assert guided[0].path == "src/parser.py"

    def test_reordering_hides_nothing(self):
        material = [self.body_change(), self.signature_change()]
        intent = Intent.parse("parse_tokens is wrong")
        assert sorted(rollup_lines(material, 1)) == \
               sorted(rollup_lines(material, 1, intent))

    def test_a_named_test_escapes_the_bulk_test_rollup(self):
        """Bulk-test rollup runs before ranking, so it needs the exemption too.

        Without it, an intent could raise a test's score and still have that
        test summarised away into a count.
        """
        material = [self.signature_change(path="src/app.py", name="handler")]
        material += [self.body_change(path="tests/test_app.py", name=f"case_{i}")
                     for i in range(5)]
        material.append(self.signature_change(path="tests/test_helper.py",
                                              name="helper"))

        plain = rollup_lines(material, 1)
        assert not any("::helper" in line for line in plain), \
            "baseline assumption: bulk rollup summarises it away"

        guided = rollup_lines(material, 1, Intent.parse("the helper assertion is wrong"))
        assert any("::helper" in line for line in guided)

    def test_unnamed_test_churn_is_still_rolled_up(self):
        """The exemption is for what the request named, not for every test."""
        material = [self.signature_change(path="src/app.py", name="handler")]
        material += [self.body_change(path="tests/test_app.py", name=f"case_{i}")
                     for i in range(5)]

        guided = rollup_lines(material, 1, Intent.parse("the handler is wrong"))
        assert any(line.startswith("tests/test_app.py  (5 tests") for line in guided)
