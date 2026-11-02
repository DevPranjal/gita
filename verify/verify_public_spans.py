"""Untimed independent, hand-reviewed public JS/Rust snapshot span checks."""

from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess


def verify(database, original, golden):
    start, end = golden["start_byte"], golden["end_byte"]
    assert 0 <= start < end <= len(original)
    assert sha256(original[start:end]).hexdigest() == golden["source_span_sha256"], "reviewed source changed"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        members = db.execute(
            "SELECT member.commit_digest FROM scope JOIN membership member "
            "ON member.generation_id=scope.published_generation_id ORDER BY sequence DESC LIMIT 1"
        ).fetchall()
        assert members == [(bytes.fromhex(golden["commit"]),)], "snapshot has wrong pinned tip"
        actual = db.execute(
            "SELECT definition.start_byte,definition.end_byte FROM scope "
            "JOIN frontier ON frontier.generation_id=scope.published_generation_id "
            "JOIN file_state ON file_state.id=frontier.file_state_id "
            "JOIN binding ON binding.binding_set_id=file_state.binding_set_id "
            "JOIN definition ON definition.id=binding.definition_id "
            "WHERE file_state.path=? AND definition.terminal_name=?",
            (golden["path"].encode(), golden["name"]),
        ).fetchall()
        assert actual == [(start, end)], "indexed span disagrees with independently reviewed span"
    return {**golden, "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goldens", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    goldens = json.loads(args.goldens.read_text(encoding="utf-8"))
    results = []
    for golden in goldens["spans"]:
        name = golden["repository"]
        original = subprocess.run(
            ["git", "-c", "core.hooksPath=NUL", "-C", str(args.corpus_root / name),
             "show", golden["commit"] + ":" + golden["path"]],
            check=True, capture_output=True, timeout=30,
        ).stdout
        database = args.run / (name + "-snapshot") / "repository" / ".git" / "gita" / "index.sqlite"
        try:
            result = verify(database, original, golden)
        except Exception as error:
            result = {**golden, "status": "failed", "error_type": type(error).__name__,
                      "error": str(error)}
        results.append(result)
    args.output.write_text(json.dumps({"schema_version": 1, "timing": "untimed follow-up",
                                      "results": results}, indent=2, sort_keys=True), encoding="utf-8")
    return int(any(result["status"] != "passed" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
