"""Tests for the multi-file caller index."""

from __future__ import annotations

from pathlib import Path

from gita import callers as callers_mod
from gita.store import gita_dir

from conftest import commit_file


def test_callers_finds_cross_file_hits(git_repo: Path) -> None:
    commit_file(git_repo, "core/util.py", "def f():\n    return 1\n", "core")
    commit_file(
        git_repo,
        "app/main.py",
        "from core.util import f\n\ndef run():\n    return f() + 1\n",
        "app",
    )
    commit_file(
        git_repo,
        "app/handlers.py",
        "from core import util\n\nclass H:\n    def go(self):\n        return util.f()\n",
        "handlers",
    )
    hits = callers_mod.find(git_repo, "f")
    files = sorted({h["file"] for h in hits})
    assert files == ["app/handlers.py", "app/main.py"]
    callers = sorted(h["caller"] for h in hits)
    assert callers == ["go", "run"]
    # Line numbers populated and 1-based.
    assert all(h["line"] >= 1 for h in hits)


def test_callers_returns_empty_for_unknown(git_repo: Path) -> None:
    commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    assert callers_mod.find(git_repo, "nope") == []


def test_callers_at_old_ref_sees_old_tree(git_repo: Path) -> None:
    s1 = commit_file(
        git_repo,
        "m.py",
        "def old():\n    return 1\n\ndef caller():\n    return old()\n",
        "init",
    )
    commit_file(
        git_repo,
        "m.py",
        "def new():\n    return 1\n\ndef caller():\n    return new()\n",
        "rename",
    )
    head_hits = callers_mod.find(git_repo, "old")
    old_hits = callers_mod.find(git_repo, "old", ref=s1)
    assert head_hits == []
    assert len(old_hits) == 1
    assert old_hits[0]["caller"] == "caller"


def test_callers_cache_written_under_gita_dir(git_repo: Path) -> None:
    commit_file(git_repo, "m.py", "def f():\n    return 1\n\ndef g():\n    return f()\n", "init")
    callers_mod.find(git_repo, "f")
    cache_dir = gita_dir(git_repo) / "callers"
    assert cache_dir.is_dir()
    files = list(cache_dir.glob("*.json"))
    assert len(files) == 1


def test_callers_cache_hits_on_second_call(git_repo: Path, monkeypatch) -> None:
    """The second call must not rebuild — assert ``_build_index_for_tree`` is not invoked."""
    commit_file(git_repo, "m.py", "def f():\n    pass\n\ndef g():\n    return f()\n", "init")
    callers_mod.find(git_repo, "f")  # warms cache

    calls = {"n": 0}
    real = callers_mod._build_index_for_tree

    def spy(root, ref):
        calls["n"] += 1
        return real(root, ref)

    monkeypatch.setattr(callers_mod, "_build_index_for_tree", spy)
    callers_mod.find(git_repo, "f")
    assert calls["n"] == 0


def test_callers_finds_dotted_attribute_calls(git_repo: Path) -> None:
    """``obj.method()`` should count as a call site of ``method``."""
    commit_file(
        git_repo,
        "m.py",
        "class C:\n"
        "    def serve(self):\n"
        "        return 1\n"
        "\n"
        "def driver():\n"
        "    c = C()\n"
        "    return c.serve()\n",
        "init",
    )
    hits = callers_mod.find(git_repo, "serve")
    assert any(h["caller"] == "driver" for h in hits)
