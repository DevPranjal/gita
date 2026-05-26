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


# ---------------------------------------------------------------------------
# phase 3 — prove / last-proven / glyph display
# ---------------------------------------------------------------------------


def _write_proof_file(root: Path, sha: str, checks: dict) -> None:
    pdir = root / ".git" / "gita" / "proofs"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{sha}.json").write_text(
        json.dumps({"commit": sha, "checks": checks}, indent=2),
        encoding="utf-8",
    )


def _ok_check() -> dict:
    return {
        "ok": True, "exit_code": 0, "duration_ms": 1,
        "ran_at": "2026-01-01T00:00:00Z", "cmd": ["true"],
        "stdout_head": "", "stdout_tail": "", "truncated": False,
    }


def _fail_check() -> dict:
    return {**_ok_check(), "ok": False, "exit_code": 1}


def test_cli_prove_runs_command_and_records(
    git_repo: Path, monkeypatch, capsys
) -> None:
    import sys as _sys
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(
        monkeypatch, git_repo,
        "prove", "pytest", "--", _sys.executable, "-c", "pass",
    )
    assert rc == 0
    assert (git_repo / ".git" / "gita" / "proofs" / f"{sha}.json").exists()


def test_cli_prove_dirty_tree_errors(
    git_repo: Path, monkeypatch, capsys
) -> None:
    import sys as _sys
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    (git_repo / "u.py").write_text("x = 2\n", encoding="utf-8")
    rc = _run_cli(
        monkeypatch, git_repo,
        "prove", "pytest", "--", _sys.executable, "-c", "pass",
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "dirty" in err.lower() or "uncommitted" in err.lower()


def test_cli_last_proven_prints_sha(
    git_repo: Path, monkeypatch, capsys
) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    _write_proof_file(git_repo, sha, {"pytest": _ok_check()})
    rc = _run_cli(monkeypatch, git_repo, "last-proven", "pytest")
    assert rc == 0
    assert sha in capsys.readouterr().out


def test_cli_last_proven_empty_exits_with_hint(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "last-proven", "pytest")
    assert rc == 3
    err = capsys.readouterr().err
    assert "gita prove" in err


def test_cli_symbol_log_shows_proof_glyphs(
    git_repo: Path, monkeypatch, capsys
) -> None:
    sha_a = commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "add foo")
    sha_b = commit_file(git_repo, "u.py", "def foo():\n    return 2\n", "edit foo")
    sha_c = commit_file(git_repo, "u.py", "def foo():\n    return 3\n", "edit again")
    _write_proof_file(git_repo, sha_a, {"pytest": _ok_check()})
    _write_proof_file(git_repo, sha_b, {"pytest": _fail_check()})
    # sha_c has no proofs

    rc = _run_cli(monkeypatch, git_repo, "symbol-log", "foo")
    assert rc == 0
    out = capsys.readouterr().out
    assert "\u2713" in out  # ✓
    assert "\u2717" in out  # ✗
    assert "\u00b7" in out  # ·


def test_cli_explain_shows_proof_section(
    git_repo: Path, monkeypatch, capsys
) -> None:
    sha = commit_file(git_repo, "u.py", "def foo():\n    return 1\n", "init")
    _write_proof_file(git_repo, sha, {"pytest": _ok_check()})
    rc = _run_cli(monkeypatch, git_repo, "explain", sha)
    assert rc == 0
    out = capsys.readouterr().out
    assert "proofs" in out.lower()
    assert "pytest" in out






# ---------------------------------------------------------------------------
# phase 0 (v0.3) — who, commit-note, last-proven --json
# ---------------------------------------------------------------------------



def test_cli_who_prints_author(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "who")
    assert rc == 0
    out = capsys.readouterr().out
    assert "Gita Tests" in out
    assert "gita-tests@example.invalid" in out
    # Absent note → no agent line.
    assert "agent:" not in out


def test_cli_who_with_note_shows_agent_line(
    git_repo: Path, monkeypatch, capsys
) -> None:
    from gita import notes
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    notes.write(git_repo, sha, {"model": "claude-3.7", "session": "abc"})
    rc = _run_cli(monkeypatch, git_repo, "who")
    assert rc == 0
    out = capsys.readouterr().out
    assert "agent:" in out
    assert "claude-3.7" in out
    assert "abc" in out


