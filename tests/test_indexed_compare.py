"""Indexed compare: the strict engine answering the `what changed` question.

Written against the contract, not the implementation: these assert that the
indexed path produces the same vocabulary the context layer consumes, and that
it refuses rather than guesses when it cannot serve a request.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from gita.diff.changes import ChangeKind
from gita.index import compare as indexed_compare


def _occurrence(path, names, start=1, end=5, definition="def1_x"):
    return {"path": path, "names": list(names), "definition_id": definition,
            "span": {"start_byte": 0, "end_byte": 10,
                     "start_line": start, "end_line": end}}


class TestEventClassification:
    """Indexed event flags carry more detail than the context vocabulary, so
    the mapping has to choose, and the choice is what a caller needs first."""

    def kind(self, flags, source=True, destination=True):
        event = {"flags": tuple(flags), "material": True,
                 "source": _occurrence("a.py", ["f"]) if source else None,
                 "destination": _occurrence("a.py", ["f"]) if destination else None}
        return indexed_compare._classify(event)

    def test_birth_is_added(self):
        assert self.kind([], source=False) is ChangeKind.ADDED

    def test_departure_is_removed(self):
        assert self.kind([], destination=False) is ChangeKind.REMOVED

    def test_rename_outranks_a_body_edit(self):
        """A caller needs the name change first; the body edit is secondary."""
        assert self.kind(["renamed", "body_changed"]) is ChangeKind.RENAMED

    def test_move_outranks_a_body_edit(self):
        assert self.kind(["moved", "body_changed"]) is ChangeKind.MOVED

    def test_signature_change_outranks_body(self):
        assert self.kind(["signature_changed", "body_changed"]) \
            is ChangeKind.SIGNATURE_CHANGED

    def test_own_content_counts_as_a_body_change(self):
        assert self.kind(["own_content_changed"]) is ChangeKind.BODY_CHANGED

    def test_descendant_change_counts_as_a_body_change(self):
        assert self.kind(["descendant_changed"]) is ChangeKind.BODY_CHANGED

    def test_cosmetic_stays_noise(self):
        assert self.kind(["cosmetic"]) is ChangeKind.COSMETIC

    def test_mode_change_alone_is_noise(self):
        """A permission bit is not a change to the code."""
        assert self.kind(["mode_changed"]) is ChangeKind.COSMETIC

    def test_no_flags_is_unchanged(self):
        assert self.kind([]) is ChangeKind.UNCHANGED

    def test_an_unknown_flag_does_not_become_a_body_change(self):
        """A flag this mapping has never seen must not be silently folded into
        a familiar kind -- that would report a fact nobody established."""
        assert self.kind(["representation_unknown"]) is not ChangeKind.BODY_CHANGED


class TestSimilarityIsNeverReported:
    def test_indexed_changes_carry_no_similarity_score(self):
        """The strict engine does not accept similarity as identity, so there
        is no score behind its pairings and inventing one would misdescribe how
        the match was made."""
        class _Version:
            id = "ver1_x"
            source_digest = b"\x01\x02"
            signature = ("def", "f")
            body = ("return",)

        class _Definition:
            kind = "function"
            names = ("f",)
            parent_id = None
            synthetic_root = False

        class _Session:
            def _definition(self, identifier):
                return _Definition(), _Version()

        event = {"flags": ("body_changed",), "material": True,
                 "source": _occurrence("a.py", ["f"]),
                 "destination": _occurrence("a.py", ["f"])}
        change = indexed_compare._change(_Session(), event)
        assert change.similarity is None
        assert change.kind is ChangeKind.BODY_CHANGED
        assert change.entity.path == "a.py"
        assert change.entity.id == "a.py::f"


class TestCoverageIsSurfaced:
    """Indexed compare inherits the index's completeness. A partial index that
    reports a departure and a birth where a complete one would report an edit
    must say so, or the tool invents a story out of its own gaps."""

    def test_partial_coverage_produces_a_warning(self):
        warning = indexed_compare.coverage_warning(
            {"status": "partial", "gaps": 27})
        assert "partial" in warning and "27" in warning
        assert "removed and added" in warning

    def test_exclusions_alone_are_silent(self):
        """An index that skipped a LICENSE is honestly partial, but nothing
        about that can turn an edit into a delete plus an add. Warning anyway
        fired on every real repository and taught a reader to skip the line."""
        assert indexed_compare.coverage_warning(
            {"status": "partial", "gaps": 0, "uncovered": 27, "excluded": 15}) == ""

    def test_a_real_gap_still_warns_even_beside_exclusions(self):
        """The counterweight: silence must come from there being no gap, not
        from the exclusions being large enough to look reassuring."""
        warning = indexed_compare.coverage_warning(
            {"status": "partial", "gaps": 2, "uncovered": 27, "excluded": 15})
        assert "2 gaps" in warning and "removed and added" in warning

    def test_complete_coverage_is_silent(self):
        assert indexed_compare.coverage_warning({"status": "complete"}) == ""

    def test_absent_coverage_is_silent(self):
        assert indexed_compare.coverage_warning({}) == ""


class TestIndexedDiffRefusesRatherThanGuesses:
    """The two engines disagree about identity by design, so a request the
    indexed engine cannot serve must fail visibly rather than fall through to
    the other one under the same flag."""

    def _repo(self, tmp_path):
        def git(*args):
            # -c rather than relying on ambient config: Git for Windows ships
            # core.safecrlf=true at system level, which makes `git add` refuse
            # the LF bytes written below.
            subprocess.run(["git", "-C", str(tmp_path),
                            "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
                            *args], check=True, capture_output=True)
        (tmp_path / "app.py").write_bytes(b"def handle(x):\n    return x\n")
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-q", "-m", "one")
        (tmp_path / "app.py").write_bytes(b"def handle(x, y):\n    return x\n")
        git("add", "-A")
        git("commit", "-q", "-m", "two")
        return tmp_path

    def test_worktree_mode_is_refused(self, tmp_path, capsys):
        from gita.cli import main

        repo = self._repo(tmp_path)
        code = main(["-C", str(repo), "diff", "--indexed"])
        assert code == 3
        assert "cannot see uncommitted work" in capsys.readouterr().out

    def test_missing_index_names_the_command_that_fixes_it(self, tmp_path, capsys):
        from gita.cli import main

        repo = self._repo(tmp_path)
        code = main(["-C", str(repo), "diff", "HEAD~1", "HEAD", "--indexed"])
        assert code == 3
        out = capsys.readouterr().out
        assert "no index" in out
        assert "gita index" in out, "a refusal without a route wastes a turn"

    def test_the_legacy_engine_still_answers_without_an_index(self, tmp_path):
        from gita.cli import main

        repo = self._repo(tmp_path)
        assert main(["-C", str(repo), "diff", "HEAD~1", "HEAD", "--brief"]) == 0

    def test_indexed_diff_preserves_intent_and_engine_metadata(self, tmp_path, capsys):
        """Generated by AI (UnitTest MCP)."""
        from gita.cli import main

        repo = self._repo(tmp_path)
        assert main(["-C", str(repo), "index", "--scope", "review"]) == 0
        capsys.readouterr()

        assert main(["-C", str(repo), "diff", "HEAD~1", "HEAD", "--indexed",
                     "--scope", "review", "--intent", "handle", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["engine"] == "indexed"
        assert payload["coverage"] is not None
        assert payload["intent"] == "handle"
        assert payload["intent_terms"] == ["handle"]
        assert "handle" in payload["text"]