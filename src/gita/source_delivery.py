"""Exact, session-local receipts for source lines already delivered to a caller.

The caller must verify source identity and register only complete output actually
delivered. Rendering never registers delivery. Eviction forgets receipts, so an
evicted line is sent again rather than represented as previously delivered.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass


def _identifier(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{name} must be a nonempty identifier without surrounding whitespace or controls")


def _positive_integer(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class SourceKey:
    """Immutable exact identity; no normalization of commit, path, or blob."""

    commit: str
    path: str
    blob: str

    def __post_init__(self) -> None:
        for name in ("commit", "path", "blob"):
            _identifier(getattr(self, name), name)


@dataclass(frozen=True)
class SourceLine:
    number: int
    text: str


@dataclass(frozen=True)
class PreviouslyDeliveredRange:
    """Inclusive contiguous bounds sharing the same earlier receipt."""

    start: int
    end: int
    receipt_id: str


@dataclass(frozen=True)
class SourceDelivery:
    source: SourceKey
    start: int
    end: int
    new_lines: tuple[SourceLine, ...]
    previously_delivered_ranges: tuple[PreviouslyDeliveredRange, ...]


class SourceDeliveryLedger:
    """Bounded exact-line ledger, not a cache of inferred file contents.

    ``max_chars`` charges all stored strings per line (including identity and
    receipt), and ``max_lines`` bounds entry overhead even for empty lines.
    Oldest registrations are forgotten first; replay does not alter receipts.
    Instances are intended for a single caller/session, not concurrent access.
    """

    def __init__(self, *, max_chars: int = 100_000, max_lines: int = 10_000) -> None:
        _positive_integer(max_chars, "max_chars")
        _positive_integer(max_lines, "max_lines")
        self._max_chars = max_chars
        self._max_lines = max_lines
        self._chars = 0
        self._lines: OrderedDict[tuple[SourceKey, int], tuple[str, str]] = OrderedDict()

    @property
    def retained_chars(self) -> int:
        return self._chars

    @property
    def retained_lines(self) -> int:
        return len(self._lines)

    @staticmethod
    def _cost(source: SourceKey, text: str, receipt_id: str) -> int:
        return len(source.commit) + len(source.path) + len(source.blob) + len(text) + len(receipt_id)

    def _checked_lines(self, source: SourceKey, lines: Mapping[int, str]) -> dict[int, str]:
        if not isinstance(source, SourceKey):
            raise TypeError("source must be a SourceKey")
        if not isinstance(lines, Mapping):
            raise TypeError("lines must map explicit line numbers to exact strings")
        snapshot = dict(lines)
        for number, text in snapshot.items():
            _positive_integer(number, "line number")
            if not isinstance(text, str):
                raise TypeError("line text must be a string")
            previous = self._lines.get((source, number))
            if previous is not None and previous[0] != text:
                raise ValueError(f"inconsistent source content at line {number}")
        return snapshot

    def register_completed(
        self, source: SourceKey, lines: Mapping[int, str], *, receipt_id: str
    ) -> None:
        """Record only caller-verified, completed source lines.

        Validation is atomic: conflicting content cannot partially register a
        batch. Re-registering a retained line preserves its first receipt.
        Oversized entries are not retained. Forgotten entries cannot be checked
        for historical conflicts and are never claimed as known.
        """
        _identifier(receipt_id, "receipt_id")
        snapshot = self._checked_lines(source, lines)
        for number, text in snapshot.items():
            key = (source, number)
            if key in self._lines:
                continue
            cost = self._cost(source, text, receipt_id)
            if cost > self._max_chars:
                continue
            while self._lines and (
                self._chars + cost > self._max_chars or len(self._lines) >= self._max_lines
            ):
                (old_source, _), (old_text, old_receipt) = self._lines.popitem(last=False)
                self._chars -= self._cost(old_source, old_text, old_receipt)
            self._lines[key] = (text, receipt_id)
            self._chars += cost

    def render(
        self,
        source: SourceKey,
        lines: Mapping[int, str],
        *,
        start: int,
        end: int,
        force_full: bool = False,
        replay: bool = False,
    ) -> SourceDelivery:
        """Select inclusive bounds from supplied exact source, without recording.

        Every requested line must be provided, even if already retained. Either
        replay flag returns every requested string and no omission receipts.
        """
        _positive_integer(start, "start")
        _positive_integer(end, "end")
        if end < start:
            raise ValueError("end must not precede start")
        if not isinstance(force_full, bool) or not isinstance(replay, bool):
            raise TypeError("force_full and replay must be booleans")
        snapshot = self._checked_lines(source, lines)
        if end - start + 1 > len(snapshot):
            raise ValueError("every requested source line must be provided")
        new_lines: list[SourceLine] = []
        previous_ranges: list[PreviouslyDeliveredRange] = []
        for number in range(start, end + 1):
            if number not in snapshot:
                raise ValueError(f"requested source line {number} was not provided")
            previous = self._lines.get((source, number))
            if previous is None or force_full or replay:
                new_lines.append(SourceLine(number, snapshot[number]))
                continue
            receipt_id = previous[1]
            if (
                previous_ranges
                and previous_ranges[-1].end == number - 1
                and previous_ranges[-1].receipt_id == receipt_id
            ):
                last = previous_ranges[-1]
                previous_ranges[-1] = PreviouslyDeliveredRange(last.start, number, receipt_id)
            else:
                previous_ranges.append(PreviouslyDeliveredRange(number, number, receipt_id))
        return SourceDelivery(source, start, end, tuple(new_lines), tuple(previous_ranges))
