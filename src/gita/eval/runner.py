"""Runs one task in one arm.

The only difference between arms is what is *installed*, never what is *asked*.
Putting "use gita" in the prompt would measure prompt engineering rather than
tool value, so gita is discovered the way any repo tool is: it is on PATH and
documented in AGENTS.md. Whether the agent reaches for it is itself a result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..telemetry import load_events
from ..telemetry.shim import install as install_shim
from .logs import parse_usage
from .score import score_run
from .spec import Task

AGENTS_MD = """\
# Working in this repository

## Reviewing changes

This repo has `gita` installed, a tool that reads git changes as *context diffs*:
it reports which named functions, classes and config keys changed rather than
which lines moved, and costs far fewer tokens than a raw `git diff`.

```
gita diff <base> <head>              headline plus changed entities
gita diff <base> <head> --budget N   cap the output at N tokens
gita diff --interface-only           only changes that can break a caller
gita diff --filter TERM              only entities matching TERM
gita expand <entity>                 children of a rolled-up entity
gita show <entity>                   exact hunks for one entity
gita history <entity>                how one entity changed over time
gita --help                          full usage
```

Ordinary `git` commands are available as well.
"""


@dataclass(frozen=True, slots=True)
class ArmConfig:
    name: str
    has_gita: bool

    @staticmethod
    def git() -> "ArmConfig":
        return ArmConfig("git", False)

    @staticmethod
    def gita() -> "ArmConfig":
        return ArmConfig("gita", True)


def copilot_executable() -> str:
    """Prefer the real binary: a .ps1 cannot be executed by subprocess, and
    routing through a shell would mangle multi-line prompts."""
    for candidate in ("copilot.exe", "copilot.bat", "copilot"):
        found = shutil.which(candidate)
        if found and not found.lower().endswith(".ps1"):
            return found
    return "copilot"


def build_prompt(task: Task) -> str:
    """The task, plus which revisions to look at.

    Without this the agent has to guess what "this commit" means -- in the first
    smoke run it inspected HEAD, found the wrong thing, and went hunting. The
    scope is identical in both arms; only the tools available differ.
    """
    if task.head == "":
        scope = ("Look at the uncommitted changes in the repository in the "
                 "current directory.")
    else:
        scope = (f"Look at the change between {task.base} and {task.head} "
                 f"in the repository in the current directory.")
    return f"{task.prompt.strip()}\n\n{scope}"


def build_command(task: Task, arm: ArmConfig, run_dir: Path,
                  model: str = "claude-opus-5") -> list[str]:
    """Identical in both arms except for the log directory."""
    return [
        copilot_executable(),
        "-p", build_prompt(task),
        "--silent",
        "--allow-all-tools",
        "--no-color",
        "--no-ask-user",
        "--model", model,
        "--log-level", "all",
        "--log-dir", str(run_dir / "logs"),
    ]


def build_env(arm: ArmConfig, task_id: str, run_id: str, run_dir: Path,
              shim_dir: Path, gita_bin: Path) -> dict[str, str]:
    env = dict(os.environ)

    path_parts = [str(shim_dir)]
    if arm.has_gita:
        path_parts.append(str(gita_bin))
    env["PATH"] = os.pathsep.join([*path_parts, env.get("PATH", "")])

    env["GITA_TELEMETRY"] = str(run_dir / "telemetry.jsonl")
    env["GITA_SESSION"] = run_id
    env["GITA_TASK"] = task_id
    env["GITA_ARM"] = arm.name
    env["GITA_REAL_GIT"] = shutil.which("git") or "git"
    env["NO_COLOR"] = "1"
    return env


def install_gita_launcher(directory: Path) -> Path:
    """A `gita` entry point that works from cmd and from bash.

    The agent's shell is not necessarily cmd, and a .cmd file is invisible to
    bash -- the first smoke run recorded no tool calls at all for this reason.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "gita.cmd").write_text(
        f'@echo off\r\n"{sys.executable}" -m gita %*\r\n', encoding="utf8")

    posix = directory / "gita"
    posix.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m gita "$@"\n',
        encoding="utf8", newline="\n")
    posix.chmod(0o755)
    return directory


def _apply_setup(repo_root: Path, task: Task) -> None:
    if task.setup is None:
        return
    target = repo_root / task.setup.file
    if task.setup.append:
        target.write_text(target.read_text(encoding="utf8") + task.setup.append,
                          encoding="utf8")


def _reset(repo_root: Path) -> None:
    subprocess.run(["git", "-C", str(repo_root), "checkout", "--", "."],
                   capture_output=True, check=False)
    subprocess.run(["git", "-C", str(repo_root), "clean", "-fd"],
                   capture_output=True, check=False)


def run_once(task: Task, arm: ArmConfig, repo_root: Path, run_dir: Path,
             shim_dir: Path, gita_bin: Path, model: str,
             timeout: int = 600) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name

    _reset(repo_root)
    agents_md = repo_root / "AGENTS.md"
    if arm.has_gita:
        agents_md.write_text(AGENTS_MD, encoding="utf8")
    elif agents_md.exists():
        agents_md.unlink()
    _apply_setup(repo_root, task)

    command = build_command(task, arm, run_dir, model)
    env = build_env(arm, task.id, run_id, run_dir, shim_dir, gita_bin)

    started = time.time()
    try:
        completed = subprocess.run(
            command, cwd=str(repo_root), env=env, capture_output=True,
            check=False, timeout=timeout,
        )
        answer = completed.stdout.decode("utf8", "replace")
        stderr = completed.stderr.decode("utf8", "replace")
        code = completed.returncode
    except subprocess.TimeoutExpired:
        answer, stderr, code = "", "TIMEOUT", -1

    elapsed = time.time() - started
    _reset(repo_root)

    (run_dir / "answer.txt").write_text(answer, encoding="utf8")
    if stderr.strip():
        (run_dir / "stderr.txt").write_text(stderr, encoding="utf8")

    result = score_run(
        answer=answer,
        must_mention=task.must_mention,
        usage=parse_usage(run_dir / "logs"),
        events=load_events(run_dir / "telemetry.jsonl"),
    )
    result.update({
        "task": task.id,
        "arm": arm.name,
        "category": task.category,
        "run_id": run_id,
        "seconds": round(elapsed, 1),
        "exit_code": code,
        "expect_no_benefit": task.expect_no_benefit,
    })
    (run_dir / "result.json").write_text(json.dumps(result, indent=2),
                                         encoding="utf8")
    return result


def prepare(workspace: Path) -> tuple[Path, Path]:
    """Create the shim and launcher directories used by every run."""
    shim_dir = install_shim(workspace / "shims")
    gita_bin = install_gita_launcher(workspace / "bin")
    return shim_dir, gita_bin
