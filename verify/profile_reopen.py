"""Profile one distinct-body edit after a retained 10,000-file checkpoint."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify.bench_public_index import execute, write


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false",
         "-c", "commit.gpgSign=false", "-c", "user.name=Reopen fixture",
         "-c", "user.email=reopen@example.invalid", "-C", str(root), *args],
        check=True, capture_output=True, timeout=120,
    ).stdout.decode().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze-source", action="store_true")
    parser.add_argument("--ref", help="Reuse the exact already-authored append commit.")
    parser.add_argument("--commit-source", type=Path, help="Local repository containing --ref.")
    parser.add_argument("--unprofiled", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.freeze_source:
        shutil.copytree(args.source, args.output / "runtime-src",
                        ignore=shutil.ignore_patterns("__pycache__"))
        source = args.output / "runtime-src"
    else:
        source = args.source
    root = args.output / "repository"
    git(args.output, "clone", "--quiet", "--shared", "--no-checkout",
        str(args.previous.resolve()), str(root.resolve()))
    git(root, "read-tree", "HEAD")
    database = root / ".git" / "gita" / "index.sqlite"
    database.parent.mkdir()
    with sqlite3.connect((args.previous / ".git" / "gita" / "index.sqlite").resolve().as_uri()
                         + "?mode=ro", uri=True) as before, sqlite3.connect(database) as after:
        before.backup(after)
        after.execute("PRAGMA journal_mode=WAL")
    if args.ref is not None:
        if args.commit_source is None:
            raise ValueError("--ref requires --commit-source")
        git(root, "fetch", "--quiet", str(args.commit_source.resolve()), args.ref)
        git(root, "update-ref", "HEAD", args.ref)
    else:
        (root / "f00000.py").write_bytes(b"def compute_0(value):\n    return value + 1000000\n")
        git(root, "add", "--", "f00000.py")
        git(root, "commit", "--quiet", "-m", "distinct-body single-file edit")
    commits = list(reversed(git(root, "rev-list", "--first-parent", "HEAD").splitlines()))
    request, output = args.output / "append.request.json", args.output / "append.json"
    write(request, {
        "repository": str(root.resolve()), "scenario": "append", "ref": commits[-1],
        "anchor": commits[0], "commits": commits, "workers": 4,
        "max_storage_bytes": 1024 ** 3, "profile": not args.unprofiled,
    })
    result = execute(request, output, 600, source.resolve())
    write(output, result)
    print(json.dumps({"status": result["status"], "seconds": result.get("operation_seconds"),
                      "phases": result.get("phase_timings")}), flush=True)


if __name__ == "__main__":
    main()
