"""A `git` stand-in that records what git returned, then forwards it verbatim.

The baseline arm has to be measured the same way as the treatment arm, or the
comparison is worthless. Instrumenting only gita would measure the arm we are
advertising.

The harness puts a directory containing a `git` shim ahead of the real git on
PATH; this module finds the real binary, runs it, records the size of its
output, and passes stdout, stderr and the exit code through untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..context import count_tokens
from .events import record, timed

ENV_REAL_GIT = "GITA_REAL_GIT"


def find_real_git(argv0: str | None = None) -> str | None:
    """Locate git, skipping the shim's own directory so we do not recurse."""
    configured = os.environ.get(ENV_REAL_GIT)
    if configured and Path(configured).exists():
        return configured

    shim_dir = Path(argv0).resolve().parent if argv0 else None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate_dir = Path(entry)
        try:
            if shim_dir and candidate_dir.resolve() == shim_dir:
                continue
        except OSError:
            continue
        found = shutil.which("git", path=str(candidate_dir))
        if found:
            return found
    return None


#: git global options that consume the following argument.
_VALUE_FLAGS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--super-prefix", "--exec-path", "--config-env",
})


def subcommand(args: list[str]) -> str:
    """The git subcommand, ignoring global options and their values."""
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in _VALUE_FLAGS:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return "git"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    real_git = find_real_git(sys.argv[0] if argv is None else None)

    if real_git is None:
        print("gita shim: could not locate the real git binary", file=sys.stderr)
        return 127

    with timed() as elapsed:
        result = subprocess.run([real_git, *args], capture_output=True, check=False)

    stdout = result.stdout.decode("utf8", "replace")
    record({
        "arm": "git",
        "tool": f"git {subcommand(args)}",
        "args": " ".join(args)[:200],
        "output_tokens": count_tokens(stdout),
        "latency_ms": elapsed.ms,
        "ok": result.returncode == 0,
    })

    sys.stdout.write(stdout)
    sys.stderr.write(result.stderr.decode("utf8", "replace"))
    return result.returncode


def install(directory: str | Path) -> Path:
    """Write a `git` shim into ``directory`` and return that directory.

    Both a .cmd and an extensionless POSIX script are written: the agent may
    shell out through cmd, powershell or bash, and a .cmd is invisible to bash.

    The real git path is baked in at install time. Discovering it at runtime is
    unreliable -- under `python -m` the shim cannot see its own directory, so it
    re-finds itself on PATH and recurses.
    """
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    real_git = shutil.which("git") or "git"

    (target / "git.cmd").write_text(
        "@echo off\r\n"
        f'set "{ENV_REAL_GIT}={real_git}"\r\n'
        f'"{python}" -m gita.telemetry.shim %*\r\n',
        encoding="utf8")

    posix = target / "git"
    posix.write_text(
        "#!/bin/sh\n"
        f'{ENV_REAL_GIT}="{real_git}"\n'
        f"export {ENV_REAL_GIT}\n"
        f'exec "{python}" -m gita.telemetry.shim "$@"\n',
        encoding="utf8", newline="\n")
    posix.chmod(0o755)

    return target


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
