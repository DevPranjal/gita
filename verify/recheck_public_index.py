"""Verification-only recheck of retained PASS publications; never builds an index."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, os.environ.get("GITA_BENCH_SOURCE", str(Path(__file__).resolve().parents[1] / "src")))

from gita.index.evidence import parse_fingerprint
from gita.index.store import IndexStore
from verify.bench_public_index import fingerprint
from verify.export_public_index import sanitize
from verify.public_index_worker import git, verify_sources


def recheck(report, run_roots, output):
    source = Path(__file__).resolve().parents[1] / "src"
    runtime_digest = sha256(b"".join(
        path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes()
        for path in sorted(source.rglob("*.py"))
    )).hexdigest()
    receipt = {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "timing": "verification-only; no new indexing or query latency measurement",
        "original_timings": "predate independent member commit/path tree binding; preserved unchanged",
        "source_revision": git(source.parent, "rev-parse", "HEAD").decode().strip(),
        "runtime_source_sha256": runtime_digest,
        "parse_fingerprint": parse_fingerprint().hex(),
        "tooling_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("public_index_worker.py", "bench_public_index.py", "export_public_index.py",
                         "recheck_public_index.py", "index_oracle.py")
        },
        "results": [],
    }
    for campaign, run_root in zip(report["campaigns"], run_roots, strict=True):
        environment = campaign["environment"]
        pinned = (runtime_digest == environment["runtime_source_sha256"]
                  and receipt["parse_fingerprint"] == environment["parse_fingerprint"]
                  and receipt["source_revision"] == environment["source_revision"]
                  and not git(source.parent, "status", "--porcelain", "--", "src"))
        for attempt in campaign["attempts"]:
            if attempt["status"] != "passed":
                continue
            name, scenario = attempt["repository"], attempt["scenario"]
            group = "history" if scenario in ("append", "noop", "query") else scenario
            root = run_root / (name + "-" + group)
            database = root / "repository" / ".git" / "gita" / "index.sqlite"
            request = root / (scenario + ".request.json")
            result = {"campaign": campaign["campaign"], "repository": name, "scenario": scenario,
                      "status": "not_rechecked", "provisional": True}
            try:
                if not pinned:
                    raise ValueError("runtime source or parser pin differs from original campaign")
                if not database.is_file() or not request.is_file():
                    raise FileNotFoundError("retained PASS database or request unavailable")
                config = json.loads(request.read_text(encoding="utf-8"))
                repository = root / "repository"
                before_git = fingerprint(repository)
                before_database = sha256(database.read_bytes()).hexdigest()
                with IndexStore.open(database, read_only=True) as store:
                    # The shared history store has advanced to 21 members.
                    # Recheck the retained 20-member predecessor, not its tip.
                    generations = store.connection.execute(
                        "SELECT id FROM generation WHERE state='published' AND tip_digest=?",
                        (bytes.fromhex(config["ref"]),),
                    ).fetchall()
                    if len(generations) != 1:
                        raise ValueError("exact retained publication unavailable or ambiguous")
                    generation = generations[0]["id"]
                    checks = verify_sources(repository, store, generation, config["commits"])
                unchanged = (before_git == fingerprint(repository)
                             and before_database == sha256(database.read_bytes()).hexdigest())
                assert unchanged, "verification modified retained Git or database"
                # A predecessor store now also contains later immutable records;
                # its canonical digest is not compared to the old timing digest.
                result.update(
                    status="passed", provisional=False, requested_commit_count=len(config["commits"]),
                    anchor=config["commits"][0], tip=config["ref"],
                    git_member_tree_entry_checks=checks["git_member_tree_entry_checks"],
                    git_blob_and_source_digest_checks=checks["git_blob_and_source_digest_checks"],
                    independent_python_ast_span_checks=checks["independent_python_ast_span_checks"],
                    sql_integrity=checks["sql_integrity"], sql_foreign_keys=checks["sql_foreign_keys"],
                    stored_contracts=checks["stored_contracts"], exact_member_scope=checks["exact_member_scope"],
                    retained_git_and_database_unchanged=unchanged,
                    source_coverage="sampled evidence only; no inventory completeness or lineage truth claim",
                )
            except Exception as error:
                result.update(status="failed" if isinstance(error, AssertionError) else "not_rechecked",
                              error={"type": type(error).__name__, "message": str(error)})
            receipt["results"].append(result)
            output.write_text(json.dumps(sanitize(receipt), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    receipt["status"] = "passed" if receipt["results"] and all(
        row["status"] == "passed" for row in receipt["results"]) else "provisional"
    output.write_text(json.dumps(sanitize(receipt), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run", action="append", type=Path, required=True,
                        help="Retained campaign output roots, in original campaign order")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    return int(recheck(report, args.run, args.output)["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
