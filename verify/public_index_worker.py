"""Measured child for public Git corpora; never imports or executes corpus code."""

from __future__ import annotations

import ast
import cProfile
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import blake2b, sha1, sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, os.environ["GITA_BENCH_SOURCE"])

from gita.index import builder, evidence
from gita.index.builder import BuildLimits, build
from gita.index.query import IndexSession
from gita.index.store import IndexStore
from gita.vcs.git import CommittedRepo, Repo
from verify.index_oracle import canonical_digest, validate_records


@contextmanager
def profile_build(result, output, enabled):
    """Identical bounded instrumentation for pinned baseline and candidate."""
    if not enabled:
        yield
        return
    from verify.index_bench_worker import phases

    counters = result.setdefault("work_counters", {})
    replacements = []
    for name in ("iter_tree", "changes", "tree_changes"):
        if not hasattr(CommittedRepo, name):
            continue
        original = getattr(CommittedRepo, name)

        def counted(*args, _original=original, _name=name, **kwargs):
            counters[_name + "_calls"] = counters.get(_name + "_calls", 0) + 1
            for page in _original(*args, **kwargs):
                counters[_name + "_entries"] = counters.get(_name + "_entries", 0) + len(page)
                yield page

        replacements.append((CommittedRepo, name, original))
        setattr(CommittedRepo, name, counted)
    original = builder._Builder._metadata

    def metadata(*args, **kwargs):
        counters["metadata_definitions"] = counters.get("metadata_definitions", 0) + len(args[1].definitions)
        return original(*args, **kwargs)

    replacements.append((builder._Builder, "_metadata", original))
    builder._Builder._metadata = metadata
    original_inventory = builder._Builder._inventory

    def inventory(instance, *args, **kwargs):
        before = instance.db.total_changes
        try:
            return original_inventory(instance, *args, **kwargs)
        finally:
            counters["inventory_sql_row_changes"] = (
                counters.get("inventory_sql_row_changes", 0) + instance.db.total_changes - before
            )

    replacements.append((builder._Builder, "_inventory", original_inventory))
    builder._Builder._inventory = inventory
    profiler = cProfile.Profile()
    try:
        with phases(result.setdefault("phase_timings", {})):
            profiler.enable()
            yield
    finally:
        profiler.disable()
        profiler.dump_stats(str(output.with_suffix(".prof")))
        for owner, name, original in replacements:
            setattr(owner, name, original)


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=NUL", "-C", str(root), *args],
        check=True, capture_output=True, timeout=30,
    ).stdout


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def ast_spans(source):
    """Independent Python UTF-8 def/class coordinates, excluding decorators."""
    offsets, position = [], 0
    for line in source.splitlines(keepends=True):
        offsets.append(position)
        position += len(line)
    result = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = offsets[node.lineno - 1] + node.col_offset
        result[node.name] = (start, offsets[node.end_lineno - 1] + node.end_col_offset)
    return result


def source_check(original, row, digest):
    assert sha1(b"blob " + str(len(original)).encode() + b"\0" + original).digest() == row["blob_digest"]
    start, end = row["start_byte"], row["end_byte"]
    assert 0 <= start <= end <= len(original), "source span outside original Git blob"
    selected = original[start:end]
    assert blake2b(selected, digest_size=32).digest() == digest, "source digest differs from Git bytes"
    return selected


