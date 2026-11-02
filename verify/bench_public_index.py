"""Reproduce pinned, bounded public-repository observations without executing corpus code."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter, sleep

APPROVED = {
    "flask": ("https://github.com/pallets/flask.git", "BSD-3-Clause", "LICENSE.txt"),
    "express": ("https://github.com/expressjs/express.git", "MIT", "LICENSE"),
    "ripgrep": ("https://github.com/BurntSushi/ripgrep.git", "MIT OR Unlicense", "COPYING"),
}


def validate_manifest(value):
    required = {"schema_version", "source_revision", "history_commits", "workers",
                "max_storage_bytes", "max_seconds", "total_seconds", "repositories"}
    if type(value) is not dict or set(value) != required or value["schema_version"] != 1:
        raise ValueError("unsupported public benchmark manifest")
    if type(value["schema_version"]) is not int:
        raise ValueError("schema version must be an integer")
    def exact_sha(value):
        return type(value) is str and len(value) == 40 and all(char in "0123456789abcdef" for char in value)

    if not exact_sha(value["source_revision"]):
        raise ValueError("source_revision must be an exact SHA-1")
    if type(value["history_commits"]) is not int or value["history_commits"] not in (20, 50):
        raise ValueError("history_commits must be 20 or 50")
    for key, lower, upper in (("workers", 1, 4), ("max_storage_bytes", 1024 ** 2, 1024 ** 3)):
        if type(value[key]) is not int or not lower <= value[key] <= upper:
            raise ValueError(f"{key} outside approved operational bound")
    for key, upper in (("max_seconds", 300), ("total_seconds", 900)):
        number = value[key]
        if type(number) not in (float, int) or not 0 < number <= upper or not isfinite(number):
            raise ValueError(f"{key} outside approved time bound")
    repositories = value["repositories"]
    if type(repositories) is not list or not repositories:
        raise ValueError("repositories must be a nonempty list")
    names = set()
    for item in repositories:
        if (type(item) is not dict or set(item) != {"name", "tip"}
                or item["name"] not in APPROVED or item["name"] in names or not exact_sha(item["tip"])):
            raise ValueError("repositories must be unique approved names with exact tips")
        names.add(item["name"])
    return value


def git(root, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=NUL", "-C", str(root), *args],
                          capture_output=True, check=True, timeout=120).stdout


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def scope(root, tip, count):
    commits = git(root, "rev-list", "--first-parent", "--max-count=" + str(count + 1), tip).decode().splitlines()
    if len(commits) != count + 1:
        raise ValueError("provisioned history must contain the bounded history plus one append")
    return list(reversed(commits))


def execute(request, output, deadline, source, worker=None):
    """Bound an owned child tree, retaining failed/timeout receipts and sampled resources."""
    import psutil

    environment = os.environ.copy()
    environment.update(GITA_BENCH_SOURCE=str(source), PYTHONPATH=str(source), GITA_ENABLE_LEGACY="0")
    worker = worker or Path(__file__).with_name("public_index_worker.py")
    config = json.loads(request.read_text())
    database = Path(config["repository"]) / ".git" / "gita" / "index.sqlite" if "repository" in config else None
    started = perf_counter()
    with output.with_suffix(".stderr.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, str(worker), str(request), str(output)],
                                   env=environment, stdout=subprocess.DEVNULL, stderr=log)
    root = psutil.Process(process.pid)
    owned, cpu, peaks = {process.pid: root.create_time()}, {}, {"rss": None, "main": None, "wal": None}
    unavailable, samples, timed_out = set(), 0, False
    while process.poll() is None:
        try:
            children = root.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        total, observed = 0, False
        for current in [root, *children]:
            try:
                created = current.create_time()
                if current.pid in owned and created != owned[current.pid]:
                    continue
                owned[current.pid] = created
                total += current.memory_info().rss
                observed = True
                usage = current.cpu_times()
                cpu[(current.pid, created)] = usage.user + usage.system
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                unavailable.add("access_denied")
        if observed:
            peaks["rss"] = max(peaks["rss"] or 0, total)
        for label, path in (() if database is None else
                            (("main", database), ("wal", database.with_name("index.sqlite-wal")))):
            try:
                peaks[label] = max(peaks[label] or 0, path.stat().st_size)
            except FileNotFoundError:
                pass
        samples += 1
        if perf_counter() - started >= deadline:
            timed_out = True
            break
        sleep(0.05)
    # Discover any still-owned descendants before the root exits; never stop
    # processes by name or use an unrelated process discovered elsewhere.
    try:
        for current in root.children(recursive=True):
            owned[current.pid] = current.create_time()
    except psutil.NoSuchProcess:
        pass
    reaped = []
    for pid, created in reversed(tuple(owned.items())):
        try:
            current = psutil.Process(pid)
            if current.create_time() == created and current.is_running():
                current.kill()
                reaped.append(current)
        except psutil.NoSuchProcess:
            pass
    process.wait(timeout=10)
    _, survivors = psutil.wait_procs(reaped, timeout=10)
    progress = output.with_suffix(".progress.json")
    result = json.loads((output if output.exists() else progress).read_text()) if (output.exists() or progress.exists()) else {}
    if timed_out:
        result.update(status="timeout", completed_seconds=None,
                      error={"code": "deadline", "message": "owned child tree stopped"})
    elif not output.exists():
        result.update(status="crash", completed_seconds=None,
                      error={"code": "child_exit", "returncode": process.returncode})
    result.update(supervised_seconds=perf_counter() - started, returncode=process.returncode)
    result["resources"] = {
        "rss_tree_sampled_peak_bytes": peaks["rss"], "main_sampled_peak_bytes": peaks["main"],
        "wal_sampled_peak_bytes": peaks["wal"], "observed_tree_cpu_seconds": sum(cpu.values()) if cpu else None,
        "sample_interval_seconds": 0.05, "samples": samples, "owned_identities": len(owned),
        "unavailable": sorted(unavailable), "cleanup_complete": not survivors,
        "temp_spool_bytes": None, "temp_spool_reason": "anonymous SQLite TEMP files not portably observable",
        "limitations": "RSS counts shared pages per process; CPU is a sampled lower bound; short-lived children/transients can be missed",
    }
    if survivors:
        result.update(status="cleanup_failed", completed_seconds=None)
    write(output, result)
    return result


def fingerprint(root):
    """Git metadata and working-tree status, excluding owned .git/gita indexes."""
    directory = Path(git(root, "rev-parse", "--absolute-git-dir").decode().strip())
    return {
        "head": git(root, "rev-parse", "HEAD").decode().strip(),
        "refs_sha256": sha256(git(root, "show-ref")).hexdigest(),
        "status_sha256": sha256(git(root, "status", "--porcelain", "-z", "--untracked-files=all")).hexdigest(),
        "tracked_metadata_sha256": sha256(b"".join(
            path.name.encode() + b"\0" + (path.read_bytes() if path.exists() else b"<absent>")
            for path in [directory / name for name in ("HEAD", "config", "index", "shallow", "packed-refs")]
        )).hexdigest(),
    }


def exit_code(summary):
    failed_attempt = any(row["status"] != "passed" or row.get("scenario_valid") is False
                         for row in summary["attempts"])
    disagreement = any(row["status"] == "different" for row in summary["equivalence"])
    return int(failed_attempt or disagreement)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True, help="Approved no-checkout clones, outside source checkout")
    parser.add_argument("--output", type=Path, required=True, help="New owned run directory outside source checkout")
    parser.add_argument("--source", type=Path, required=True, help="Pinned gita src directory")
    args = parser.parse_args()
    manifest = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    if git(args.source.parent, "rev-parse", "HEAD").decode().strip() != manifest["source_revision"]:
        raise ValueError("source revision differs from manifest")
    if git(args.source.parent, "status", "--porcelain", "--", "src"):
        raise ValueError("runtime source must be clean")
    if args.output.exists():
        raise ValueError("output must be a new directory")
    if args.output.resolve().is_relative_to(args.source.parent.resolve()):
        raise ValueError("output must be outside source checkout")
    if args.corpus_root.resolve().is_relative_to(args.source.parent.resolve()):
        raise ValueError("corpus root must be outside source checkout")
    args.output.mkdir(parents=True)
    summary = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
               "manifest": manifest, "attempts": [], "repositories": [], "equivalence": []}
    summary["tooling_sha256"] = {
        name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("bench_public_index.py", "public_index_worker.py", "index_oracle.py")
    }
    request = args.output / "environment.request.json"
    write(request, {"scenario": "environment"})
    summary["environment"] = execute(request, args.output / "environment.json", 30, args.source)
    write(args.output / "summary.json", summary)
    if summary["environment"].get("status") in ("error", "crash", "timeout", "cleanup_failed"):
        raise ValueError("environment/installed-grammar preflight failed; no measured build attempted")
    started = perf_counter()
    for item in manifest["repositories"]:
        name, tip = item["name"], item["tip"]
        url, license_name, license_path = APPROVED[name]
        root = args.corpus_root / name
        if git(root, "config", "--get", "remote.origin.url").decode().strip() != url:
            raise ValueError("corpus is not the approved public repository")
        commits = scope(root, tip, manifest["history_commits"])
        original = fingerprint(root)
        license_bytes = git(root, "show", tip + ":" + license_path)
        record = {
            "name": name, "url": url, "tip": tip, "anchor": commits[0], "history_tip": commits[-2],
            "history_commits": len(commits) - 1, "append_commits": 1, "commits": commits,
            "available_commit_count": int(git(root, "rev-list", "--count", "--all")),
            "shallow": git(root, "rev-parse", "--is-shallow-repository").decode().strip() == "true",
            "license": license_name, "license_path": license_path,
            "license_sha256": sha256(license_bytes).hexdigest(),
            "tip_tree_entries": len(git(root, "ls-tree", "-r", "--name-only", "-z", tip).split(b"\0")) - 1,
        }
        summary["repositories"].append(record)
        paths, outcomes = {}, {}
        for scenario in ("snapshot", "history", "append", "noop", "query", "serial_history", "cold_final"):
            # Cold history stops one member before the pinned tip. Append alone
            # adds that final real commit; no-op/query reuse the resulting index.
            group = "history" if scenario in ("append", "noop", "query") else scenario
            run_root = args.output / (name + "-" + group)
            repository = run_root / "repository"
            if group not in paths:
                run_root.mkdir()
                shutil.copytree(root / ".git", repository / ".git", ignore=shutil.ignore_patterns("gita"))
                paths[group] = repository
            before = fingerprint(repository)
            expected = [tip] if scenario == "snapshot" else commits[:-1] if scenario in ("history", "serial_history") else commits
            config = {
                "repository": str(repository), "scenario": scenario,
                "ref": expected[-1], "anchor": expected[0], "commits": expected,
                "workers": 1 if scenario == "serial_history" else manifest["workers"],
                "max_storage_bytes": manifest["max_storage_bytes"],
            }
            request = run_root / (scenario + ".request.json")
            write(request, config)
            remaining = manifest["total_seconds"] - (perf_counter() - started)
            deadline = max(0.1, min(manifest["max_seconds"], remaining))
            result = execute(request, run_root / (scenario + ".json"), deadline, args.source)
            result["git_unchanged"] = fingerprint(repository) == before
            if not result["git_unchanged"]:
                result.update(status="git_modified", completed_seconds=None)
            result.update(repository=name, scenario=scenario, workers=config["workers"],
                          requested_commit_count=len(expected), deadline_seconds=deadline)
            # Dependent attempts remain visible even after failed setup. Their
            # receipts are never interpreted as one-commit append/no-op costs.
            if scenario in ("append", "noop", "query"):
                prerequisite = {"append": "history", "noop": "append", "query": "append"}[scenario]
                result["prerequisite_status"] = outcomes[prerequisite]["status"]
                if outcomes[prerequisite]["status"] != "passed":
                    result["scenario_valid"] = False
                    result["completed_seconds"] = None
                else:
                    result["scenario_valid"] = True
            outcomes[scenario] = result
            summary["attempts"].append(result)
            write(args.output / "summary.json", summary)
            print(f"{name} {scenario}: {result['status']}; operation={result.get('operation_seconds')}", flush=True)
        for left, right in (("history", "serial_history"), ("append", "cold_final")):
            first, second = outcomes[left], outcomes[right]
            valid = all(row["status"] == "passed" and row.get("scenario_valid", True) for row in (first, second))
            summary["equivalence"].append({
                "repository": name, "scenarios": [left, right],
                "status": "equal" if valid and first["correctness"]["canonical_digest"] == second["correctness"]["canonical_digest"]
                          else "different" if valid else "unavailable",
                "scope": "same policy/anchor; immutable records intact; only operational generation/allocation fields excluded",
                "lineage_truth": "equivalence does not prove accepted links true",
            })
        record["download_git_unchanged"] = fingerprint(root) == original
        write(args.output / "summary.json", summary)
    summary["measured_campaign_seconds"] = perf_counter() - started
    write(args.output / "summary.json", summary)
    return exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
