"""Benchmark evidence parsing or a cold snapshot index from this worktree."""

from __future__ import annotations

import argparse
import _thread
import cProfile
import json
from math import isfinite
import os
from pathlib import Path
import pstats
import sqlite3
import sys
import tempfile
from threading import Timer
from time import perf_counter

sys.path.insert(0, os.environ.get("GITA_BENCH_SOURCE", str(Path(__file__).resolve().parents[1] / "src")))

from gita.index.evidence import parse_evidence
from gita.index import builder
from gita.index.builder import BuildLimits, build
from gita.index.store import IndexStore
from gita.vcs.git import CommittedRepo


class TimedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.timings = {}

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, statement, parameters=()):
        started = perf_counter()
        try:
            return self.connection.execute(statement, parameters)
        finally:
            count, elapsed = self.timings.get(statement, (0, 0.0))
            self.timings[statement] = (count + 1, elapsed + perf_counter() - started)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="Source file or Git repository")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--parse-timeout", type=float, default=5.0)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--max-seconds", type=float, default=180.0)
    parser.add_argument("--cache-mib", type=int)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--sql-profile", action="store_true")
    args = parser.parse_args()
    if args.repeat < 1 or not isfinite(args.max_seconds) or args.max_seconds <= 0:
        parser.error("--repeat and --max-seconds must be positive")
    if args.cache_mib is not None and args.cache_mib < 1:
        parser.error("--cache-mib must be positive")
    options = {"parse_timeout_seconds": args.parse_timeout}
    if args.workers is not None:
        options["workers"] = args.workers
    limits = BuildLimits(**options)
    profile = cProfile.Profile()
    connections = []

    def cancel():
        for connection in connections:
            try:
                connection.interrupt()
            except sqlite3.Error:
                pass
        _thread.interrupt_main()

    timer = Timer(args.max_seconds, cancel)
    timer.daemon = True
    timer.start()
    started = perf_counter()
    if args.profile:
        profile.enable()
    try:
        if args.target.is_dir():
            destination = Path(tempfile.mkdtemp(prefix="gita-index-bench-")) / "index.sqlite"
            print(f"Cold snapshot index: {destination}", flush=True)
            with CommittedRepo(args.target) as repository, IndexStore.create(destination) as store:
                connections.append(store.connection)
                timed = TimedConnection(store.connection) if args.sql_profile else None
                if timed is not None:
                    store.connection = timed
                if args.cache_mib is not None:
                    store.connection.execute(f"PRAGMA main.cache_size = {-args.cache_mib * 1024}")
                    store.connection.execute(f"PRAGMA temp.cache_size = {-args.cache_mib * 1024}")
                original_parse = builder._Builder._parse
                parsed = 0

                def measured_parse(current, entry):
                    nonlocal parsed
                    result = original_parse(current, entry)
                    parsed += 1
                    if parsed % 500 == 0:
                        print(f"{parsed} files, {perf_counter() - started:.1f}s: "
                              f"{entry.path!r}", flush=True)
                    return result

                builder._Builder._parse = measured_parse
                try:
                    result = build(repository, store, ref=args.ref, since=args.ref,
                                   limits=limits)
                finally:
                    builder._Builder._parse = original_parse
                    print(f"Completed file parses: {parsed}", flush=True)
                    if timed is not None:
                        for statement, (count, elapsed) in sorted(
                            timed.timings.items(), key=lambda item: item[1][1], reverse=True,
                        )[:15]:
                            print(f"{elapsed:.3f}s / {count} calls: {statement[:160]}", flush=True)
                print(json.dumps({
                    "seconds": round(perf_counter() - started, 3),
                    "tip": result.tip.digest.hex(), "status": result.status,
                    "commits": result.commits, "parsed_files": result.parsed_files,
                    "cached_files": result.cached_files,
                    "definitions": store.connection.execute("SELECT count(*) FROM definition").fetchone()[0],
                    "parse_statuses": dict(store.connection.execute(
                        "SELECT status, count(*) FROM parse_result GROUP BY status",
                    )),
                    "database_bytes": destination.stat().st_size,
                }), flush=True)
        else:
            source = args.target.read_bytes()
            path = args.target.name.encode("utf-8")
            parse_evidence(source, path)
            started = perf_counter()
            for _ in range(args.repeat):
                bundle = parse_evidence(source, path)
            elapsed = perf_counter() - started
            print(f"{args.repeat} parses, {len(source)} bytes, "
                  f"{len(bundle.result.definitions)} definitions: {elapsed:.3f}s "
                  f"({elapsed / args.repeat:.4f}s/file)")
    finally:
        timer.cancel()
        timer.join()
        print(f"Elapsed: {perf_counter() - started:.3f}s", flush=True)
        if args.profile:
            profile.disable()
            pstats.Stats(profile).strip_dirs().sort_stats("cumulative").print_stats(25)


if __name__ == "__main__":
    main()