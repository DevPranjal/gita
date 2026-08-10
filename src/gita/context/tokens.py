"""Token accounting.

Uses tiktoken when available so budget numbers match what an agent actually
pays; falls back to a character estimate so the engine never hard-depends on it.
"""

from __future__ import annotations

from functools import lru_cache

_ENCODER = None
_METHOD = "approx(chars/4)"

try:  # pragma: no cover - depends on optional dependency
    import tiktoken

    _ENCODER = tiktoken.get_encoding("cl100k_base")
    _METHOD = "tiktoken/cl100k_base"
except Exception:  # pragma: no cover
    _ENCODER = None


def token_method() -> str:
    return _METHOD


@lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    if not text:
        return 0
    if _ENCODER is not None:
        return len(_ENCODER.encode(text, disallowed_special=()))
    return max(1, len(text) // 4)
