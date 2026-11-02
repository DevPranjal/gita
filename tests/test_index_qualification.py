"""Offline S2 contracts: independent truth, strict inputs and crash recovery."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from verify.index_corpus import generate
from verify.index_qualification import load_manifest, validate_manifest
from verify.compare_index_runs import compare


def test_corpus_is_reproducible_and_authored(tmp_path):
    first = generate(tmp_path / "first", files=3, functions=2, commits=10)
    second = generate(tmp_path / "second", files=3, functions=2, commits=10)
    assert first == second
    assert len(first["commits"]) == 10
    assert [item["operation"] for item in first["ledger"]] == [
        "root", "body", "signature", "rename", "move", "copy", "delete",
        "reintroduce", "cosmetic", "revert",
    ]
    for commit, entry in zip(first["commits"], first["ledger"], strict=True):
        for path, content in entry["writes"].items():
            actual = subprocess.run(
                ["git", "-C", str(tmp_path / "first"), "show", f"{commit}:{path}"],
                check=True, capture_output=True,
            ).stdout
            assert actual == content.encode()
    with pytest.raises(FileExistsError):
        generate(tmp_path / "first", files=3, functions=2, commits=10)


def manifest():
    return {
        "schema_version": 1, "source_revision": "a" * 40,
        "corpus": {"files": 3, "functions": 2, "commits": 10, "profile": "uniform"},
        "scenarios": ["history", "snapshot", "incremental", "noop", "query"],
        "repetitions": 1, "workers": 1, "max_seconds": 120,
        "notes": {"hardware": "CI", "power": "unknown", "contention": "uncontrolled"},
    }


@pytest.mark.parametrize("field,value", [
    ("unknown", 1), ("schema_version", True), ("source_revision", "HEAD"),
    ("repetitions", 0), ("workers", True), ("max_seconds", float("nan")),
    ("max_seconds", 10 ** 1000),
    ("scenarios", ["history", "history"]), ("scenarios", ["unknown"]),
    ("corpus", {"files": 0, "functions": 2, "commits": 10, "profile": "uniform"}),
])
def test_manifest_rejects_invalid_inputs(field, value):
    invalid = deepcopy(manifest())
    invalid[field] = value
    with pytest.raises(ValueError):
        validate_manifest(invalid)


def test_manifest_accepts_valid_inputs():
    assert validate_manifest(manifest()) == manifest()


def test_manifest_rejects_duplicate_fields(tmp_path):
    path = tmp_path / "ambiguous.json"
    path.write_text('{"schema_version": 1, "schema_version": 2}')
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(path)


def test_manifest_cli_rejects_ignored_legacy_options(tmp_path):
    script = Path(__file__).resolve().parents[1] / "verify" / "bench_index.py"
    result = subprocess.run(
        [sys.executable, str(script), "--manifest", "not-read.json", "--output",
         str(tmp_path / "not-created"), "--workers", "2"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2 and "legacy options" in result.stderr
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("corruption", ("status", "semantic", "counters", "repetition", "timing"))
def test_comparison_refuses_unqualified_speed_ratios(tmp_path, corruption):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    environment = {key: "same" for key in (
        "python", "platform", "machine", "processor", "cpu_count", "ram_bytes", "sqlite", "git",
        "packages", "parse_fingerprint", "workload_limits", "notes", "tooling_digest",
    )}
    config = {**manifest(), "scenarios": ["history"]}
    sample = {"scenario": "history", "repetition": 0, "status": "passed", "completed_seconds": 2.0,
              "correctness": {"passed": True, "semantic_digest": "same"},
              "corpus_digest": "same", "counters": {"parsed_files": 3}}
    for root in (baseline, candidate):
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps(config))
        (root / "environment.json").write_text(json.dumps(environment))
        (root / "samples.jsonl").write_text(json.dumps(sample) + "\n")
    assert compare(baseline, candidate)["scenarios"]["history"]["median_ratio"] == 1
    if corruption == "status":
        sample["status"] = "timeout"
    elif corruption == "semantic":
        sample["correctness"]["semantic_digest"] = "different"
    elif corruption == "counters":
        sample["counters"]["parsed_files"] = 1
    elif corruption == "repetition":
        sample["repetition"] = 1
    else:
        sample["completed_seconds"] = 0
    (candidate / "samples.jsonl").write_text(json.dumps(sample) + "\n")
    with pytest.raises(ValueError):
        compare(baseline, candidate)
def test_campaign_passes_all_gates_and_preserves_failed_attempts(tmp_path):
    pytest.importorskip("psutil", reason="install .[bench] for campaign/process-tree sampling")
    requested = manifest()
    requested["source_revision"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    config = tmp_path / "manifest.json"
    config.write_text(json.dumps(requested), encoding="utf-8")
    output = tmp_path / "run"
    script = Path(__file__).resolve().parents[1] / "verify" / "bench_index.py"
    result = subprocess.run(
        [sys.executable, str(script), "--manifest", str(config), "--output", str(output)],
        capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    environment = json.loads((output / "environment.json").read_text())
    archive = output / "runtime-source.zip"
    assert sha256(archive.read_bytes()).hexdigest() == environment["runtime_source_archive_sha256"]
    with ZipFile(archive) as runtime:
        actual = sha256(b"".join(
            name.encode() + b"\0" + runtime.read(name) for name in sorted(runtime.namelist())
        )).hexdigest()
    assert actual == environment["runtime_source_digest"]
    samples = [json.loads(line) for line in (output / "samples.jsonl").read_text().splitlines()]
    assert len(samples) == 5
    assert all(sample["status"] == "passed" and sample["correctness"]["passed"] for sample in samples)
    noop = next(sample for sample in samples if sample["scenario"] == "noop")
    assert noop["counters"]["parsed_files"] == 0
    assert noop["publication"] == "unchanged"
    assert next(sample for sample in samples if sample["scenario"] == "query")["timings"]["open_seconds"] > 0
    result = subprocess.run(
        [sys.executable, str(script), "--manifest", str(config), "--output", str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    requested["max_seconds"] = 0.001
    config.write_text(json.dumps(requested), encoding="utf-8")
    failed = tmp_path / "failed"
    result = subprocess.run(
        [sys.executable, str(script), "--manifest", str(config), "--output", str(failed)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0
    samples = [json.loads(line) for line in (failed / "samples.jsonl").read_text().splitlines()]
    assert len(samples) == 5
    assert all(sample["status"] == "timeout" and sample["completed_seconds"] is None for sample in samples)
