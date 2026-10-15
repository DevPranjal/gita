from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

EVIDENCE_CALL_LIMIT = 40
FINALIZATION_GRACE = 5


class ProtocolError(RuntimeError):
    """A refusal that must not be converted to a successful empty review."""


class IntegrityError(ProtocolError):
    """A scientific-integrity violation that permanently stops this protocol."""

    code = "integrity"


class EvidenceBudgetError(ProtocolError):
    """Valid evidence cannot fit a model-facing response without truncation."""

    code = "evidence_budget"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def require_integrity(condition: bool, message: str) -> None:
    if not condition:
        raise IntegrityError(message)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".staging")
    staging.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf8")
    os.replace(staging, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf8") as stream:
        stream.write(json.dumps({"at": utc_now(), **value}, ensure_ascii=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def oid(value: str) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value) is not None,
            "An exact lowercase SHA-1 commit is required")
    return value


def checked_path(value: str) -> str:
    require(type(value) is str and bool(value) and "\\" not in value
            and ":" not in value and not any(c in value for c in "\x00\r\n")
            and not PurePosixPath(value).is_absolute()
            and all(p not in ("", ".", "..", ".git") for p in value.split("/")),
            "Expected a repository-relative POSIX path without traversal")
    return value


def git(repo: Path, *args: str, accepted: tuple[int, ...] = (0,),
        timeout: int = 120) -> bytes:
    # No inherited Git configuration, replacement objects, hooks, textconv or lazy fetch.
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith("GIT_"):
            del environment[name]
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                       GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    process = subprocess.run(
        ["git", "--no-pager", "-c", "core.hooksPath=" + os.devnull,
         "-c", "core.quotePath=false", "-c", "protocol.file.allow=never",
         "-C", str(repo), *args], env=environment, capture_output=True,
        timeout=timeout, check=False)
    require(process.returncode in accepted,
            "Git refused operation: " + process.stderr.decode("utf8", "replace")[:1000])
    return process.stdout


def pr_identity(task: dict) -> dict:
    return {name: task[name] for name in ("repo", "pr_number", "base", "head")}


def task_identity(task: dict) -> str:
    return digest(pr_identity(task))


def source_lines(source: bytes) -> list[str]:
    """Git/editor source coordinates count LF bytes, not Unicode separators."""
    lines = source.decode("utf8", "replace").split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def final_answer(events: list[dict]) -> dict:
    messages = [(i, e.get("data", {})) for i, e in enumerate(events)
                if e.get("type") == "assistant.message"]
    require(bool(messages), "Missing final answer")
    position, message = messages[-1]
    require(not message.get("toolRequests"), "Last assistant message still requests tools")
    phase = message.get("phase")
    require(phase in (None, "final_answer"), "Last assistant content is not a final answer")
    terminal = events[-1] if events else {}
    if phase is None or terminal.get("type") == "result":
        require(terminal.get("type") == "result" and terminal.get("exitCode") == 0,
                "Missing successful native terminal receipt")
    require(not any(e.get("type", "").startswith("tool.execution")
                    for e in events[position + 1:]), "Tool activity follows purported final answer")
    text = message.get("content")
    require(type(text) is str, "Missing final answer")
    # Deliberately no markdown stripping, JSON salvage or findings=[] fallback.
    try:
        answer = json.loads(text)
    except (ValueError, TypeError) as error:
        raise ProtocolError("Malformed final JSON") from error
    require(type(answer) is dict, "Final answer must be an object")
    return answer


def validate_findings(answer: dict, task: dict, evidence: Any) -> list[dict]:
    require(set(answer) == {"pr", "agent", "findings"}, "Wrong findings contract")
    require(answer["pr"] == pr_identity(task), "Wrong PR identity/revision")
    require(answer["agent"] == "reviewbench-reviewer", "Wrong agent identity")
    rows = answer["findings"]
    require(type(rows) is list and len(rows) <= 100, "Invalid findings array")
    for row in rows:
        require(type(row) is dict and set(row) ==
                {"file", "start_line", "end_line", "message", "producer"}, "Invalid finding fields")
        path = checked_path(row["file"])
        require(row["producer"] == answer["agent"], "Wrong producer")
        require(type(row["message"]) is str and bool(row["message"].strip()), "Empty issue message")
        start, end = row["start_line"], row["end_line"]
        require(type(start) is int and type(end) is int and 1 <= start <= end,
                "Invalid finding line range")
        require(path in evidence.paths(task["head"]), "Nonmember head finding path")
        require(end <= len(source_lines(evidence.blob(path, task["head"]))),
                "Finding lines outside actual head source")
    return rows
