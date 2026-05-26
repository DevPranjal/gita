"""``gita context <symbol>`` — a single composite read for agents.

Pulls together what's already in v0.2 verbs (`get`, `callers`, `symbol-log`,
`last-proven`) into one record. Designed for MCP token economy: a `budget`
knob trims sections in a fixed order so the signature + body always survive,
and agents get a predictable shape regardless of how big the truth actually
is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import callers as callers_mod
from . import history as history_mod
from . import lookup as lookup_mod
from . import proofs as proofs_mod


# Drop order, oldest-first. Signature is never dropped.
_DROP_SECTIONS = ("log", "callers", "last_proven", "body")


@dataclass
class ContextRecord:
    symbol: lookup_mod.Symbol
    callers: list[dict[str, Any]]
    log: list[dict[str, Any]]
    last_proven: str | None
    rev: str
    dropped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        sym = {
            "name": self.symbol.name,
            "kind": self.symbol.kind,
            "path": self.symbol.path,
            "line_start": self.symbol.line_start,
            "line_end": self.symbol.line_end,
            "signature": self.symbol.signature,
            "body": self.symbol.body,
            "rev": self.symbol.rev,
            "requested_as": self.symbol.requested_as,
        }
        if self.symbol.parent:
            sym["parent"] = self.symbol.parent
        return {
            "symbol": sym,
            "callers": self.callers,
            "log": self.log,
            "last_proven": self.last_proven,
            "rev": self.rev,
            "dropped": list(self.dropped),
        }


def build(
    root: Path,
    name: str,
    *,
    rev: str = "HEAD",
    budget: int | None = None,
    log_limit: int = 10,
) -> ContextRecord:
    """Compose the four sections, then fit to ``budget`` if given."""
    symbol = lookup_mod.get(root, name, rev=rev)
    callers = callers_mod.find(root, name, ref=rev)
    log = history_mod.symbol_log(root, name, ref=rev, max_count=log_limit)
    try:
        last_proven = proofs_mod.last_proven(root, symbol=name)
    except proofs_mod.NoProofs:
        last_proven = None
    rec = ContextRecord(
        symbol=symbol,
        callers=list(callers),
        log=list(log),
        last_proven=last_proven,
        rev=symbol.rev,
    )
    if budget is not None:
        _fit(rec, budget)
    return rec


def _size(rec: ContextRecord) -> int:
    """Cheap proxy for serialized-size: length of the JSON dump."""
    return len(json.dumps(rec.to_dict(), ensure_ascii=False))


def _fit(rec: ContextRecord, budget: int) -> None:
    """Drop sections in fixed order until ``_size(rec) <= budget``.

    Signature is never dropped — pathological budgets just leave the record
    with only the signature and a populated ``dropped`` list.
    """
    if _size(rec) <= budget:
        return
    # 1. Pop log entries oldest-first (log is newest-first → pop from end).
    while rec.log and _size(rec) > budget:
        rec.log.pop()
    if not rec.log and "log" not in rec.dropped:
        rec.dropped.append("log")
    if _size(rec) <= budget:
        return
    # 2. Drop callers wholesale.
    if rec.callers:
        rec.callers = []
        rec.dropped.append("callers")
    if _size(rec) <= budget:
        return
    # 3. Drop last_proven.
    if rec.last_proven is not None:
        rec.last_proven = None
        rec.dropped.append("last_proven")
    if _size(rec) <= budget:
        return
    # 4. Drop body (last resort). Signature stays.
    if rec.symbol.body:
        # Symbol is frozen; rebuild with empty body.
        from dataclasses import replace
        rec.symbol = replace(rec.symbol, body="")
        rec.dropped.append("body")


__all__ = ["ContextRecord", "build"]
