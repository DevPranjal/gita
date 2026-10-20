"""Bounded public-corpus receipts are separate from generated lineage truth."""

from hashlib import blake2b, sha1
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from uuid import uuid4

import pytest

pytest.importorskip("psutil", reason="install .[bench] for public benchmark supervision")

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("GITA_BENCH_SOURCE", str(ROOT / "src"))

from verify.bench_public_index import execute, fingerprint, scope, validate_manifest
from verify.public_index_worker import ast_spans, run, source_check


@pytest.fixture
def owned_root():
    # Explicit project-local fixture, not the host's shared temporary directory.
    root = ROOT / ".public-benchmark-test-fixtures" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        def writable_remove(operation, path, error):
            os.chmod(path, stat.S_IWRITE)
            operation(path)

        shutil.rmtree(root, onerror=writable_remove)
        try:
            root.parent.rmdir()
        except OSError:
            pass


def test_source_check_uses_original_git_blob_and_span():
    source = b"prefix\nreturn 42\n"
    row = {"blob_digest": sha1(b"blob 17\0" + source).digest(), "start_byte": 7, "end_byte": 16}
    digest = blake2b(source[7:16], digest_size=32).digest()
    assert source_check(source, row, digest) == b"return 42"
    with pytest.raises(AssertionError):
        source_check(source.replace(b"42", b"43"), row, digest)
    with pytest.raises(AssertionError, match="digest"):
        source_check(source, row, b"\0" * 32)
    with pytest.raises(AssertionError, match="outside"):
        source_check(source, {**row, "end_byte": 100}, digest)


def test_ast_spans_are_utf8_def_coordinates_excluding_decorators():
    source = '# é\n@decorator\nasync def f():\n    return "λ"\n'.encode()
    expected = source.index(b"async def"), len(source.rstrip(b"\n"))
    assert ast_spans(source) == {"f": expected}


@pytest.mark.parametrize("key,value", [
    ("source_revision", "HEAD"), ("workers", True), ("workers", 8),
    ("history_commits", 1), ("total_seconds", 901), ("max_seconds", float("nan")),
    ("repositories", [{"name": "private", "tip": "a" * 40}]),
])
def test_manifest_rejects_unpinned_or_unbounded_inputs(key, value):
    manifest = json.loads((ROOT / "verify" / "results" / "public-20261014.manifest.json").read_text())
    assert validate_manifest(manifest) == manifest
    with pytest.raises(ValueError):
        validate_manifest({**manifest, key: value})


@pytest.mark.parametrize("kind", ["timeout", "crash"])
def test_supervisor_retains_timeout_and_crash_receipts(owned_root, kind):
    worker = owned_root / "child.py"
    worker.write_text(
        "import json, pathlib, sys, time\n"
        "output=pathlib.Path(sys.argv[2])\n"
        "output.with_suffix('.progress.json').write_text(json.dumps("
        "{'publication':'published','operation_seconds':0.1,'completed_seconds':0.1}))\n"
        + ("time.sleep(120)\n" if kind == "timeout" else "sys.exit(17)\n"),
        encoding="utf-8",
    )
    request, output = owned_root / "request.json", owned_root / "result.json"
    request.write_text('{"scenario":"test"}')
    result = execute(request, output, 0.3 if kind == "timeout" else 5, ROOT / "src", worker)
    assert result["status"] == kind
    assert result["publication"] == "published"
    assert result["completed_seconds"] is None
    assert result["operation_seconds"] == 0.1
    assert result["resources"]["cleanup_complete"]
    assert result["resources"]["temp_spool_bytes"] is None
    assert result["error"]["code"] == ("deadline" if kind == "timeout" else "child_exit")
    assert json.loads(output.read_text()) == result


def git(root, *arguments):
    return subprocess.run(["git", "-c", "core.hooksPath=NUL", "-C", str(root), *arguments],
                          check=True, capture_output=True).stdout


def test_scope_snapshot_append_noop_and_source_gates(owned_root):
    repository = owned_root / "repo"
    repository.mkdir()
    git(repository, "init", "-q")
    for value in range(4):
        (repository / "fixture.py").write_text(f"def f():\n    return {value}\n")
        git(repository, "add", "fixture.py")
        git(repository, "-c", "user.name=Public Benchmark", "-c", "user.email=benchmark@example.invalid",
            "commit", "-q", "-m", f"fixture {value}")
    tip = git(repository, "rev-parse", "HEAD").decode().strip()
    commits = scope(repository, tip, 2)
    assert len(commits) == 3
    with pytest.raises(ValueError, match="bounded"):
        scope(repository, tip, 20)
    before = fingerprint(repository)
    config = {"repository": str(repository), "workers": 1, "max_storage_bytes": 64 * 1024 ** 2,
              "anchor": commits[0], "ref": commits[-2], "commits": commits[:-1], "scenario": "history"}
    history = run(config, owned_root / "history.json")
    assert history["status"] == "passed"
    assert history["counters"]["commits"] == 2
    appended = run({**config, "ref": tip, "commits": commits, "scenario": "append"},
                   owned_root / "append.json")
    assert appended["status"] == "passed" and appended["publication"] == "published"
    noop = run({**config, "ref": tip, "commits": commits, "scenario": "noop"},
               owned_root / "noop.json")
    assert noop["status"] == "passed" and noop["publication"] == "unchanged"
    assert noop["counters"]["parsed_files"] == noop["counters"]["cached_files"] == 0
    query = run({**config, "ref": tip, "commits": commits, "scenario": "query"},
                owned_root / "query.json")
    assert query["status"] == "passed" and query["query_source_checks"] == 2
    assert appended["correctness"]["canonical_digest"] == noop["correctness"]["canonical_digest"]
    assert appended["correctness"]["independent_python_ast_span_checks"] == 1
    assert fingerprint(repository) == before


