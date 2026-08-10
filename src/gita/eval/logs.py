"""Real token usage, read from the Copilot CLI's own logs.

Each run gets its own --log-dir, so usage is unambiguously attributable without
correlating on timestamps.

Regex rather than JSON parsing: the logs interleave prose and pretty-printed
JSON, and a format change should degrade to zero rather than crash the harness.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPT = re.compile(r'"prompt_tokens"\s*:\s*(\d+)')
_COMPLETION = re.compile(r'"completion_tokens"\s*:\s*(\d+)')
_CACHED = re.compile(r'"cached_tokens"\s*:\s*(\d+)')
_CACHE_WRITE = re.compile(r'"cache_creation_tokens"\s*:\s*(\d+)')


def parse_usage(log_dir: str | Path) -> dict[str, int]:
    directory = Path(log_dir)
    empty = {"turns": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "cached_tokens": 0, "cache_creation_tokens": 0,
             "peak_prompt_tokens": 0}
    if not directory.exists():
        return empty

    prompts: list[int] = []
    completions: list[int] = []
    cached: list[int] = []
    written: list[int] = []

    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue
        prompts += [int(n) for n in _PROMPT.findall(text)]
        completions += [int(n) for n in _COMPLETION.findall(text)]
        cached += [int(n) for n in _CACHED.findall(text)]
        written += [int(n) for n in _CACHE_WRITE.findall(text)]

    return {
        "turns": len(prompts),
        "prompt_tokens": sum(prompts),
        "completion_tokens": sum(completions),
        "cached_tokens": sum(cached),
        "cache_creation_tokens": sum(written),
        "peak_prompt_tokens": max(prompts, default=0),
    }
