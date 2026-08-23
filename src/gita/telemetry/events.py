"""Append-only telemetry events.

Telemetry must never break the thing it measures: every failure here is
swallowed. An eval harness that crashes the tool under test produces no data
and a lot of confusion.

What we record is **tokens of tool output injected into context** -- not total
model spend, which is not recoverable locally. That distinction belongs on the
dashboard too, not just in this docstring.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ENV_SINK = "GITA_TELEMETRY"
ENV_SESSION = "GITA_SESSION"
ENV_TASK = "GITA_TASK"
ENV_ARM = "GITA_ARM"
ENV_VIA = "GITA_VIA"

#: Which layer each tool exposes, for the drill-depth metric.
TOOL_LAYER = {
    "gita_diff": "L1",
    "diff": "L1",
    "gita_ask": "L1",
    "ask": "L1",
    "gita_expand": "L1",
    "expand": "L1",
    "gita_show": "L2",
    "show": "L2",
}

_PROCESS_SESSION = f"proc-{uuid.uuid4().hex[:12]}"


def sink_path(path: str | Path | None = None) -> Path | None:
    target = path or os.environ.get(ENV_SINK)
    return Path(target) if target else None


def _cwd() -> str:
    try:
        return os.getcwd()
    except OSError:  # pragma: no cover - a deleted working directory
        return ""


def session_id() -> str:
    return os.environ.get(ENV_SESSION) or _PROCESS_SESSION


def caller() -> str:
    """Who is driving this call.

    Usage is only interesting once you can separate what an agent did from what
    you did by hand. A launcher that knows the answer sets ``GITA_VIA``;
    otherwise it is inferred from the markers the host leaves in the environment.
    """
    explicit = os.environ.get(ENV_VIA)
    if explicit:
        return explicit
    if any(key.startswith(("COPILOT_", "GH_COPILOT", "GITHUB_COPILOT"))
           for key in os.environ):
        return "copilot-cli"
    if (os.environ.get("TERM_PROGRAM") == "vscode"
            or "VSCODE_GIT_IPC_HANDLE" in os.environ
            or "VSCODE_INJECTION" in os.environ):
        return "vscode"
    return "shell"


def record(event: dict, path: str | Path | None = None) -> bool:
    """Append one event. Returns False when telemetry is off or unwritable."""
    target = sink_path(path)
    if target is None:
        return False

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "session": session_id(),
        **event,
    }
    if ENV_TASK in os.environ:
        payload.setdefault("task", os.environ[ENV_TASK])
    if ENV_ARM in os.environ:
        payload.setdefault("arm", os.environ[ENV_ARM])
    payload.setdefault("via", caller())
    payload.setdefault("cwd", _cwd())
    payload.setdefault("layer", TOOL_LAYER.get(str(payload.get("tool", ""))))

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf8") as handle:
            handle.write(json.dumps(payload) + "\n")
        return True
    except OSError:
        return False


def load_events(path: str | Path | None = None) -> list[dict]:
    target = sink_path(path)
    if target is None or not target.exists():
        return []

    events: list[dict] = []
    try:
        text = target.read_text(encoding="utf8", errors="replace")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue  # a partially written line must not lose the rest
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


class timed:
    """Context manager yielding elapsed milliseconds."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> bool:
        self.ms = int((time.perf_counter() - self._start) * 1000)
        return False