def tree_entries(root, commit):
    entries = {}
    for entry in git(root, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not entry:
            continue
        header, path = entry.split(b"\t", 1)
        mode, kind, oid = header.split()
        entries[path] = (bytes.fromhex(oid.decode("ascii")), int(mode, 8), kind.decode("ascii"))
    return entries


def member_entry(entries, row):
    actual = entries.get(row["path"])
    assert actual is not None, "sampled path missing from member Git tree entry"
    assert actual == (row["blob_digest"], row["mode"], row["object_type"]), (
        "stored interval disagrees with member Git tree entry blob/mode/type"
    )
    return actual


def verify_sources(root, store, generation, expected_commits):
    """Sample original bytes at every member, plus independent Python tip spans.

    The database chooses evidence to inspect, not expected byte values or lineage.
    This is not an authored operation ledger or a completeness oracle.
    """
    db = store.connection
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    members = list(db.execute(
        "SELECT sequence, commit_digest FROM membership WHERE generation_id=? ORDER BY sequence",
        (generation,),
    ))
    assert [row["commit_digest"].hex() for row in members] == expected_commits, "wrong bounded membership"
    digests = validate_records(store, generation)
    cache, byte_checks, span_checks, tree_checks, reviewed = {}, 0, 0, 0, []
    sample_query = (
        "SELECT state.path, state.blob_digest, state.mode, state.object_type, definition.* FROM file_state state "
        "JOIN binding ON binding.binding_set_id=state.binding_set_id "
        "JOIN definition ON definition.id=binding.definition_id "
        "WHERE state.generation_id=? AND state.from_seq<=? "
        "AND (state.to_seq IS NULL OR state.to_seq>?) AND definition.synthetic_root=0 "
        "ORDER BY state.path, definition.start_byte, definition.id LIMIT ?"
    )
    for member in members:
        sequence = member["sequence"]
        entries = tree_entries(root, member["commit_digest"].hex())
        for row in db.execute(sample_query, (generation, sequence, sequence, 8)):
            oid = member_entry(entries, row)[0].hex()
            tree_checks += 1
            if oid not in cache:
                cache[oid] = git(root, "cat-file", "blob", oid)
            source_check(cache[oid], row, digests[row["version_id"]])
            byte_checks += 1
    # Top-level definitions of the first eight indexed Python files at the tip.
    sequence = members[-1]["sequence"]
    python_files = db.execute(
        "SELECT state.path, state.blob_digest, state.mode, state.object_type, state.binding_set_id FROM file_state state "
        "WHERE state.generation_id=? AND state.from_seq<=? "
        "AND (state.to_seq IS NULL OR state.to_seq>?) AND state.status='present' "
        "AND CAST(state.path AS TEXT) LIKE '%.py' ORDER BY state.path LIMIT 8",
        (generation, sequence, sequence),
    ).fetchall()
    for file in python_files:
        oid = member_entry(entries, file)[0].hex()
        tree_checks += 1
        original = cache.get(oid)
        if original is None:
            original = cache[oid] = git(root, "cat-file", "blob", oid)
        expected = ast_spans(original)
        for row in db.execute(
            "SELECT * FROM definition JOIN binding ON binding.definition_id=definition.id "
            "WHERE binding.binding_set_id=? AND definition.parent_id IS NOT NULL",
            (file["binding_set_id"],),
        ):
            if row["terminal_name"] in expected and tuple(expected[row["terminal_name"]]) == (
                row["start_byte"], row["end_byte"],
            ):
                span_checks += 1
                if len(reviewed) < 8:
                    reviewed.append({"path": file["path"].decode("utf-8"),
                                     "name": row["terminal_name"], "start_byte": row["start_byte"],
                                     "end_byte": row["end_byte"]})
        # Only top-level names are independently covered; nested definitions
        # with the same terminal name must not be counted as expected matches.
        actual = {(row["terminal_name"], row["start_byte"], row["end_byte"])
                  for row in db.execute(
                      "SELECT definition.* FROM definition JOIN binding "
                      "ON binding.definition_id=definition.id WHERE binding.binding_set_id=?",
                      (file["binding_set_id"],),
                  )}
        assert all((name, *span) in actual for name, span in expected.items()), "Python AST span missing"
    return {
        "stored_contracts": "passed", "sql_integrity": "passed", "sql_foreign_keys": "passed",
        "exact_member_scope": "passed", "git_blob_and_source_digest_checks": byte_checks,
        "git_member_tree_entry_checks": tree_checks,
        "independent_python_ast_span_checks": span_checks, "python_span_examples": reviewed,
        "canonical_digest": canonical_digest(db, generation),
        "lineage_truth": "not independently established for these real repositories",
        "non_python_spans": "byte/digest checked; no independent JS/Rust grammar oracle",
        "inventory_completeness": "not independently established",
    }


def metadata(source):
    import psutil

    return {
        "python": platform.python_version(), "platform": platform.platform(),
        "machine": platform.machine(), "processor": platform.processor(),
        "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
        "ram_bytes": psutil.virtual_memory().total, "sqlite": sqlite3.sqlite_version,
        "git": git(source.parent, "--version").decode().strip(),
        "source_revision": git(source.parent, "rev-parse", "HEAD").decode().strip(),
        "source_dirty": bool(git(source.parent, "status", "--porcelain", "--", "src")),
        "runtime_source_sha256": sha256(b"".join(
            path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
            for path in sorted(source.rglob("*.py"))
        )).hexdigest(),
        "builder_sha256": sha256(Path(builder.__file__).read_bytes()).hexdigest(),
        "parse_fingerprint": evidence.parse_fingerprint().hex(),
        "packages": {name: version(name) for name in
                     ("tree-sitter", "tree-sitter-language-pack", "psutil")},
        "grammars_preflight": {name: bool(evidence.get_parser(name))
                              for name in ("python", "javascript", "rust")},
        "cache": "fresh SQLite per cold scenario; OS/Git caches uncontrolled and warm",
        "power": "uncontrolled", "contention": "shared host; parent agent active",
    }


def run(config, output):
    root = Path(config["repository"])
    database = IndexStore.locate(Repo(root))
    scenario = config["scenario"]
    progress = output.with_suffix(".progress.json")
    result = {"scenario": scenario, "status": "started", "publication": "not_attempted",
              "completed_seconds": None, "operation_seconds": None, "correctness": None}
    write(progress, result)
    limits = BuildLimits(workers=config["workers"], max_storage_bytes=config["max_storage_bytes"])
    with CommittedRepo(root) as repository:
        with (IndexStore.open(database, read_only=scenario == "query") if database.exists()
              else IndexStore.create(database)) as store:
            previous = store.published_generation("default")
            if scenario == "query":
                assert previous is not None
                start = perf_counter()
                with IndexSession(repository) as session:
                    result["session_open_seconds"] = perf_counter() - start
                    row = store.connection.execute(
                        "SELECT state.path, binding.eid FROM frontier JOIN file_state state "
                        "ON state.id=frontier.file_state_id "
                        "JOIN binding ON binding.binding_set_id=state.binding_set_id "
                        "JOIN definition ON definition.id=binding.definition_id "
                        "WHERE frontier.generation_id=? AND definition.synthetic_root=0 "
                        "ORDER BY state.path, definition.start_byte LIMIT 1", (previous,),
                    ).fetchone()
                    assert row is not None, "no representative query entity"
                    result["warm_query_seconds"] = {}
                    for label, operation in (
                        ("entities", lambda: session.entities_in(row["path"], limit=100)),
                        ("state", lambda: session.state_at(row["eid"])),
                        ("history", lambda: session.history(row["eid"], limit=100)),
                    ):
                        answers, durations = [], []
                        for _ in range(5):
                            start = perf_counter()
                            answers.append(operation())
                            durations.append(perf_counter() - start)
                        assert all(answer == answers[0] for answer in answers), "unstable query"
                        result["warm_query_seconds"][label] = durations
                    answer = session.entities_in(row["path"], limit=100)
                    source_checks = 0
                    for occurrence in answer["results"][:8]:
                        start = perf_counter()
                        selected = session.source(occurrence)
                        result.setdefault("source_read_seconds", []).append(perf_counter() - start)
                        original = git(root, "show", config["ref"] + ":" + row["path"].decode())
                        span = occurrence["span"]
                        assert selected == original[span["start_byte"]:span["end_byte"]]
                        source_checks += 1
                    result["query_source_checks"] = source_checks
                    result["query_path"] = row["path"].decode()
                    result["query_lineage_check"] = "stable only; no independent event/identity truth"
                generation = previous
                result["publication"] = "unchanged"
            else:
                start = perf_counter()
                with profile_build(result, output, config.get("profile", False)):
                    built = build(repository, store, ref=config["ref"], since=config["anchor"], limits=limits)
                result["operation_seconds"] = perf_counter() - start
                generation = built.generation
                result.update(
                    publication="unchanged" if previous == generation else "published",
                    build_status=built.status,
                    counters={"commits": built.commits, "parsed_files": built.parsed_files,
                              "cached_files": built.cached_files},
                    policy={"parse_fingerprint": built.context.parse_fingerprint.hex(),
                            "match_fingerprint": built.context.match_fingerprint.hex(),
                            "limits": dict(built.context.limits)},
                    operational_settings=asdict(limits),
                )
                if scenario == "noop":
                    assert generation == previous and built.parsed_files == built.cached_files == 0
            result["coverage"] = [dict(row) for row in store.connection.execute(
                "SELECT category, reason, count(*) AS records, sum(omitted) AS omitted "
                "FROM coverage WHERE generation_id=? GROUP BY category, reason ORDER BY category, reason",
                (generation,),
            )]
            result["parse_statuses"] = dict(store.connection.execute(
                "SELECT status,count(*) FROM parse_result GROUP BY status"))
            result["row_counts"] = {name: store.connection.execute(
                f"SELECT count(*) FROM {name}").fetchone()[0]
                for name in ("definition", "version", "lineage", "occurrence", "lineage_link", "event")}
            write(progress, result)
            start = perf_counter()
            result["correctness"] = verify_sources(root, store, generation, config["commits"])
            result["verification_seconds"] = perf_counter() - start
            result["status"] = "passed"
            result["completed_seconds"] = result["operation_seconds"]
    result["database_bytes"] = database.stat().st_size
    return result


def main():
    request, output = map(Path, sys.argv[1:])
    config = json.loads(request.read_text(encoding="utf-8"))
    try:
        result = metadata(Path(os.environ["GITA_BENCH_SOURCE"])) if config["scenario"] == "environment" else run(config, output)
    except Exception as error:
        progress = output.with_suffix(".progress.json")
        result = json.loads(progress.read_text()) if progress.exists() else {}
        result.update(status="correctness_failed" if isinstance(error, AssertionError) else "error",
                      completed_seconds=None, error={"type": type(error).__name__,
                      "code": getattr(error, "code", None), "message": str(error)})
    write(output, result)
    return int(result.get("status") in ("error", "correctness_failed"))


if __name__ == "__main__":
    raise SystemExit(main())
