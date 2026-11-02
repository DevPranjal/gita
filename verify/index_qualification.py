"""Safe correctness-gated benchmark supervisor, separate from legacy profiling."""

from __future__ import annotations

from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from time import perf_counter, sleep
from zipfile import ZIP_DEFLATED, ZipFile

from verify.index_corpus import generate, git, validate_corpus

SCENARIOS = ("snapshot", "history", "incremental", "noop", "query")


def validate_manifest(value):
    required = {"schema_version", "source_revision", "corpus", "scenarios", "repetitions",
                "workers", "max_seconds", "notes"}
    if type(value) is not dict or not required <= set(value) or set(value) - required - {"max_storage_bytes"}:
        raise ValueError("manifest has missing or unknown fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    revision = value["source_revision"]
    if type(revision) is not str or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("source_revision must be an exact lowercase SHA-1")
    validate_corpus(value["corpus"])
    for key, minimum, maximum in (
        ("repetitions", 1, 20), ("workers", 1, 32),
        ("max_storage_bytes", 1024 * 1024, 1024 ** 3),
    ):
        number = value.get(key, 256 * 1024 ** 2)
        if type(number) is not int or not minimum <= number <= maximum:
            raise ValueError(f"{key} must be an integer in [{minimum}, {maximum}]")
    number = value["max_seconds"]
    try:
        valid = type(number) in (int, float) and isfinite(number) and 0 < number <= 3600
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("max_seconds must be finite, positive and at most 3600")
    scenarios = value["scenarios"]
    if (type(scenarios) is not list or not scenarios or any(type(item) is not str for item in scenarios)
            or len(set(scenarios)) != len(scenarios) or set(scenarios) - set(SCENARIOS)):
        raise ValueError("scenarios must be unique supported scenario names")
    notes = value["notes"]
    if (type(notes) is not dict or set(notes) != {"hardware", "power", "contention"}
            or any(type(note) is not str or not note.strip() or len(note) > 1000 for note in notes.values())):
        raise ValueError("notes requires nonempty hardware/power/contention strings")
    return value


def load_manifest(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate manifest field: {key}")
            value[key] = item
        return value

    def nonfinite(value):
        raise ValueError(f"nonfinite JSON constant: {value}")

    return validate_manifest(json.loads(path.read_text(encoding="utf-8"),
                                        object_pairs_hook=unique, parse_constant=nonfinite))


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False), encoding="utf-8")


def execute(request, output, deadline):
    """Sample only this child's tree; PID+creation-time ownership survives exit/reparenting."""
    import psutil

    worker = Path(__file__).with_name("index_bench_worker.py")
    environment = os.environ.copy()
    environment["GITA_ENABLE_LEGACY"] = "0"
    environment["PYTHONPATH"] = os.environ.get("GITA_BENCH_SOURCE", str(worker.parents[1] / "src"))
    with output.with_suffix(".stderr.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(worker), str(request), str(output)], env=environment,
            stdout=subprocess.DEVNULL, stderr=log,
        )
    owned, cpu = {}, {}
    root = psutil.Process(process.pid)
    owned[process.pid] = root.create_time()
    peak, main_peak, wal_peak, samples = 0, 0, 0, 0
    memory_errors = set()
    start = perf_counter()
    timed_out = False
    reaped = []
    try:
        while process.poll() is None:
            try:
                descendants = root.children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            total = 0
            for current in [root, *descendants]:
                try:
                    identity = current.create_time()
                    if current.pid in owned and owned[current.pid] != identity:
                        continue
                    owned[current.pid] = identity
                    total += current.memory_info().rss
                    usage = current.cpu_times()
                    cpu[(current.pid, identity)] = usage.user + usage.system
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    memory_errors.add("access_denied")
            peak = max(peak, total)
            samples += 1
            database = output.parent / "repository" / ".git" / "gita" / "index.sqlite"
            for path in (database, database.with_name("index.sqlite-wal")):
                try:
                    if path.name == "index.sqlite":
                        main_peak = max(main_peak, path.stat().st_size)
                    elif path.name.endswith("-wal"):
                        wal_peak = max(wal_peak, path.stat().st_size)
                except FileNotFoundError:
                    pass
            if perf_counter() - start >= deadline:
                timed_out = True
                break
            sleep(0.05)
    finally:
        # Only discovered owned identities may be stopped, even after a root exit.
        try:
            for child in root.children(recursive=True):
                owned[child.pid] = child.create_time()
        except psutil.NoSuchProcess:
            pass
        for pid, created in reversed(tuple(owned.items())):
            try:
                current = psutil.Process(pid)
                if current.create_time() == created and current.is_running():
                    current.kill()
                    reaped.append(current)
            except psutil.NoSuchProcess:
                pass
        process.wait(timeout=10)
        unused_dead, survivors = psutil.wait_procs(reaped, timeout=10)
    resources = {
        "rss_tree_sampled_peak_bytes": peak if samples else None,
        "memory_complete": not memory_errors and samples > 0,
        "memory_unavailable_reasons": sorted(memory_errors),
        "sample_interval_seconds": 0.05, "samples": samples, "owned_process_identities": len(owned),
        "observed_tree_cpu_seconds": sum(cpu.values()) if cpu else None,
        "cpu_limitations": "lower bound; processes that exit between samples may be missed",
        "main_sampled_peak_bytes": main_peak, "wal_sampled_peak_bytes": wal_peak,
        "temp_spool_bytes": None,
        "temp_spool_unavailable_reason": "SQLite anonymous TEMP files are not portably observable",
        "memory_limitations": "sum of simultaneous RSS; shared pages counted per process; short transients may be missed",
        "cleanup_complete": not survivors,
    }
    if timed_out:
        progress = output.parent / "operation.json"
        result = json.loads(progress.read_text()) if progress.exists() else {}
        result.update(status="timeout", completed_seconds=None, correctness={"passed": False},
                      error={"code": "deadline", "message": "Owned process tree stopped at supervisor deadline"})
    elif output.exists():
        result = json.loads(output.read_text(encoding="utf-8"))
    else:
        result = {"status": "crash", "completed_seconds": None, "correctness": {"passed": False},
                  "error": {"code": "child_exit", "returncode": process.returncode}}
    result["resources"] = resources
    if survivors:
        result.update(status="cleanup_failed", completed_seconds=None, correctness={"passed": False},
                      error={"code": "owned_survivor", "count": len(survivors)})
    result["supervised_seconds"] = perf_counter() - start
    return result


