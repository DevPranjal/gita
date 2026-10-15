"""Copilot-native auto accounting and fail-closed, persistent spend reservations."""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Iterator

from .common import (EVIDENCE_CALL_LIMIT, FINALIZATION_GRACE, IntegrityError, ProtocolError, digest, read_json, require,
                     require_integrity, utc_now, write_json)

SESSION_CREDITS = 30  # CLI 1.0.97 rejects lower native caps.
REVIEWER_CREDITS = 400
JUDGE_CREDITS = 200
MAX_SESSIONS = 2
MAX_SECONDS = 900
CAMPAIGN_SECONDS = 4 * 3600
MAX_TURNS = 40


def parse_usage(raw: dict) -> dict:
    try:
        metrics = raw["modelMetrics"]
        require(type(metrics) is dict and bool(metrics), "Missing realized model metrics")
        require(set(raw["agentMetrics"]) == {"main"}, "Unexpected nested agent usage")
        require(raw["currentModel"] in metrics, "Current model missing from realized routes")
        credits = raw["totalNanoAiu"]
        require(type(credits) in (int, float) and math.isfinite(credits) and credits >= 0,
                "Missing/invalid authoritative native credit total")
        totals = dict(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                      cache_write_tokens=0, model_requests=0)
        per_model = {}
        for model, value in metrics.items():
            require(type(model) is str and bool(model), "Invalid realized model")
            usage = value["usage"]
            row = {}
            for native, local in (("inputTokens", "input_tokens"),
                                  ("outputTokens", "output_tokens"),
                                  ("cacheReadTokens", "cache_read_tokens"),
                                  ("cacheWriteTokens", "cache_write_tokens")):
                number = usage[native]
                require(type(number) is int and number >= 0, "Missing/invalid token usage")
                row[local] = number
                totals[local] += number
            require(row["input_tokens"] >= row["cache_read_tokens"] + row["cache_write_tokens"],
                    "Impossible token partitions")
            requests = value["requests"]["count"]
            require(type(requests) is int and requests >= 1, "Missing requests")
            totals["model_requests"] += requests
            reasoning = usage.get("reasoningTokens")
            require(reasoning is None or (type(reasoning) is int and reasoning >= 0),
                    "Invalid reasoning telemetry")
            row.update(model_requests=requests, reasoning_tokens=reasoning,
                       native_model_nano_aiu=value.get("totalNanoAiu"))
            per_model[model] = row
        uncached = totals["input_tokens"] - totals["cache_read_tokens"] - totals["cache_write_tokens"]
        for key, expected in (("input", uncached), ("output", totals["output_tokens"]),
                              ("cache_read", totals["cache_read_tokens"]),
                              ("cache_write", totals["cache_write_tokens"])):
            require(raw["tokenDetails"][key]["tokenCount"] == expected,
                    "Aggregate/native token partitions disagree")
        require(totals["input_tokens"] > 0, "No measured model input")
        duration = raw["totalApiDurationMs"]
        require(type(duration) in (int, float) and math.isfinite(duration) and duration >= 0,
                "Invalid API duration")
        return {**totals, "uncached_input_tokens": uncached,
                "credits": credits / 1e9, "total_nano_aiu": credits,
                "api_seconds": duration / 1000, "models": sorted(metrics),
                "current_model": raw["currentModel"], "per_model": per_model,
                "reasoning_tokens": (sum(r["reasoning_tokens"] for r in per_model.values())
                                     if all(r["reasoning_tokens"] is not None
                                            for r in per_model.values()) else None)}
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ProtocolError("Missing/invalid native usage telemetry") from error


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ProtocolError("Another campaign/ledger writer owns this lock") from error
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ProtocolError("Another campaign/ledger writer owns this lock") from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class CreditLedger:
    def __init__(self, path: Path, protocol: str, ceiling: int):
        require(0 < ceiling <= REVIEWER_CREDITS, "Invalid ledger ceiling")
        self.path, self.protocol, self.ceiling = path, protocol, ceiling
        with file_lock(path.with_suffix(".lock")):
            if path.exists():
                self._load()
            else:
                write_json(path, {"protocol": protocol, "ceiling": ceiling,
                                  "created": utc_now(), "spent": 0, "slots": {},
                                  "terminal_stop": None})

    def _load(self) -> dict:
        state = read_json(self.path)
        require(state["protocol"] == self.protocol and state["ceiling"] == self.ceiling,
                "Ledger protocol/ceiling mismatch")
        return state

    def snapshot(self) -> dict:
        with file_lock(self.path.with_suffix(".lock")):
            return self._load()

    def reserve(self, slot: str, *, receipt_path: Path | None = None) -> None:
        with file_lock(self.path.with_suffix(".lock")):
            state = self._load()
            require_integrity(state.get("terminal_stop") is None,
                              "Protocol has a permanent terminal integrity stop")
            require(slot not in state["slots"], "Started slot cannot be retried automatically")
            require(not any(s["state"] == "unknown" for s in state["slots"].values()),
                    "Campaign stopped: unknown usage")
            active = sum(s["state"] == "reserved" for s in state["slots"].values())
            require(active < MAX_SESSIONS, "At most two model sessions")
            require(state["spent"] + (active + 1) * SESSION_CREDITS <= self.ceiling,
                    "Campaign budget cannot reserve another 30-credit session")
            state["slots"][slot] = {"state": "reserved", "cap": SESSION_CREDITS,
                                     "started": utc_now(), "credits": None}
            if receipt_path is not None:
                state["slots"][slot]["receipt_path"] = str(receipt_path.resolve())
            write_json(self.path, state)

    def settle(self, slot: str, credits: float | None) -> None:
        with file_lock(self.path.with_suffix(".lock")):
            state = self._load()
            row = state["slots"][slot]
            require(row["state"] == "reserved", "Slot already settled")
            valid = (type(credits) in (int, float) and math.isfinite(credits)
                     and 0 <= credits <= SESSION_CREDITS)
            row.update(state="settled" if valid else "unknown",
                       credits=credits, ended=utc_now())
            if valid:
                state["spent"] += credits
            write_json(self.path, state)
            require(credits is None or valid, "Native spend exceeded reservation; campaign stopped")

    def halt_integrity(self, slot: str, reason: str) -> dict:
        with file_lock(self.path.with_suffix(".lock")):
            state = self._load()
            if state.get("terminal_stop") is None:
                state["terminal_stop"] = {"protocol": self.protocol, "kind": "integrity",
                                          "slot": slot, "reason": reason, "at": utc_now()}
                write_json(self.path, state)
            return state["terminal_stop"]


