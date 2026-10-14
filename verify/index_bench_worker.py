"""Owned child for qualification setup and measured work; never executes corpus code."""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from importlib.metadata import version
import os
from pathlib import Path
import platform
import sqlite3
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, os.environ.get("GITA_BENCH_SOURCE", str(Path(__file__).resolve().parents[1] / "src")))

from gita.index import builder, evidence, matching
from gita.index.builder import BuildLimits, build
from gita.index.query import IndexSession
from gita.index.store import IndexStore
from gita.vcs.git import CommittedRepo, Repo
from verify.index_oracle import query_expectations, verify


def limits(config):
    return BuildLimits(workers=config["workers"], max_storage_bytes=config.get("max_storage_bytes", 256 * 1024 ** 2))


def metadata():
    from verify.index_corpus import git
    source = Path(builder.__file__).resolve().parents[2]
    return {
        "schema_version": 1, "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "cpu_count": os.cpu_count(),
        "sqlite": sqlite3.sqlite_version, "git": git(source.parent, "--version").decode().strip(),
        "source_revision": git(source.parent, "rev-parse", "HEAD").decode().strip(),
        "runtime_source_digest": sha256(b"".join(
            path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
            for path in sorted(source.rglob("*.py"))
        )).hexdigest(),
        "parse_fingerprint": evidence.parse_fingerprint().hex(),
        "builder_sha256": sha256(Path(builder.__file__).read_bytes()).hexdigest(),
        "match_fingerprint": matching.match_fingerprint(matching.match_manifest(
            sha256(Path(builder.__file__).read_bytes()).digest(), 64, 10000,
        )).hex(),
        "packages": {name: version(name) for name in ("tree-sitter", "tree-sitter-language-pack")},
        "cache": "OS cache uncontrolled/warm; no global flush",
    }


@contextmanager
def phases(timings):
    replacements = []
    for owner, method, label in (
        (builder, "_walk", "history_walk"), (builder, "_walk_suffix", "suffix_walk"),
        (builder._Builder, "_inventory", "inventory_parse_persist"),
        (builder._Builder, "_structural", "structural_matching"),
        (builder._Builder, "_modules", "module_matching"),
        (builder._Builder, "_suggestions", "candidate_matching"),
        (builder._Builder, "_links", "link_event_writes"),
        (builder._Builder, "_states", "interval_writes"),
        (IndexStore, "publish_generation", "publication_validation"),
        (IndexSession, "_check_policy", "query_policy"),
    ):
        if not hasattr(owner, method):
            continue
        original = getattr(owner, method)

        def measured(*args, _original=original, _label=label, **kwargs):
            start = perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                timings[_label + "_seconds"] = timings.get(_label + "_seconds", 0) + perf_counter() - start
                if _label == "candidate_matching":
                    timings["_candidate_pairs"] = timings.get("_candidate_pairs", 0) + args[0].considered

        replacements.append((owner, method, original))
        setattr(owner, method, measured)
    try:
        yield
    finally:
        for owner, method, original in replacements:
            setattr(owner, method, original)


