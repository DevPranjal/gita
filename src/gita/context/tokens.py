"""Token accounting.

Uses tiktoken when available so budget numbers match what an agent actually
pays; falls back to a character estimate so the engine never hard-depends on it.
"""

from __future__ import annotations

import re
from functools import lru_cache

_UNSET = object()
_encoder: object = _UNSET


def _get_encoder():
    """Built on first use: loading it cost ~280ms of every invocation, including
    the ones that print usage or an error and never count a token."""
    global _encoder
    if _encoder is _UNSET:
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover - optional dependency
            _encoder = None
    return _encoder


def token_method() -> str:
    return "tiktoken/cl100k_base" if _get_encoder() is not None else "approx"


_PIECES = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Token count without tiktoken, erring high on purpose.

    A budget is a cap, so an estimate that guesses low breaks the contract rather
    than the answer. The old `chars // 4` undercounted 61% of corpus files, and
    `--budget 60` emitted 79 real tokens.

    The three terms cover each other's blind spots: word-and-punctuation counting
    undercounts long identifiers that BPE splits, character counting undercounts
    dense punctuation, and non-ASCII text costs far more tokens than characters.
    Conservative by ~1.5x typically; exact counting needs tiktoken.
    """
    if not text:
        return 0
    wide = sum(1 for ch in text if ord(ch) > 127)
    # The trailing +1 covers short strings, where fixed overhead dominates.
    return max(1, int(len(_PIECES.findall(text)) * 1.2) + 1, -(-len(text) * 2 // 5)) + wide + 1


@lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is not None:
        return len(encoder.encode(text, disallowed_special=()))
    return estimate_tokens(text)
