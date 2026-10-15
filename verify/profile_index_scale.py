"""Bounded before/after profiles against retained pinned public corpora."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify.bench_public_index import execute, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retained", type=Path, required=True)
    parser.add_argument("--names", nargs="+", default=["flask", "express", "ripgrep"])
    parser.add_argument("--unprofiled", action="store_true", help="Record production wall time without cProfile.")
    parser.add_argument("--max-storage-bytes", type=int, help="Explicit per-store bound for a separate qualification.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for name in args.names:
        prior = args.retained / "public-index-observations-20261014-attempt1"
        if name == "flask":
            prior = args.retained / "public-index-observations-20261014-flask-retry"
        history_request = json.loads((prior / (name + "-history") / "history.request.json").read_text())
        append_request = json.loads((prior / (name + "-history") / "append.request.json").read_text())
        for group, scenarios in (("snapshot", ("snapshot",)), ("history", ("history", "append", "noop"))):
            directory = args.output / (name + "-" + group)
            directory.mkdir()
            root = directory / "repository"
            subprocess.run(["git", "-c", "core.hooksPath=NUL", "clone", "--quiet", "--shared",
                            "--no-checkout", str(args.retained / "public-index-20261014" / name),
                            str(root)], check=True, timeout=120)
            for scenario in scenarios:
                request = dict(append_request if scenario in ("append", "noop") else history_request)
                if scenario == "snapshot":
                    request.update(anchor=request["ref"], commits=[request["ref"]])
                request.update(repository=str(root.resolve()), scenario=scenario, profile=not args.unprofiled)
                if args.max_storage_bytes is not None:
                    if not 1024 ** 2 <= args.max_storage_bytes <= 1024 ** 3:
                        raise ValueError("explicit study storage bound must be between 1 MiB and 1 GiB")
                    request["max_storage_bytes"] = args.max_storage_bytes
                config, output = directory / (scenario + ".request.json"), directory / (scenario + ".json")
                write(config, request)
                measured = execute(config, output, 600, args.source.resolve())
                measured.update(repository=name, scenario=scenario)
                write(output, measured)
                results.append(measured)
                print(name, scenario, measured.get("status"), measured.get("operation_seconds"), flush=True)
                if measured.get("status") != "passed":
                    break
    write(args.output / "summary.json", {"runs": results, "source": str(args.source.resolve())})


if __name__ == "__main__":
    main()