def run(config, truth, root, scenario, prepare):
    path = IndexStore.locate(Repo(root))
    tip = truth["commits"][-1]
    if prepare:
        ref = truth["commits"][-2] if scenario == "incremental" else tip
        with CommittedRepo(root) as repository, IndexStore.create(path) as store:
            result = build(repository, store, ref=ref, limits=limits(config))
        return {"status": "prepared", "publication": result.status, "generation": result.generation}
    sample = {"schema_version": 1, "scenario": scenario, "status": "started",
              "publication": "not_attempted", "timings": {}, "completed_seconds": None,
              "correctness": {"passed": False}}
    started = perf_counter()
    with CommittedRepo(root) as repository:
        sample["timings"]["repository_open_seconds"] = perf_counter() - started
        store_handle = IndexStore.open(path, read_only=False) if path.exists() else IndexStore.create(path)
        with store_handle as store:
            previous = store.published_generation("default")
            if scenario == "query":
                sample["publication"] = "unchanged"
                (root.parent / "operation.json").write_text(json.dumps(sample), encoding="utf-8")
                start = perf_counter()
                sample["correctness"] = verify(root, truth, store, previous)
                path_bytes, eid = store.connection.execute(
                    "SELECT state.path, binding.eid FROM frontier JOIN file_state state "
                    "ON state.id = frontier.file_state_id "
                    "JOIN binding ON binding.binding_set_id = state.binding_set_id "
                    "WHERE frontier.generation_id = ? ORDER BY state.path, binding.eid LIMIT 1",
                    (previous,),
                ).fetchone()
                expected = query_expectations(truth, store, previous, path_bytes, eid)
                sample["timings"]["verification_seconds"] = perf_counter() - start
                sample["correctness"]["query_response_checks"] = 0
            with phases(sample["timings"]):
                start = perf_counter()
                if scenario == "query":
                    with IndexSession(repository) as session:
                        sample["timings"]["open_seconds"] = perf_counter() - start
                        sample["policy"] = {
                            "parse_fingerprint": session.context.parse_fingerprint.hex(),
                            "match_fingerprint": session.context.match_fingerprint.hex(),
                            "limits": dict(session.context.limits),
                        }
                        for label, operation in (
                            ("entities", lambda: session.entities_in(path_bytes, limit=1000)),
                            ("state", lambda: session.state_at(eid)),
                            ("history", lambda: session.history(eid, limit=1000)),
                        ):
                            durations, answers = [], []
                            for unused in range(10):
                                start = perf_counter()
                                answer = operation()
                                durations.append(perf_counter() - start)
                                answers.append(answer)
                            assert all(answer == answers[0] for answer in answers), "unstable warm query"
                            assert all(answer == expected[label] for answer in answers), (
                                label + " query disagrees with independently verified authored evidence"
                            )
                            sample["correctness"]["query_response_checks"] += len(answers)
                            sample["timings"][label + "_seconds"] = durations
                    generation = previous
                    sample["counters"] = {"parsed_files": 0, "cached_files": 0, "warm_queries": 30}
                    sample["publication"] = "unchanged"
                else:
                    result = build(repository, store, ref=tip,
                                   since=tip if scenario == "snapshot" else None, limits=limits(config))
                    sample["timings"]["operation_seconds"] = perf_counter() - start
                    generation = result.generation
                    sample["publication"] = "unchanged" if previous == generation else "published"
                    sample["build_status"] = result.status
                    sample["policy"] = {
                        "parse_fingerprint": result.context.parse_fingerprint.hex(),
                        "match_fingerprint": result.context.match_fingerprint.hex(),
                        "limits": dict(result.context.limits),
                    }
                    sample["counters"] = {
                        "commits": result.commits, "parsed_files": result.parsed_files,
                        "cached_files": result.cached_files,
                    }
                    if scenario == "noop":
                        assert result.generation == previous and result.parsed_files == result.cached_files == 0
            # Persist actual publication before a potentially failing oracle/timeout.
            sample["counters"]["candidate_pairs_considered"] = sample["timings"].pop("_candidate_pairs", 0)
            (root.parent / "operation.json").write_text(json.dumps(sample), encoding="utf-8")
            if scenario != "query":
                start = perf_counter()
                sample["correctness"] = verify(root, truth, store, generation, snapshot=scenario == "snapshot")
                sample["timings"]["verification_seconds"] = perf_counter() - start
            sample["counters"].update({
                table: store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("definition", "version", "lineage", "occurrence", "lineage_link", "event")
            })
            sample["counters"]["parse_statuses"] = dict(store.connection.execute(
                "SELECT status, count(*) FROM parse_result GROUP BY status",
            ))
            sample["counters"]["coverage"] = [dict(row) for row in store.connection.execute(
                "SELECT category, reason, count(*) AS records, sum(omitted) AS omitted "
                "FROM coverage WHERE generation_id = ? GROUP BY category, reason", (generation,),
            )]
    sample["status"] = "passed"
    sample["completed_seconds"] = sample["timings"].get("operation_seconds", sample["timings"].get("open_seconds"))
    return sample


def main():
    request, output = map(Path, sys.argv[1:])
    config = json.loads(request.read_text(encoding="utf-8"))
    try:
        if config.get("action") == "environment":
            result = metadata()
        else:
            truth = json.loads(Path(config["truth"]).read_text(encoding="utf-8"))
            result = run(config["manifest"], truth, Path(config["repository"]), config["scenario"],
                         config["action"] == "prepare")
    except Exception as error:
        progress = output.parent / "operation.json"
        result = json.loads(progress.read_text()) if progress.exists() else {}
        result.update(status="correctness_failed" if isinstance(error, AssertionError) else "error",
                      completed_seconds=None, correctness={"passed": False},
                      error={"type": type(error).__name__, "code": getattr(error, "code", None),
                             "message": str(error)})
    output.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    return 0 if result.get("status", "passed") in ("passed", "prepared") else 1


if __name__ == "__main__":
    raise SystemExit(main())
