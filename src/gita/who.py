"""``gita who`` — author + optional agent identity for a commit.

Combines git's intrinsic author/committer metadata (always present) with the
optional commit note written by ``gita commit-note`` (may be absent). When the
note is absent, ``agent`` is ``None`` and serialization omits the key entirely
— silent absence rather than a stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import git as gx
from . import notes


@dataclass(frozen=True)
class WhoRecord:
    commit: str
    author_name: str
    author_email: str
    timestamp: int  # unix seconds (UTC)
    message: str
    agent: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "commit": self.commit,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "timestamp": self.timestamp,
            "message": self.message,
        }
        if self.agent is not None:
            d["agent"] = self.agent
        return d


def describe(root: Path, rev: str = "HEAD") -> WhoRecord:
    sha = gx.rev_parse(root, rev)
    meta = gx.commit_meta(root, sha)
    note = notes.read(root, sha)
    return WhoRecord(
        commit=sha,
        author_name=meta.author_name,
        author_email=meta.author_email,
        timestamp=meta.timestamp,
        message=meta.message,
        agent=note,
    )


__all__ = ["WhoRecord", "describe"]
