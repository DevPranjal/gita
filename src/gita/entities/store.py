"""A content-addressed store for parsed trees.

The same file content is parsed over and over: commit N's "before" is commit
N-1's "after", so walking twenty commits of one file parsed 4.2MB of source and
43% of it twice. git solves exactly this problem by naming objects after their
content, and the same idea applies here -- a parse depends only on the bytes and
the path, so it can be reused wherever those repeat.

Correctness is free: content that differs hashes differently, so a stale entry
is not possible. Only memory needs bounding.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Callable

from .model import EntityTree

#: Trees are small next to the source they describe, but a long history walk
#: would still grow without a bound.
DEFAULT_CAPACITY = 512


class TreeStore:
    """Least-recently-used cache of parsed trees, keyed by content and path.

    The path is part of the key because it selects the language: identical bytes
    in `a.py` and `a.txt` are not the same tree.
    """

    __slots__ = ("_entries", "_capacity", "hits", "misses")

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._entries: OrderedDict[tuple[str, str], EntityTree | None] = OrderedDict()
        self._capacity = max(1, capacity)
        self.hits = 0
        self.misses = 0

    def get(self, blob: bytes, path: str,
            parse: Callable[[bytes, str], EntityTree | None]) -> EntityTree | None:
        key = (hashlib.blake2b(blob, digest_size=16).hexdigest(), path)
        if key in self._entries:
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]

        self.misses += 1
        tree = parse(blob, path)
        self._entries[key] = tree
        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return tree

    def clear(self) -> None:
        self._entries.clear()
        self.hits = self.misses = 0

    def __len__(self) -> int:
        return len(self._entries)


#: Shared by every read path, so a tree parsed for a diff is not parsed again
#: for the hunks that follow it.
TREES = TreeStore()
