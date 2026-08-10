"""Real money, not token counts.

Token totals are a bad proxy for cost because the four token classes differ in
price by 50x. Two earlier readings of the same run disagreed with each other and
with reality:

    raw prompt tokens            gita -15%   (ignores that most are cached)
    prompt minus cached          gita +3.8%  (treats cache reads as free, and
                                              ignores output entirely)
    actual credits               gita -8.3%  <- the one that is true

Cache reads are 54% of the baseline arm's bill despite costing a tenth of the
list rate, purely because there are so many of them. Output is only ~1,000 tokens
a run but is billed at 5x. Neither survives a token-count summary.
"""

from __future__ import annotations

from dataclasses import dataclass

#: GitHub Copilot credits per 1M tokens, Claude Opus 5.
PRICING = {
    "input": 500,
    "output": 2500,
    "cache_read": 50,
    "cache_write": 625,
}


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def fresh_tokens(self) -> int:
        """Prompt tokens that were neither read from nor written to cache."""
        return max(0, self.prompt_tokens - self.cached_tokens
                   - self.cache_creation_tokens)


def credits(usage: Usage | dict, pricing: dict[str, int] | None = None) -> float:
    """Cost in credits for one request or one session."""
    price = pricing or PRICING
    if isinstance(usage, dict):
        usage = Usage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cached_tokens=int(usage.get("cached_tokens", 0)),
            cache_creation_tokens=int(usage.get("cache_creation_tokens", 0)),
        )

    return (
        usage.fresh_tokens * price["input"]
        + usage.cached_tokens * price["cache_read"]
        + usage.cache_creation_tokens * price["cache_write"]
        + usage.completion_tokens * price["output"]
    ) / 1_000_000


def breakdown(usage: Usage | dict, pricing: dict[str, int] | None = None) -> dict:
    """Where the money actually went, so a summary cannot hide a dominant term."""
    price = pricing or PRICING
    if isinstance(usage, dict):
        usage = Usage(
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cached_tokens=int(usage.get("cached_tokens", 0)),
            cache_creation_tokens=int(usage.get("cache_creation_tokens", 0)),
        )

    parts = {
        "fresh": usage.fresh_tokens * price["input"] / 1_000_000,
        "cache_read": usage.cached_tokens * price["cache_read"] / 1_000_000,
        "cache_write": usage.cache_creation_tokens * price["cache_write"] / 1_000_000,
        "output": usage.completion_tokens * price["output"] / 1_000_000,
    }
    total = sum(parts.values())
    return {
        "credits": total,
        "parts": parts,
        "share": {k: (v / total if total else 0.0) for k, v in parts.items()},
    }
