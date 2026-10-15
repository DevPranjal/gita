"""Pinned public corpus provisioning. Labels are written only to grader storage."""
from __future__ import annotations

import json
from pathlib import Path
import re
import urllib.request

from .common import (digest, file_digest, oid, pr_identity, read_json, require,
                     task_identity, write_json)

PIN = "c17b05be9c90b85a1fc012ce39e455e2d09a7255"
UPSTREAM = "https://raw.githubusercontent.com/review-bench/ReviewBench/" + PIN + "/"
OFFICIAL_FILES = (
    "corpus/manifest.json", "corpus/test/test.json", "AGENT_CONTRACT.md", "LICENSE",
    "scripts/lib/match-types.ts", "scripts/eval/scorer.ts",
    "scripts/eval/matcher.ts", "scripts/classifier/prompts.ts",
    "scripts/eval/prompt-format.ts", "tests/scorer.test.ts", "tests/matcher-response.test.ts",
    "package.json",
)
PINNED_DIGESTS = {
    "corpus/manifest.json": "e74656b8c1c4bbab5ce85f1fcb742a7aaad550a4d7bd46e1f83a0746807bfa02",
    "corpus/test/test.json": "0f277447878a0b02f587c0b40a76a74985d9d1db3cb212d9dfbc97b1d1270f60",
    "AGENT_CONTRACT.md": "e4badd7f08505747317e5ee6a3d21703ea54fa24b4fd7e754744898c9a97d66c",
    "LICENSE": "18a24e58967e165bcdd21030b16b85673e025439d55686d3af642c8f815d5840",
    "scripts/lib/match-types.ts": "082ccf8a0e29b88cc043e435eccf04fbd31fb157268896ceaf6cbbf713339ad4",
    "scripts/eval/scorer.ts": "d155f1ca20d912f1a045d323c937dd7a8d63346ba6b6934366f88337afd5a754",
    "scripts/eval/matcher.ts": "0d87235f2092e33732e38896f8d8f09a10b9f7e923cbd67017a6f956dbf3e3c9",
    "scripts/classifier/prompts.ts": "fa6c6ced1200f377d92d0d6e40dcad4e8b9d2e976fd81f32179d085de5a2f02d",
    "scripts/eval/prompt-format.ts": "ec31f56e14a2d2b656729d781605a21e04f34062bc895f78de5a6ce509372b69",
    "tests/scorer.test.ts": "c012ff9a44819ddfc7830d20dfa3db8d6136c41f6d73b19ef1bdedc75e117e30",
    "tests/matcher-response.test.ts": "f73ce6b9217331da7945fcc56fb59c2fcc82aaf39f34601aaeecc888fca32922",
    "package.json": "ded5308e848c04e09a8c70d02ea8b83e68dfb9381a2c13602137f11e69b946e2",
}


def download(relative: str, target: Path) -> None:
    require(not relative.startswith("/") and ".." not in relative.split("/"),
            "Invalid official source path")
    with urllib.request.urlopen(UPSTREAM + relative, timeout=60) as response:
        content = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def validate_task(row: dict) -> dict:
    require(type(row) is dict, "Invalid corpus task")
    repository = row.get("repo")
    require(type(repository) is str and re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None,
        "Expected pinned public GitHub repository")
    number = row.get("pr_number")
    require(type(number) is int and number > 0, "Invalid PR number")
    oid(row["base"])
    oid(row["head"])
    nwo = repository.removeprefix("https://github.com/")
    require(row.get("nwo", nwo) == nwo, "Corpus repository identity mismatch")
    return {**row, "id": task_identity(row),
            "mirror": "https://github.com/review-bench/" + nwo.replace("/", "_") + ".git"}


def freeze(root: Path, grader: Path) -> dict:
    root, grader = root.resolve(), grader.resolve()
    require(root != grader and root not in grader.parents and grader not in root.parents,
            "Grader storage must be separate from corpus/reviewer artifacts")
    require(not (root / "freeze.json").exists(), "Dataset freeze already exists; verify instead of overwriting")
    for relative in OFFICIAL_FILES:
        download(relative, root / "upstream" / relative)
        require(file_digest(root / "upstream" / relative) == PINNED_DIGESTS[relative],
                "Pinned upstream download digest mismatch")
    full = [validate_task(row) for row in read_json(root / "upstream" / "corpus" / "manifest.json")]
    starter = [validate_task(row) for row in read_json(root / "upstream" / "corpus" / "test" / "test.json")]
    require(len(full) == 219 and len({row["repo"] for row in full}) == 187, "Pinned full corpus changed")
    require(len(starter) == 25 and len({row["id"] for row in starter}) == 25, "Pinned starter membership changed")
    full_ids = {row["id"] for row in full}
    require(all(row["id"] in full_ids for row in starter), "Starter outside full corpus")
    labels = []
    # Public golden labels are intentionally never placed in root/upstream or a mirror.
    for row in full:
        nwo = row["repo"].removeprefix("https://github.com/").replace("/", "_")
        name = f"{nwo}_{row['pr_number']}-{row['head'][:8]}.json"
        target = grader / "golden" / name
        download("golden/" + name, target)
        labels.append({"task": row["id"], "relative_path": "golden/" + name,
                       "sha256": file_digest(target)})
    manifest = {"version": 1, "pin": PIN, "full_count": 219, "starter_count": 25,
                "sources": {relative: file_digest(root / "upstream" / relative)
                            for relative in OFFICIAL_FILES},
                "tasks": full, "starter_ids": [row["id"] for row in starter],
                "labels_digest": digest(labels)}
    manifest["digest"] = digest(manifest)
    write_json(root / "freeze.json", manifest)
    write_json(grader / "labels.json", {"pin": PIN, "labels": labels, "digest": digest(labels)})
    return manifest


def verify(root: Path) -> dict:
    manifest = read_json(root / "freeze.json")
    body = {key: value for key, value in manifest.items() if key != "digest"}
    require(manifest["digest"] == digest(body) and manifest["pin"] == PIN, "Dataset freeze tampered")
    require(manifest["sources"] == PINNED_DIGESTS, "Pinned official source identities changed")
    for relative, expected in manifest["sources"].items():
        require(file_digest(root / "upstream" / relative) == expected, "Pinned official source tampered")
    require(len(manifest["tasks"]) == 219 and len(manifest["starter_ids"]) == 25,
            "Dataset membership tampered")
    for row in manifest["tasks"]:
        require(task_identity(row) == row["id"], "Wrong frozen task identity")
        require(validate_task(row)["mirror"] == row["mirror"], "Wrong official mirror membership")
    expected = [validate_task(row) for row in read_json(root / "upstream" / "corpus" / "manifest.json")]
    starter = [validate_task(row) for row in read_json(root / "upstream" / "corpus" / "test" / "test.json")]
    require(expected == manifest["tasks"] and [row["id"] for row in starter] == manifest["starter_ids"],
            "Self-consistent task manifest differs from pinned official inputs")
    return manifest


def public_manifest(root: Path) -> dict:
    manifest = verify(root)
    return {"benchmark": "ReviewBench", "pin": PIN, "freeze_digest": manifest["digest"],
            "sources": manifest["sources"], "full_task_count": len(manifest["tasks"]),
            "starter_ids": manifest["starter_ids"],
            "tasks": [{**pr_identity(row), "id": row["id"], "mirror": row["mirror"]}
                      for row in manifest["tasks"]]}