def test_reviewed_golden_requires_exact_indexed_span(owned_root):
    import sqlite3
    from hashlib import sha256
    from verify.verify_public_spans import verify

    database = owned_root / "snapshot.sqlite"
    source = b"function f() {}"
    golden = {"commit": "a" * 40, "path": "fixture.js", "name": "f", "start_byte": 0,
              "end_byte": len(source), "source_span_sha256": sha256(source).hexdigest()}
    with closing(sqlite3.connect(database)) as db, db:
        db.executescript(
            "CREATE TABLE scope(published_generation_id TEXT);"
            "CREATE TABLE membership(generation_id TEXT, commit_digest BLOB, sequence INTEGER);"
            "CREATE TABLE frontier(generation_id TEXT, file_state_id INTEGER);"
            "CREATE TABLE file_state(id INTEGER, binding_set_id TEXT, path BLOB);"
            "CREATE TABLE binding(binding_set_id TEXT, definition_id TEXT);"
            "CREATE TABLE definition(id TEXT, terminal_name TEXT, start_byte INTEGER, end_byte INTEGER);"
        )
        db.execute("INSERT INTO scope VALUES ('g')")
        db.execute("INSERT INTO membership VALUES ('g', ?, 0)", (bytes.fromhex("a" * 40),))
        db.execute("INSERT INTO frontier VALUES ('g', 1)")
        db.execute("INSERT INTO file_state VALUES (1, 'b', ?)", (b"fixture.js",))
        db.execute("INSERT INTO binding VALUES ('b', 'd')")
        db.execute("INSERT INTO definition VALUES ('d', 'f', 0, ?)", (len(source),))
    assert verify(database, source, golden)["status"] == "passed"
    with pytest.raises(AssertionError, match="source changed"):
        verify(database, b"function x() {}", golden)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE definition SET start_byte=1")
    with pytest.raises(AssertionError, match="indexed span"):
        verify(database, source, golden)


def test_public_export_retains_failures_and_redacts_paths(owned_root):
    from verify.export_public_index import export

    run_root = owned_root / "private-run"
    run_root.mkdir()
    attempt = {"status": "timeout", "completed_seconds": None, "operation_seconds": 1.25,
               "error": {"message": r"Cannot open C:\Users\private\index.sqlite"},
               "resources": {"temp_spool_bytes": None}}
    (run_root / "summary.json").write_text(json.dumps({"attempts": [attempt]}))
    output = owned_root / "public.json"
    exported = export([run_root], [], output, {"public_index_worker.py": "a" * 64})
    actual = exported["campaigns"][0]["attempts"][0]
    assert actual["status"] == "timeout" and actual["completed_seconds"] is None
    assert actual["operation_seconds"] == 1.25
    assert actual["resources"]["temp_spool_bytes"] is None
    assert "private" not in output.read_text()
    assert "local-path-redacted" in actual["error"]["message"]