def reconcile_judge_outcomes(run_root: Path, protocol: str) -> dict | None:
    path = run_root / "judge-ledger.json"
    if not path.exists():
        return None
    ceiling = read_json(path)["ceiling"]
    require_integrity(type(ceiling) is int and 0 < ceiling <= JUDGE_CREDITS,
                      "Judge ledger exceeds its authorized maximum")
    ledger = CreditLedger(path, protocol, ceiling)
    state = ledger.snapshot()
    if state.get("terminal_stop") is not None:
        return state["terminal_stop"]
    for key, slot in state["slots"].items():
        if slot["state"] != "settled":
            return {"kind": "unresolved", "slot": key, "reason": "Unresolved prior judge outcome blocks model reservations"}
        receipt_path = slot.get("receipt_path")
        try:
            require(type(receipt_path) is str and bool(receipt_path), "Judge slot lacks its audit receipt path")
            outcome = read_json(Path(receipt_path))
            require(type(outcome) is dict, "Malformed prior judge outcome")
        except (ProtocolError, OSError, ValueError) as error:
            return {"kind": "unresolved", "slot": key, "reason": str(error)}
        if (outcome.get("integrity_stop") is True or outcome.get("status") == "integrity_failed"
                or outcome.get("error_code") == IntegrityError.code):
            try:
                return ledger.halt_integrity(key, "Reconciled judge integrity failure")
            except (OSError, ProtocolError) as error:
                raise IntegrityError("Cannot persist reconciled judge integrity stop; abort") from error
        require_integrity(outcome.get("usage") is not None
                          and outcome["usage"].get("credits") == slot["credits"],
                          "Prior judge outcome disagrees with settled spend")
    return None


class ProcessTree:
    """Own the CLI and all descendants, including surviving MCP children."""
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.handle = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                            ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.CreateJobObjectW(None, None)
            limits = Extended()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE.
            configured = bool(self.handle and self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            attached = configured and self.kernel.AssignProcessToJobObject(
                self.handle, wintypes.HANDLE(process._handle))
            if not attached:
                if self.handle:
                    self.kernel.CloseHandle(self.handle)
                    self.handle = None
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, check=False)
                process.wait(timeout=30)
                raise ProtocolError("Cannot establish owned Windows job; refusing model session")

    def close(self) -> None:
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process.poll() is None:
            self.process.wait(timeout=30)


def read_events(path: Path, *, partial: bool = False) -> list[dict]:
    import json
    events = []
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as error:
            if partial and i == len(lines) - 1:
                break
            raise ProtocolError("Malformed runtime JSON stream") from error
        require(type(event) is dict, "Nonobject runtime event")
        events.append(event)
    return events


def inventories(events: list[dict], allowed: set[str], *, final: bool = True) -> list[list[str]]:
    observed = []
    for event in events:
        if event.get("type") != "session.usage_checkpoint":
            continue
        states = event.get("data", {}).get("promptCacheBreakState", [])
        for state in states:
            for value in state.get("models", {}).values():
                require_integrity(not value.get("tools_truncated", 0), "Truncated exposed tool inventory")
                names = [tool["name"] for tool in value.get("tools", [])]
                require_integrity(set(names) == allowed and len(names) == len(allowed),
                                  "Forbidden/missing exposed tools")
                if "tool_count" in value:
                    require_integrity(value["tool_count"] == len(names), "Inventory count mismatch")
                observed.append(names)
    if final:
        require(bool(observed), "Missing tool-schema inventory audit")
    for event in events:
        if event.get("type") == "tool.execution_start":
            require_integrity(event.get("data", {}).get("toolName") in allowed, "Forbidden tool invocation")
        if event.get("type") == "session.mcp_servers_loaded":
            for server in event.get("data", {}).get("servers", []):
                require_integrity(server.get("status") != "loaded" or server.get("name") == "review",
                                  "Unexpected MCP server")
    return observed


