"""Generated one-function-per-file workloads; no corpus code is executed."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify.bench_public_index import execute, write


def git(root, *args):
    env = os.environ.copy()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
    return subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false",
         "-c", "core.safecrlf=false", "-c", "commit.gpgSign=false",
         "-c", "user.name=Scale fixture", "-c", "user.email=scale@example.invalid",
         "-C", str(root), *args], env=env, check=True, capture_output=True, timeout=120,
    ).stdout.decode().strip()


def source(index, step):
    return f"def compute_{index}(value):\n    return value + {index + step}\n".encode()


def corpus(root, size):
    root.mkdir()
    git(root, "init", "--quiet")
    for index in range(size):
        (root / f"f{index:05}.py").write_bytes(source(index, 0))
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "pinned root")
    commits = [git(root, "rev-parse", "HEAD")]
    for step in range(1, 21):
        (root / "f00000.py").write_bytes(source(0, step))
        git(root, "add", ".")
        git(root, "commit", "--quiet", "-m", f"one file edit {step}")
        commits.append(git(root, "rev-parse", "HEAD"))
    return commits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpora", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 10000])
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.corpora.mkdir(parents=True, exist_ok=True)
    results = []
    for size in args.sizes:
        root = args.corpora / ("corpus-" + str(size))
        manifest = args.corpora / (str(size) + ".json")
        commits = json.loads(manifest.read_text()) if manifest.exists() else corpus(root, size)
        write(manifest, commits)
        for group, scenarios in (("snapshot", ("snapshot",)),
                                 ("history", ("history", "append", "noop"))):
            directory = args.output / (str(size) + "-" + group)
            directory.mkdir()
            repository = directory / "repository"
            git(args.output, "clone", "--quiet", "--shared", "--no-checkout",
                str(root.resolve()), str(repository.resolve()))
            for scenario in scenarios:
                selected = commits[:20] if scenario == "history" else (
                    commits[:1] if scenario == "snapshot" else commits)
                config = {
                    "repository": str(repository.resolve()), "scenario": scenario,
                    "ref": selected[-1], "anchor": selected[0], "commits": selected,
                    "workers": 4, "max_storage_bytes": 1024 ** 3, "profile": args.profile,
                }
                request, output = directory / (scenario + ".request.json"), directory / (scenario + ".json")
                write(request, config)
                result = execute(request, output, 600, args.source.resolve())
                result.update(files=size, functions=size, commit_count=len(selected))
                write(output, result)
                results.append(result)
                print(size, scenario, result.get("status"), result.get("operation_seconds"), flush=True)
                if result.get("status") != "passed":
                    break
    write(args.output / "summary.json", {"runs": results, "source": str(args.source.resolve())})


if __name__ == "__main__":
    main()