def test_member_tree_rejects_stale_interval_at_intermediate_commit(owned_root, monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    from verify import public_index_worker as worker

    repository = owned_root / "repo"
    repository.mkdir()
    git(repository, "init", "-q")
    commits, blobs, contents = [], [], []
    for value in range(3):
        contents.append(f"function f() {{ return {value}; }}".encode())
        (repository / "fixture.js").write_bytes(contents[-1])
        git(repository, "add", "fixture.js")
        git(repository, "-c", "user.name=Public Benchmark", "-c", "user.email=benchmark@example.invalid",
            "commit", "-q", "-m", str(value))
        commits.append(git(repository, "rev-parse", "HEAD").decode().strip())
        blobs.append(bytes.fromhex(git(repository, "rev-parse", "HEAD:fixture.js").decode().strip()))
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            "CREATE TABLE membership(generation_id TEXT, sequence INTEGER, commit_digest BLOB);"
            "CREATE TABLE file_state(generation_id TEXT, path BLOB, blob_digest BLOB, "
            "mode INTEGER, object_type TEXT, binding_set_id TEXT, from_seq INTEGER, "
            "to_seq INTEGER, status TEXT);"
            "CREATE TABLE binding(binding_set_id TEXT, definition_id TEXT);"
            "CREATE TABLE definition(id TEXT, version_id TEXT, synthetic_root INTEGER, "
            "start_byte INTEGER, end_byte INTEGER);"
        )
        db.executemany("INSERT INTO membership VALUES ('g', ?, ?)",
                       [(index, bytes.fromhex(commit)) for index, commit in enumerate(commits)])
        # Internally valid old evidence extends through the intermediate member.
        # Isolate the independent Git oracle from the unrelated record decoder.
        digests = {}
        for index, start, end in ((0, 0, 2), (2, 2, None)):
            key = str(index)
            db.execute("INSERT INTO file_state VALUES ('g', ?, ?, ?, 'blob', ?, ?, ?, 'present')",
                       (b"fixture.js", blobs[index], 0o100644, key, start, end))
            db.execute("INSERT INTO binding VALUES (?, ?)", (key, key))
            db.execute("INSERT INTO definition VALUES (?, ?, 0, 0, ?)",
                       (key, key, len(contents[index])))
            digests[key] = blake2b(contents[index], digest_size=32).digest()
        monkeypatch.setattr(worker, "validate_records", lambda *args: digests)
        monkeypatch.setattr(worker, "canonical_digest", lambda *args: "unchanged-internal-records")
        with pytest.raises(AssertionError, match="Git tree entry"):
            worker.verify_sources(repository, SimpleNamespace(connection=db), "g", commits)


def test_equivalence_disagreement_fails_exit_gate():
    from verify.bench_public_index import exit_code

    summary = {"attempts": [{"status": "passed", "scenario_valid": True}],
               "equivalence": [{"status": "equal"}]}
    assert exit_code(summary) == 0
    assert exit_code({**summary, "equivalence": [{"status": "different"}]}) == 1
    assert exit_code({**summary, "attempts": [{"status": "timeout"}]}) == 1


@pytest.mark.parametrize("path", [
    "/home/alice/private/index.sqlite", "/mnt/work/private/index.sqlite",
    "/Users/alice/private/report.json", "/var/lib/private/index.sqlite",
])
def test_public_sanitizer_redacts_unix_paths_without_touching_urls(path):
    from verify.export_public_index import sanitize

    evidence = {"error": f"Unable to read {path}", "url": "https://github.com/pallets/flask.git",
                "file_url": "https://example.org/home/alice/docs",
                "repo_path": "examples/javascript/tests/conftest.py",
                "seconds": 1.25}
    actual = sanitize(evidence)
    assert path not in actual["error"]
    assert "<local-path-redacted>" in actual["error"]
    assert actual["url"] == evidence["url"] and actual["file_url"] == evidence["file_url"]
    assert actual["repo_path"] == evidence["repo_path"] and actual["seconds"] == 1.25


@pytest.mark.parametrize("path", [r"C:\Users\alice\private\index.sqlite",
                                r"\\server\private\index.sqlite", "/home/alice/private/index.sqlite"])
def test_public_sanitizer_preserves_url_in_error_containing_local_path(path):
    from verify.export_public_index import sanitize

    url = "https://github.com/pallets/flask/blob/main/src/flask/app.py"
    sanitized = sanitize(f"Could not open {path}; reference {url}")
    assert path not in sanitized and url in sanitized


def test_member_tree_checks_mode_and_path_not_only_blob():
    from verify.public_index_worker import member_entry

    row = {"path": b"fixture.js", "blob_digest": b"x" * 20, "mode": 0o100644, "object_type": "blob"}
    assert member_entry({row["path"]: (row["blob_digest"], row["mode"], "blob")}, row)
    with pytest.raises(AssertionError, match="mode/type"):
        member_entry({row["path"]: (row["blob_digest"], 0o100755, "blob")}, row)
    with pytest.raises(AssertionError, match="missing"):
        member_entry({}, row)


def test_recheck_missing_retained_store_is_explicitly_provisional(owned_root):
    from hashlib import sha256
    from gita.index.evidence import parse_fingerprint
    from verify.recheck_public_index import recheck

    source = ROOT / "src"
    environment = {
        "source_revision": git(ROOT, "rev-parse", "HEAD").decode().strip(),
        "parse_fingerprint": parse_fingerprint().hex(),
        "runtime_source_sha256": sha256(b"".join(
            path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
            for path in sorted(source.rglob("*.py"))
        )).hexdigest(),
    }
    report = {"campaigns": [{"campaign": 1, "environment": environment,
                            "attempts": [{"repository": "express", "scenario": "snapshot",
                                          "status": "passed"}]}]}
    receipt = recheck(report, [owned_root / "missing-retained-run"], owned_root / "recheck.json")
    assert receipt["status"] == "provisional"
    assert receipt["results"][0]["status"] == "not_rechecked"
    assert receipt["results"][0]["provisional"] is True
    assert receipt["results"][0]["error"]["type"] == "FileNotFoundError"
    assert "git_member_tree_entry_checks" not in receipt["results"][0]