def environment(home: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("COPILOT_", "GITA_", "GIT_")) and key not in (
                "COPILOT_GITHUB_TOKEN", "COPILOT_GH_HOST"):
            del env[key]
    env.update(COPILOT_HOME=str(home), COPILOT_AUTO_UPDATE="false", NO_COLOR="1",
               PYTHONIOENCODING="utf-8", USE_TGREP="false",
               PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    return env


def command(directory: Path, prompt: str, allowed: list[str], mcp: Path | None) -> list[str]:
    executable = shutil.which("copilot.exe") or shutil.which("copilot")
    require(executable is not None, "Copilot CLI missing")
    args = [executable, "-C", str(directory / "workspace"), "--model", "auto",
            "--context", "default",
            "--max-ai-credits", str(SESSION_CREDITS), "--no-auto-update",
            "--no-custom-instructions", "--disable-builtin-mcps", "--no-ask-user",
            "--no-color", "--output-format", "json", "--stream", "on",
            "--usage-output-file", str(directory / "usage.json"),
            "--log-dir", str(directory / "logs"), "--log-level", "debug",
            "--dynamic-retrieval", "skills=off", "--max-autopilot-continues", "0",
            "--allow-all-tools"]
    # Bare --available-tools is NOT an empty allowlist in CLI 1.0.97.
    args += ["--available-tools", ",".join(allowed) if allowed else "review-no-such-tool"]
    if mcp is not None:
        args += ["--additional-mcp-config", "@" + str(mcp)]
    return args


def invoke(directory: Path, prompt: str, allowed: list[str], mcp: Path | None,
           *, timeout: float = MAX_SECONDS, streaming: bool = True) -> dict:
    require(0 < timeout <= MAX_SECONDS, "Invalid reviewer timeout")
    for name in ("workspace", "home", "logs"):
        (directory / name).mkdir(parents=True, exist_ok=False)
    args = command(directory, prompt, allowed, mcp)
    require(type(streaming) is bool, "Streaming selection must be boolean")
    if not streaming:
        args += ["--stream", "off"]
    write_json(directory / "invocation.json", {
        "arguments_without_prompt": args, "prompt_digest": digest(prompt),
        "prompt_transport": "utf8-stdin", "requested_model": "auto",
        "native_credit_cap": SESSION_CREDITS})
    (directory / "prompt.txt").write_text(prompt, encoding="utf8", newline="")
    start = time.monotonic()
    outcome = {"status": "runtime_failed", "usage": None, "error": None}
    with (directory / "events.jsonl").open("w", encoding="utf8") as output, (
            directory / "stderr.txt").open("w", encoding="utf8") as errors, (
            directory / "prompt.txt").open("rb") as incoming:
        process = None
        tree = None
        try:
            process = subprocess.Popen(args, stdout=output, stderr=errors, stdin=incoming,
                                       env=environment(directory / "home"),
                                       start_new_session=os.name != "nt")
            tree = ProcessTree(process)
            write_json(directory / "process.json", {"pid": process.pid, "started": utc_now()})
            while process.poll() is None:
                require(time.monotonic() - start < timeout, "Reviewer wall-time limit reached")
                events = read_events(directory / "events.jsonl", partial=True)
                inventories(events, set(allowed), final=False)
                turns = sum(e.get("type") == "assistant.turn_start" for e in events)
                require(turns <= MAX_TURNS + FINALIZATION_GRACE, "Reviewer finalization turn limit reached")
                calls = sum(e.get("type") == "tool.execution_start" for e in events)
                require(calls <= EVIDENCE_CALL_LIMIT + FINALIZATION_GRACE, "Evidence finalization grace exceeded")
                time.sleep(0.25)
            require(process.returncode == 0, "CLI exited unsuccessfully")
            events = read_events(directory / "events.jsonl")
            inventories(events, set(allowed))
            outcome.update(status="runtime_complete", events=events)
        except IntegrityError as error:
            outcome.update(status="integrity_failed", integrity_stop=True,
                           error_code=error.code, error=str(error))
        except (ProtocolError, OSError, subprocess.SubprocessError) as error:
            outcome["error"] = str(error)
        finally:
            if tree is not None:
                tree.close()
            elif process is not None and process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=30)
    try:
        outcome["usage"] = parse_usage(read_json(directory / "usage.json"))
    except (OSError, ValueError, ProtocolError) as error:
        outcome["usage_error"] = str(error)
        if not outcome.get("integrity_stop"):
            outcome["status"] = "measurement_invalid"
    outcome["wall_seconds"] = time.monotonic() - start
    return outcome