def campaign(manifest_path: Path, output: Path) -> int:
    manifest = load_manifest(manifest_path)
    import psutil  # Optional bench extra, required BEFORE starting any owned child.

    output = output.resolve()
    source = Path(os.environ.get("GITA_BENCH_SOURCE", Path(__file__).resolve().parents[1] / "src")).resolve()
    if not (source / "gita" / "index" / "builder.py").is_file():
        raise ValueError("selected source must contain the gita runtime")
    for checkout in (source.parent, Path(__file__).resolve().parents[1]):
        if output == checkout or checkout in output.parents:
            raise ValueError("campaign output must be outside source/tooling checkouts")
    if git(source.parent, "rev-parse", "HEAD").decode().strip() != manifest["source_revision"]:
        raise ValueError("requested source_revision does not match selected checkout")
    output.mkdir(parents=True, exist_ok=False)
    write(output / "manifest.json", manifest)
    reserve = manifest.get("max_storage_bytes", 256 * 1024 ** 2) + 1024 ** 3
    if shutil.disk_usage(output).free < reserve:
        write(output / "campaign-refused.json", {"status": "storage_headroom_refused",
                                                "required_free_bytes": reserve, "samples_started": 0})
        raise RuntimeError("insufficient storage headroom; no corpus or timed run started")
    write(output / "environment-request.json", {"action": "environment"})
    environment = execute(output / "environment-request.json", output / "environment.json", 60)
    if environment.get("status") in ("error", "crash", "timeout"):
        write(output / "environment.json", environment)
        raise RuntimeError("environment/policy provisioning failed; no timed sample started")
    environment.update(
        psutil_version=psutil.__version__, ram_bytes=psutil.virtual_memory().total,
        storage={"free_bytes_at_start": shutil.disk_usage(output).free,
                 "filesystem": "operator supplied directory; device characteristics unspecified"},
        notes=manifest["notes"], workload_limits={"parse_timeout_seconds": 5, "page_size": 1000,
                                                "max_storage_bytes": manifest.get("max_storage_bytes", 256 * 1024 ** 2)},
        tooling_digest=sha256(b"".join(
            path.name.encode() + b"\0" + path.read_bytes()
            for path in sorted(Path(__file__).parent.glob("*.py"))
        )).hexdigest(),
    )
    # Reproduce dirty candidates as well as clean commits. Only runtime Python
    # source is archived; no Git config, credentials, corpus or user cache files.
    with ZipFile(output / "runtime-source.zip", "x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*.py")):
            archive.write(path, path.relative_to(source).as_posix())
    environment["runtime_source_archive_sha256"] = sha256((output / "runtime-source.zip").read_bytes()).hexdigest()
    write(output / "environment.json", environment)
    truth = generate(output / "corpus", **manifest["corpus"])
    truth_path = output / "corpus-ledger.json"
    corpus_digest = sha256(truth_path.read_bytes()).hexdigest()
    records = []
    # Rotate scenario order across repetitions, instead of always warming the same first scenario.
    with (output / "samples.jsonl").open("x", encoding="utf-8") as stream:
        for repetition in range(manifest["repetitions"]):
            scenarios = manifest["scenarios"]
            rotation = repetition % len(scenarios)
            for scenario in scenarios[rotation:] + scenarios[:rotation]:
                attempt = output / f"{repetition:03}-{scenario}"
                if shutil.disk_usage(output).free < reserve:
                    write(output / "campaign-refused.json", {
                        "status": "storage_headroom_refused", "required_free_bytes": reserve,
                        "samples_started": len(records), "next_scenario": scenario, "next_repetition": repetition,
                    })
                    raise RuntimeError("remaining campaign refused before clone; prior attempts retained")
                attempt.mkdir()
                repository = attempt / "repository"
                git(output, "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(output / "corpus"), str(repository))
                request = {"action": "run", "repository": str(repository), "truth": str(truth_path),
                           "manifest": manifest, "scenario": scenario}
                previous = None
                if scenario in ("incremental", "noop", "query"):
                    write(attempt / "prepare.json", {**request, "action": "prepare"})
                    prepared = execute(attempt / "prepare.json", attempt / "prepared.json", manifest["max_seconds"])
                    previous = prepared.get("generation")
                    if prepared["status"] != "prepared":
                        sample = {**prepared, "failure_phase": "setup"}
                    else:
                        write(attempt / "request.json", request)
                        sample = execute(attempt / "request.json", attempt / "result.json", manifest["max_seconds"])
                else:
                    write(attempt / "request.json", request)
                    sample = execute(attempt / "request.json", attempt / "result.json", manifest["max_seconds"])
                # The actual publication is observable even when a child dies before its receipt.
                database = repository / ".git" / "gita" / "index.sqlite"
                from sqlite3 import connect, Error
                if database.exists():
                    try:
                        db = connect(database.as_uri() + "?mode=ro", uri=True)
                        try:
                            sample["published_generations_after_close"] = db.execute(
                                "SELECT count(*) FROM generation WHERE state = 'published'",
                            ).fetchone()[0]
                            publication = db.execute(
                                "SELECT published_generation_id FROM scope WHERE name = 'default'",
                            ).fetchone()
                            published = None if publication is None else publication[0]
                            sample["publication"] = (
                                "previous_retained" if sample["status"] != "passed" and published == previous and previous
                                else "unchanged" if published == previous and previous
                                else "not_published" if published is None else "published"
                            )
                        finally:
                            db.close()
                    except Error as error:
                        sample["published_generations_after_close"] = None
                        sample["publication"] = "inspection_unavailable"
                        sample["publication_inspection_error"] = str(error)
                else:
                    sample["published_generations_after_close"] = 0
                    sample["publication"] = "not_published"
                sample.setdefault("counters", {"parsed_files": None, "cached_files": None})
                if sample["status"] == "passed" and any(
                    sample.get("policy", {}).get(field) != environment[field]
                    for field in ("parse_fingerprint", "match_fingerprint")
                ):
                    sample.update(status="policy_drift", completed_seconds=None,
                                  correctness={"passed": False}, error={"code": "policy_drift"})
                sample.update(schema_version=1, scenario=scenario, repetition=repetition,
                              corpus_digest=corpus_digest, source_digest=environment["runtime_source_digest"],
                              cache_state="fresh cloned repo/store; setup store reused only for update/noop/query; "
                                          "new measured process; OS cache uncontrolled/warm")
                sample["storage_after_close_bytes"] = {
                    path.name: path.stat().st_size for path in (
                        database, database.with_name("index.sqlite-wal"), database.with_name("index.sqlite-shm"),
                    ) if path.exists()
                }
                write(attempt / "sample.json", sample)
                stream.write(json.dumps(sample, sort_keys=True, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                records.append(sample)
                print(f"{repetition} {scenario}: {sample['status']} {sample.get('completed_seconds')}", flush=True)
    summary = {"schema_version": 1, "corpus_digest": corpus_digest, "scenarios": {}}
    for scenario in manifest["scenarios"]:
        samples = [record for record in records if record["scenario"] == scenario]
        successful = [record["completed_seconds"] for record in samples if record["status"] == "passed"]
        summary["scenarios"][scenario] = {
            "attempts": len(samples), "passed": len(successful), "failed": len(samples) - len(successful),
            "median_seconds": statistics.median(successful) if successful else None,
            "min_seconds": min(successful) if successful else None,
            "max_seconds": max(successful) if successful else None,
            "uncertainty": "observed range only; no population confidence/large-scale qualification",
        }
    write(output / "summary.json", summary)
    return int(any(record["status"] != "passed" for record in records))


def main(manifest, output):
    try:
        return campaign(manifest, output)
    except (ValueError, OSError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
        print(f"Qualification refused: {error}", file=sys.stderr)
        return 2
