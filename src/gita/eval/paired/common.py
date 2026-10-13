from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL = "gpt-5.6-sol"
SEED = "gita-aacr-20260919-v1"
DATASET_SHA = "68a569759289a83654a59d06db2a72910edf0a4a"
_EVENT_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf8")
    os.replace(temporary, path)


def append_event(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _EVENT_LOCK, path.open("a", encoding="utf8") as stream:
        stream.write(json.dumps({"at": utc_now(), **value}, ensure_ascii=True) + "\n")
        stream.flush()


def git(repo: Path, *args: str, timeout: int = 180) -> str:
    result = subprocess.run(
        ["git", "--no-pager", "-c", "core.quotePath=false", "-C", str(repo), *args],
        capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"git {args[0]}: {result.stderr.decode('utf8', 'replace')[:2000]}")
    return result.stdout.decode("utf8", "replace")


def read_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8-sig").split("\n"):
        if line.strip():
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("CLI stream contains a non-object record")
            events.append(event)
    return events


def execution_events(directory: Path) -> tuple[list[dict], dict]:
    stream = directory / "events.jsonl"
    try:
        return read_events(stream), {"event_source": "events.jsonl"}
    except json.JSONDecodeError as error:
        terminal = json.loads([line for line in stream.read_text(encoding="utf-8-sig").split("\n")
                               if line.strip()][-1])
        journals = list((directory / "home" / "session-state").glob("*/events.jsonl"))
        if (len(journals) != 1 or terminal.get("type") != "result"
                or terminal.get("exitCode") != 0
                or journals[0].parent.name != terminal.get("sessionId")):
            raise ValueError("Corrupt stream has no uniquely verified successful session journal") from error
        journal = journals[0]
        return read_events(journal), {
            "event_source": str(journal.relative_to(directory)),
            "stream_parse_error": str(error),
            "cli_session_seconds": terminal["usage"]["sessionDurationMs"] / 1000,
        }


def parse_usage(path: Path, model: str = MODEL) -> dict:
    """Read one authoritative CLI aggregate, never sum nested duplicate totals."""
    raw = read_json(path)
    metrics = raw["modelMetrics"]
    if set(metrics) != {model} or raw["currentModel"] != model:
        raise ValueError(f"Unexpected model(s): {list(metrics)}")
    usage = metrics[model]["usage"]
    keys = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
    if any(type(usage[k]) is not int or usage[k] < 0 for k in keys):
        raise ValueError("Missing, negative or non-integral usage")
    total_input, output, cached, written = (usage[k] for k in keys)
    uncached = total_input - cached - written
    if total_input <= 0 or uncached < 0:
        raise ValueError("Impossible token accounting")
    details = raw["tokenDetails"]
    for key, expected in (("input", uncached), ("output", output),
                          ("cache_read", cached), ("cache_write", written)):
        if details[key]["tokenCount"] != expected:
            raise ValueError(f"Token detail mismatch: {key}")
    if set(raw.get("agentMetrics", {})) != {"main"}:
        raise ValueError("Unexpected nested agent usage")
    requests = metrics[model]["requests"]["count"]
    if type(requests) is not int or requests < 1:
        raise ValueError("No model requests measured")
    credit_estimate = (uncached * 400 + cached * 40 + written * 500 + output * 2000) / 1e6
    return {
        "input_tokens": total_input, "output_tokens": output,
        "total_tokens": total_input + output,
        "uncached_input_tokens": uncached, "cache_read_tokens": cached,
        "cache_write_tokens": written, "model_requests": requests,
        "reasoning_tokens": usage.get("reasoningTokens"),
        "estimated_credits": credit_estimate,
        "reported_credits": raw["totalNanoAiu"] / 1e9,
        "api_seconds": raw["totalApiDurationMs"] / 1000,
    }


def final_json(events: list[dict]) -> dict:
    messages = [e["data"]["content"] for e in events
                if e.get("type") == "assistant.message"
                and e.get("data", {}).get("phase") == "final_answer"
                and not e["data"].get("toolRequests")]
    if not messages:
        raise ValueError("No final assistant answer")
    text = messages[-1].strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Final answer must be a JSON object")
    return value


def validate_findings(answer: dict) -> list[dict]:
    findings = answer["findings"]
    if not isinstance(findings, list):
        raise ValueError("findings must be an array")
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("finding must be an object")
        for key in ("path", "title", "body", "category"):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                raise ValueError(f"Invalid finding {key}")
        if finding.get("side") not in ("left", "right"):
            raise ValueError("finding side must be left or right")
        start, end = finding.get("start_line"), finding.get("end_line")
        if type(start) is not int or type(end) is not int or not 0 < start <= end:
            raise ValueError("Invalid finding line range")
        if finding["category"] not in (
                "Code Defect", "Performance", "Maintainability and Readability", "Security"):
            raise ValueError("Invalid finding category")
    return findings


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
