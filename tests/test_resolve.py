"""Entity resolution.

An agent asked to trace `SaveUploadedFile` types exactly that. gita required
`context.go::SaveUploadedFile` and returned "no recorded changes", so the agent
fell back to `git log -L` and paid for an extra turn.

Being strict about identifiers is correct for storage and wrong for input.
"""

from __future__ import annotations

import subprocess

import pytest

from gita.context.resolve import Ambiguous, resolve_entity
from gita.history import entity_history
from gita.vcs.git import Repo

IDS = [
    "src/app.py::handle",
    "src/app.py::Store::get",
    "src/app.py::Store::put",
    "tests/test_app.py::test_handle",
    "crates/ignore/src/walk.rs::Iterator for Walk::next",
]


class TestResolve:
    def test_exact_id_wins(self):
        assert resolve_entity(IDS, "src/app.py::Store::get") == "src/app.py::Store::get"

    def test_bare_leaf_name(self):
        assert resolve_entity(IDS, "handle") == "src/app.py::handle"

    def test_qualified_suffix(self):
        assert resolve_entity(IDS, "Store::get") == "src/app.py::Store::get"

    def test_case_insensitive_fallback(self):
        assert resolve_entity(IDS, "STORE::GET") == "src/app.py::Store::get"

    def test_ambiguous_names_are_reported_not_guessed(self):
        ids = ["a.py::run", "b.py::run"]
        with pytest.raises(Ambiguous) as excinfo:
            resolve_entity(ids, "run")
        assert set(excinfo.value.matches) == set(ids)

    def test_unknown_returns_none(self):
        assert resolve_entity(IDS, "nope") is None

    def test_prefers_exact_leaf_over_substring(self):
        ids = ["a.py::get", "a.py::get_config"]
        assert resolve_entity(ids, "get") == "a.py::get"

    def test_substring_when_nothing_better(self):
        assert resolve_entity(IDS, "test_han") == "tests/test_app.py::test_handle"


@pytest.fixture
def evolving(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    def commit(msg, src):
        (tmp_path / "svc.py").write_text(src)
        git("add", "-A")
        git("commit", "-q", "-m", msg)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    commit("add", "def fetch(url):\n    return get(url)\n")
    commit("retry", "def fetch(url):\n    for _ in range(3):\n        return get(url)\n")
    return Repo(tmp_path)


class TestHistoryAcceptsBareNames:
    def test_bare_function_name_works(self, evolving):
        """This exact call returned 'no recorded changes' in iteration 3."""
        assert entity_history(evolving, "fetch", limit=10)

    def test_qualified_id_still_works(self, evolving):
        assert entity_history(evolving, "svc.py::fetch", limit=10)

    def test_unknown_name_is_still_empty(self, evolving):
        assert entity_history(evolving, "nonexistent", limit=10) == []
