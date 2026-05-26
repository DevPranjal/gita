"""End-to-end tests for the gita CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gita.cli import main

from conftest import commit_file


def _run_cli(monkeypatch, root: Path, *args: str) -> int:
    monkeypatch.chdir(root)
    return main(list(args))


def test_init_creates_store_in_existing_repo(git_repo: Path, monkeypatch, capsys) -> None:
    rc = _run_cli(monkeypatch, git_repo, "init", str(git_repo))
    assert rc == 0
    assert (git_repo / ".git" / "gita").is_dir()
    out = capsys.readouterr().out
    assert "Initialized gita store" in out


def test_init_creates_repo_then_store(tmp_path: Path, monkeypatch, capsys) -> None:
    target = tmp_path / "fresh"
    monkeypatch.chdir(tmp_path)
    rc = main(["init", str(target)])
    assert rc == 0
    assert (target / ".git").is_dir()
    assert (target / ".git" / "gita").is_dir()


def test_diff_default_compares_head_to_working_tree(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    (git_repo / "m.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    rc = _run_cli(monkeypatch, git_repo, "diff")
    assert rc == 0
    out = capsys.readouterr().out
    assert "modify_body" in out.replace("body", "modify_body") or "body" in out
    assert "f" in out


def test_diff_json_emits_manifest(git_repo: Path, monkeypatch, capsys) -> None:
    s1 = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    s2 = commit_file(git_repo, "m.py", "def f():\n    return 2\n", "edit")
    rc = _run_cli(monkeypatch, git_repo, "diff", s1, s2, "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "manifest"
    assert payload["from"] == s1
    assert payload["to"] == s2


def test_diff_filter_only_logic(git_repo: Path, monkeypatch, capsys) -> None:
    s1 = commit_file(git_repo, "m.py", "import os\n\ndef f():\n    return 1\n", "init")
    s2 = commit_file(
        git_repo, "m.py", "import os\nimport sys\n\ndef f():\n    return 2\n", "two"
    )
    rc = _run_cli(monkeypatch, git_repo, "diff", s1, s2, "--only", "logic", "--json")
    payload = json.loads(capsys.readouterr().out)
    op_kinds = {op["op"] for fe in payload["files"] for op in fe["ops"]}
    assert "add_import" not in op_kinds  # filtered out (signature category)


def test_status_summary_when_clean(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(git_repo, "m.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "status")
    assert rc == 0
    out = capsys.readouterr().out
    assert "working tree clean" in out
    assert "manifest" in out


def test_commit_writes_manifest_and_reports_summary(git_repo: Path, monkeypatch, capsys) -> None:
    (git_repo / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "-C", str(git_repo), "add", "m.py"], check=True)
    rc = _run_cli(monkeypatch, git_repo, "commit", "-m", "add f")
    assert rc == 0
    out = capsys.readouterr().out
    assert "manifest:" in out
    # Manifest file written.
    manifests = list((git_repo / ".git" / "gita" / "manifests").glob("*.json"))
    assert len(manifests) == 1


def test_explain_falls_back_to_recompute_for_uninstrumented_commit(
    git_repo: Path, monkeypatch, capsys
) -> None:
    sha = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "explain", sha, "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit"] == sha
    assert payload["files"]


def test_symbol_log_finds_rename_path(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(git_repo, "u.py", "def old():\n    return 1\n", "init")
    commit_file(git_repo, "u.py", "def new():\n    return 1\n", "rename")
    rc = _run_cli(monkeypatch, git_repo, "symbol-log", "old", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2  # rename commit + initial add


def test_callers_command_lists_call_sites(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(
        git_repo,
        "m.py",
        "def f():\n    return 1\n\ndef g():\n    return f()\n",
        "init",
    )
    rc = _run_cli(monkeypatch, git_repo, "callers", "f", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "f"
    assert any(h["caller"] == "g" for h in payload["callers"])


def test_reindex_command_backfills(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(git_repo, "m.py", "x = 1\n", "one")
    commit_file(git_repo, "m.py", "x = 2\n", "two")
    rc = _run_cli(monkeypatch, git_repo, "reindex")
    assert rc == 0
    out = capsys.readouterr().out
    assert "computed 2" in out


def test_diff_outside_repo_errors(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["diff"])


def test_cli_diff_symbol_flag(git_repo: Path, monkeypatch, capsys) -> None:
    s1 = commit_file(
        git_repo,
        "m.py",
        "def fetch_user():\n    return 1\n\ndef other():\n    return 2\n",
        "init",
    )
    s2 = commit_file(
        git_repo,
        "m.py",
        "def fetch_user():\n    return 11\n\ndef other():\n    return 22\n",
        "edit both",
    )
    rc = _run_cli(monkeypatch, git_repo, "diff", s1, s2, "--symbol", "fetch_user", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = {op.get("name") for fe in payload["files"] for op in fe["ops"]}
    assert names == {"fetch_user"}


def test_cli_diff_nonpy_rendered_as_textual(git_repo: Path, monkeypatch, capsys) -> None:
    s1 = commit_file(git_repo, "config.yaml", "port: 80\n", "init")
    s2 = commit_file(git_repo, "config.yaml", "port: 8080\n", "edit")
    rc = _run_cli(monkeypatch, git_repo, "diff", s1, s2)
    assert rc == 0
    out = capsys.readouterr().out
    assert "config.yaml" in out
    assert "non-python" in out


def test_cli_get_prints_symbol(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(
        git_repo, "u.py", "def fetch_user():\n    return 1\n", "init"
    )
    rc = _run_cli(monkeypatch, git_repo, "get", "fetch_user")
    assert rc == 0
    out = capsys.readouterr().out
    assert "u.py" in out
    assert "1-2" in out or "1:2" in out or ":1" in out
    assert "def fetch_user" in out


def test_cli_get_json_shape(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(
        git_repo, "u.py", "def fetch_user(uid):\n    return uid\n", "init"
    )
    rc = _run_cli(monkeypatch, git_repo, "get", "fetch_user", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {
        "name", "kind", "path", "line_start", "line_end",
        "signature", "body", "rev", "requested_as",
    }
    assert payload["name"] == "fetch_user"
    assert payload["kind"] == "function"


def test_cli_get_ambiguous_exits_nonzero_with_candidates(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "a.py", "def helper():\n    return 1\n", "a")
    commit_file(git_repo, "b.py", "def helper():\n    return 2\n", "b")
    rc = _run_cli(monkeypatch, git_repo, "get", "helper")
    assert rc == 2
    err = capsys.readouterr().err
    assert "ambiguous" in err.lower()
    assert "a.py" in err and "b.py" in err


def test_cli_get_not_found_exits_nonzero(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "get", "wat")
    assert rc == 1
    err = capsys.readouterr().err
    assert "wat" in err
    assert "not found" in err.lower()


def test_cli_get_at_rev_syntax(git_repo: Path, monkeypatch, capsys) -> None:
    sha_a = commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "add foo")
    commit_file(git_repo, "u.py", "x = 1\n", "delete foo")
    rc = _run_cli(monkeypatch, git_repo, "get", f"foo@{sha_a}", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "foo"
    assert payload["rev"] == sha_a


