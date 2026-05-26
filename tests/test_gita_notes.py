"""Tests for ``gita.notes`` (commit-keyed JSON sidecar) and ``gita.who``
(author + optional agent identity)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gita import notes, who

from conftest import commit_file


# ---------------------------------------------------------------------------
# notes.py — write/read round-trip
# ---------------------------------------------------------------------------


def test_write_and_read_note_roundtrip(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "x = 1\n", "init")
    payload = {"model": "claude", "session": "abc", "prompt_hash": "deadbeef"}
    notes.write(git_repo, sha, payload)
    assert notes.read(git_repo, sha) == payload


def test_read_missing_note_returns_none(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "x = 1\n", "init")
    assert notes.read(git_repo, sha) is None


def test_write_overwrites_existing_note(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "x = 1\n", "init")
    notes.write(git_repo, sha, {"model": "claude"})
    notes.write(git_repo, sha, {"model": "gpt-5"})
    assert notes.read(git_repo, sha) == {"model": "gpt-5"}


# ---------------------------------------------------------------------------
# who.py — author + optional agent
# ---------------------------------------------------------------------------


def test_who_reads_author_from_git(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "x = 1\n", "init")
    rec = who.describe(git_repo, "HEAD")
    assert rec.commit == sha
    assert rec.author_name == "Gita Tests"
    assert rec.author_email == "gita-tests@example.invalid"
    assert isinstance(rec.timestamp, int)
    assert rec.agent is None


def test_who_merges_note_fields(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "x = 1\n", "init")
    notes.write(git_repo, sha, {"model": "claude-3.7", "session": "abc123"})
    rec = who.describe(git_repo, "HEAD")
    assert rec.agent == {"model": "claude-3.7", "session": "abc123"}


def test_who_absent_note_omits_agent(git_repo: Path) -> None:
    commit_file(git_repo, "m.py", "x = 1\n", "init")
    rec = who.describe(git_repo, "HEAD")
    # Absent note is silent — agent is None, not {} and not a stub dict.
    assert rec.agent is None


def test_who_describe_at_arbitrary_rev(git_repo: Path) -> None:
    s1 = commit_file(git_repo, "m.py", "x = 1\n", "init")
    s2 = commit_file(git_repo, "m.py", "x = 2\n", "bump")
    rec1 = who.describe(git_repo, s1)
    rec2 = who.describe(git_repo, s2)
    assert rec1.commit == s1
    assert rec2.commit == s2


def test_who_to_dict_omits_agent_key_when_absent(git_repo: Path) -> None:
    commit_file(git_repo, "m.py", "x = 1\n", "init")
    rec = who.describe(git_repo, "HEAD")
    d = rec.to_dict()
    assert "agent" not in d
    assert d["author_name"] == "Gita Tests"


def test_who_to_dict_includes_agent_when_present(git_repo: Path) -> None:
    sha = commit_file(git_repo, "m.py", "x = 1\n", "init")
    notes.write(git_repo, sha, {"model": "claude"})
    rec = who.describe(git_repo, "HEAD")
    d = rec.to_dict()
    assert d["agent"] == {"model": "claude"}
