"""Deterministic offline Python history, with an authored (not matcher) ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def git(root: Path, *args: str, environment: dict | None = None) -> bytes:
    environment = dict(os.environ if environment is None else environment)
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
         "-c", "commit.gpgsign=false", "-C", str(root), *args],
        env=environment, check=True, capture_output=True, timeout=60,
    ).stdout


def source(file: int, functions: int, *, salt: int = 0) -> str:
    # Distinct meaningful bodies, rather than duplicate stubs that imply ambiguity.
    return "\n\n".join(
        f"def function_{file}_{number}(value):\n"
        f"    first = value + {salt + file * 1000 + number * 10 + 1}\n"
        f"    second = first * {salt + file * 1000 + number * 10 + 2}\n"
        f"    third = second - {salt + file * 1000 + number * 10 + 3}\n"
        f"    return (third + first) // (second + {salt + file * 1000 + number * 10 + 4})"
        for number in range(functions)
    ) + "\n"


def validate_corpus(config: dict) -> None:
    if type(config) is not dict or set(config) != {"files", "functions", "commits", "profile"}:
        raise ValueError("corpus requires files/functions/commits/profile")
    for key, maximum in (("files", 10000), ("functions", 20), ("commits", 1000)):
        if type(config[key]) is not int or not 1 <= config[key] <= maximum:
            raise ValueError(f"corpus.{key} must be an integer in [1, {maximum}]")
    if config["commits"] < 2 or config["profile"] not in ("uniform", "hotspot"):
        raise ValueError("at least two commits and a uniform/hotspot profile required")


def generate(root: Path, *, files: int, functions: int, commits: int,
             profile: str = "uniform") -> dict:
    config = dict(files=files, functions=functions, commits=commits, profile=profile)
    validate_corpus(config)
    root.mkdir(parents=True, exist_ok=False)
    git(root, "init", "--quiet", "--template=", "--object-format=sha1")
    git(root, "config", "user.name", "Index fixture")
    git(root, "config", "user.email", "index-fixture@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "gc.auto", "0")
    contents = {f"file_{file:05}.py": source(file, functions) for file in range(files)}
    identities = {
        path: {"<module>": f"module:{file}:0", **{
            f"function_{file}_{number}": f"function:{file}:{number}:0"
            for number in range(functions)
        }}
        for file, path in enumerate(contents)
    }
    paths = list(contents)
    ledger, revisions = [], []
    operations = ("body", "signature", "rename", "move", "copy", "delete",
                  "reintroduce", "cosmetic", "revert")
    for step in range(commits):
        writes, removes, mapping = {}, [], {}
        operation = "root" if not step else operations[(step - 1) % len(operations)]
        file = 0 if profile == "hotspot" else ((step - 1) // len(operations)) % files
        path = paths[file]
        if step == 0:
            writes, mapping = dict(contents), dict(identities)
        elif operation == "body":
            writes[path] = contents[path].replace("first = value +", "first = value *", 1)
        elif operation == "signature":
            writes[path] = contents[path].replace("(value):", "(value, extra=7):", 1)
        elif operation == "rename":
            old = next(name for name in identities[path] if name != "<module>")
            new = f"renamed_{file}_{step}"
            writes[path] = contents[path].replace(f"def {old}(", f"def {new}(", 1)
            updated = dict(identities[path])
            updated[new] = updated.pop(old)
            mapping[path] = updated
        elif operation == "move":
            new = f"moved_{file:05}_{step}.py"
            writes[new], mapping[new] = contents[path], dict(identities[path])
            removes.append(path)
            paths[file] = new
        elif operation == "copy":
            new = f"copy_{file:05}_{step}.py"
            writes[new] = contents[path]
            mapping[new] = {name: f"copy:{step}:{logical}" for name, logical in identities[path].items()}
        elif operation == "delete":
            removes.append(path)
        elif operation == "reintroduce":
            # Reintroduction has distinctive bodies; otherwise a retained copy
            # legitimately makes later continuity ambiguous, not an exact oracle.
            writes[path] = source(file, functions, salt=step * 1000000)
            mapping[path] = {"<module>": f"module:{file}:{step}", **{
                f"function_{file}_{number}": f"function:{file}:{number}:{step}"
                for number in range(functions)
            }}
        elif operation == "cosmetic":
            writes[path] = "# Cosmetic fixture comment.\n" + contents[path]
        elif operation == "revert":
            writes[path] = contents[path].replace("# Cosmetic fixture comment.\n", "")
        for removed in removes:
            (root / removed).unlink()
            contents.pop(removed)
            identities.pop(removed)
        for written, text in writes.items():
            (root / written).write_bytes(text.encode("utf-8"))
            contents[written] = text
            if written not in mapping:
                mapping[written] = dict(identities[written])
            identities[written] = mapping[written]
        environment = os.environ.copy()
        timestamp = f"{946684800 + step} +0000"
        environment.update(
            GIT_AUTHOR_DATE=timestamp, GIT_COMMITTER_DATE=timestamp,
            GIT_AUTHOR_NAME="Index fixture", GIT_AUTHOR_EMAIL="index-fixture@example.invalid",
            GIT_COMMITTER_NAME="Index fixture", GIT_COMMITTER_EMAIL="index-fixture@example.invalid",
        )
        git(root, "add", "--all")
        git(root, "commit", "--quiet", "--allow-empty", "-m", f"{step:04} {operation}", environment=environment)
        revisions.append(git(root, "rev-parse", "HEAD").decode("ascii").strip())
        transitions = {}
        if operation in ("body", "signature", "rename", "move"):
            written = next(iter(writes))
            name = next(line[4:].split("(", 1)[0] for line in writes[written].splitlines()
                        if line.startswith("def "))
            transitions[mapping[written][name]] = {
                "body": "body_changed", "signature": "signature_changed",
                "rename": "renamed", "move": "moved",
            }[operation]
        ledger.append(dict(operation=operation, writes=writes, removes=removes,
                           identities=mapping, transitions=transitions))
    truth = dict(schema_version=1, generator_version=1, config=config, commits=revisions, ledger=ledger)
    (root.parent / (root.name + "-ledger.json")).write_text(
        json.dumps(truth, sort_keys=True, indent=2), encoding="utf-8",
    )
    return truth