def test_cli_who_json(git_repo: Path, monkeypatch, capsys) -> None:
    from gita import notes
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    notes.write(git_repo, sha, {"model": "claude"})
    rc = _run_cli(monkeypatch, git_repo, "who", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit"] == sha
    assert payload["author_name"] == "Gita Tests"
    assert payload["agent"] == {"model": "claude"}


def test_cli_who_json_omits_agent_when_absent(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "who", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "agent" not in payload


def test_cli_who_at_rev(git_repo: Path, monkeypatch, capsys) -> None:
    s1 = commit_file(git_repo, "u.py", "x = 1\n", "init")
    commit_file(git_repo, "u.py", "x = 2\n", "bump")
    rc = _run_cli(monkeypatch, git_repo, "who", s1, "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit"] == s1


def test_cli_commit_note_writes_note(
    git_repo: Path, monkeypatch, capsys
) -> None:
    from gita import notes
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(
        monkeypatch, git_repo,
        "commit-note", "--set", "model=claude", "--set", "session=abc",
    )
    assert rc == 0
    assert notes.read(git_repo, sha) == {"model": "claude", "session": "abc"}


def test_cli_commit_note_refuses_dirty_tree(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    (git_repo / "u.py").write_text("x = 2\n", encoding="utf-8")
    rc = _run_cli(
        monkeypatch, git_repo,
        "commit-note", "--set", "model=claude",
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "dirty" in err.lower() or "uncommitted" in err.lower()


def test_cli_commit_note_requires_at_least_one_set(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "commit-note")
    assert rc != 0


def test_cli_commit_note_rejects_malformed_pair(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "u.py", "x = 1\n", "init")
    rc = _run_cli(
        monkeypatch, git_repo,
        "commit-note", "--set", "no_equals_sign",
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "key=value" in err.lower() or "key=value" in err


def test_cli_last_proven_json_shape(
    git_repo: Path, monkeypatch, capsys
) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    _write_proof_file(git_repo, sha, {"pytest": _ok_check()})
    rc = _run_cli(monkeypatch, git_repo, "last-proven", "pytest", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit"] == sha
    assert payload["check"] == "pytest"
    assert payload["ok"] is True
    assert "ran_at" in payload


def test_cli_last_proven_json_no_check_name(
    git_repo: Path, monkeypatch, capsys
) -> None:
    sha = commit_file(git_repo, "u.py", "x = 1\n", "init")
    _write_proof_file(git_repo, sha, {"pytest": _ok_check()})
    rc = _run_cli(monkeypatch, git_repo, "last-proven", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["commit"] == sha
    assert payload["check"] is None


# ---------------------------------------------------------------------------
# phase 1 (v0.3) — gita context
# ---------------------------------------------------------------------------


def test_cli_context_text(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(
        git_repo, "users.py",
        "def fetch_user(uid):\n    return {'id': uid}\n",
        "add fetch_user",
    )
    commit_file(
        git_repo, "api.py",
        "from users import fetch_user\n\ndef route(u):\n    return fetch_user(u)\n",
        "call",
    )
    rc = _run_cli(monkeypatch, git_repo, "context", "fetch_user")
    assert rc == 0
    out = capsys.readouterr().out
    assert "symbol:" in out
    assert "callers:" in out
    assert "log:" in out
    assert "route" in out


def test_cli_context_json(git_repo: Path, monkeypatch, capsys) -> None:
    commit_file(
        git_repo, "users.py",
        "def fetch_user(uid):\n    return {'id': uid}\n",
        "add",
    )
    rc = _run_cli(monkeypatch, git_repo, "context", "fetch_user", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbol"]["name"] == "fetch_user"
    assert payload["callers"] == []
    assert isinstance(payload["log"], list)
    assert payload["last_proven"] is None
    assert payload["dropped"] == []


def test_cli_context_budget_flag(git_repo: Path, monkeypatch, capsys) -> None:
    for i in range(6):
        commit_file(
            git_repo, "m.py",
            f"def f():\n    return {i}\n",
            f"bump f to {i}",
        )
    rc = _run_cli(
        monkeypatch, git_repo, "context", "f", "--budget", "0", "--json",
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dropped"]


def test_cli_context_at_rev(git_repo: Path, monkeypatch, capsys) -> None:
    s1 = commit_file(git_repo, "m.py", "def f():\n    return 1\n", "init")
    commit_file(git_repo, "m.py", "def f():\n    return 2\n", "bump")
    rc = _run_cli(
        monkeypatch, git_repo, "context", "f", "--rev", s1, "--json",
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "return 1" in payload["symbol"]["body"]


def test_cli_context_symbol_not_found(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "m.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "context", "ghost")
    assert rc != 0

# ---------------------------------------------------------------------------
# phase 2: bisect-proven CLI
# ---------------------------------------------------------------------------


def _write_pytest_proof(root: Path, sha: str, ok: bool) -> None:
    from gita import proofs as proofs_mod
    p = proofs_mod.proof_path(root, sha)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({
            "commit": sha,
            "checks": {
                "pytest": {
                    "ok": ok, "exit_code": 0 if ok else 1, "duration_ms": 1,
                    "ran_at": "2026-01-01T00:00:00Z", "cmd": ["fake"],
                    "stdout_head": "", "stdout_tail": "", "truncated": False,
                }
            },
        }),
        encoding="utf-8",
    )


def test_cli_bisect_proven_text_output(
    git_repo: Path, monkeypatch, capsys
) -> None:
    a = commit_file(git_repo, "s.py", "def foo():\n    return 1\n", "init")
    _write_pytest_proof(git_repo, a, ok=True)
    b = commit_file(git_repo, "s.py", "def foo():\n    return 99\n", "break")
    _write_pytest_proof(git_repo, b, ok=False)
    rc = _run_cli(monkeypatch, git_repo, "bisect-proven", "pytest")
    out = capsys.readouterr().out
    assert "range:" in out
    assert "suspect:" in out
    assert b in out
    assert rc != 0


def test_cli_bisect_proven_json_output(
    git_repo: Path, monkeypatch, capsys
) -> None:
    a = commit_file(git_repo, "s.py", "def foo():\n    return 1\n", "init")
    _write_pytest_proof(git_repo, a, ok=True)
    b = commit_file(git_repo, "s.py", "def foo():\n    return 99\n", "break")
    _write_pytest_proof(git_repo, b, ok=False)
    _run_cli(monkeypatch, git_repo, "bisect-proven", "--json", "pytest")
    payload = json.loads(capsys.readouterr().out)
    assert payload["suspect"] == b
    assert payload["from_sha"] == a
    assert payload["reason"] == "first_failure"
    assert payload["checks_used"] == ["pytest"]


def test_cli_bisect_proven_exits_nonzero_on_no_baseline(
    git_repo: Path, monkeypatch, capsys
) -> None:
    commit_file(git_repo, "s.py", "x = 1\n", "init")
    rc = _run_cli(monkeypatch, git_repo, "bisect-proven", "pytest")
    err = capsys.readouterr().err
    assert rc == 3
    assert "no baseline" in err.lower()


# ---------------------------------------------------------------------------
# phase 3: hooks + auto-prove
# ---------------------------------------------------------------------------


def test_cli_hooks_install_and_status(git_repo: Path, monkeypatch, capsys) -> None:
    rc = _run_cli(monkeypatch, git_repo, "hooks", "install")
    assert rc == 0
    out = capsys.readouterr().out
    assert "post-commit" in out
    assert (git_repo / ".git" / "hooks" / "post-commit").exists()

    rc = _run_cli(monkeypatch, git_repo, "hooks", "status")
    assert rc == 0
    assert "installed" in capsys.readouterr().out


def test_cli_hooks_uninstall(git_repo: Path, monkeypatch, capsys) -> None:
    _run_cli(monkeypatch, git_repo, "hooks", "install")
    capsys.readouterr()
    rc = _run_cli(monkeypatch, git_repo, "hooks", "uninstall")
    assert rc == 0

    rc = _run_cli(monkeypatch, git_repo, "hooks", "status")
    assert "not installed" in capsys.readouterr().out


def test_cli_auto_enable_and_list_json(git_repo: Path, monkeypatch, capsys) -> None:
    import sys as _sys
    rc = _run_cli(
        monkeypatch, git_repo, "auto", "enable", "pytest", "--", _sys.executable, "-c", "pass"
    )
    assert rc == 0
    capsys.readouterr()

    rc = _run_cli(monkeypatch, git_repo, "auto", "list", "--json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "pytest" in payload["checks"]
    assert payload["checks"]["pytest"]["enabled"] is True
    assert payload["checks"]["pytest"]["cmd"][0] == _sys.executable


def test_cli_auto_enable_requires_cmd(git_repo: Path, monkeypatch, capsys) -> None:
    rc = _run_cli(monkeypatch, git_repo, "auto", "enable", "pytest")
    assert rc == 2
    assert "auto enable requires" in capsys.readouterr().err


def test_cli_auto_disable(git_repo: Path, monkeypatch, capsys) -> None:
    import sys as _sys
    _run_cli(monkeypatch, git_repo, "auto", "enable", "x", "--", _sys.executable, "-c", "pass")
    capsys.readouterr()
    rc = _run_cli(monkeypatch, git_repo, "auto", "disable", "x")
    assert rc == 0
    capsys.readouterr()
    _run_cli(monkeypatch, git_repo, "auto", "list", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"]["x"]["enabled"] is False


def test_cli_auto_run_records_proof(git_repo: Path, monkeypatch, capsys) -> None:
    import sys as _sys
    from gita import proofs as proofs_mod

    sha = commit_file(git_repo, "a.py", "x = 1\n", "init")
    _run_cli(
        monkeypatch, git_repo, "auto", "enable", "always", "--", _sys.executable, "-c", "pass"
    )
    capsys.readouterr()
    rc = _run_cli(monkeypatch, git_repo, "auto", "run")
    assert rc == 0
    out = capsys.readouterr().out
    assert "always" in out
    stored = proofs_mod.read(git_repo, sha)
    assert stored["checks"]["always"]["ok"] is True


def test_cli_auto_prove_hook_internal_swallows_errors(
    git_repo: Path, monkeypatch, capsys
) -> None:
    # Even with no config and no commits the hidden hook command must exit 0.
    rc = _run_cli(monkeypatch, git_repo, "_auto-prove-hook")
    assert rc == 0


