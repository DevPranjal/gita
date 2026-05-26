"""``gita bisect-proven`` — narrow a regression to a single commit.

Given a check name (e.g. ``pytest``) with a recorded proof somewhere in
history, walk the commits between that baseline and HEAD newest-first to
find the first commit where the check no longer passes. Optionally fill
gaps by running a command (we checkout each commit, run the cmd, record
the proof, then restore).

Three deliberate non-features:

* Not parallel — one cmd at a time. Saves machinery; agents can shard.
* Not heuristic on rename — we re-resolve the symbol per commit only when
  the caller passes ``--symbol``. The walk itself is sha-based.
* Not interactive — no ``good``/``bad`` REPL. The verb either has cached
  proofs to walk or a cmd to fill gaps, otherwise it returns ``reason="gaps"``
  and lists what's missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import callers as callers_mod
from . import git as gx
from . import history as history_mod
from . import proofs as proofs_mod


class NoBaseline(LookupError):
    """No commit reachable from HEAD has a passing proof for the check."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@dataclass
class BisectResult:
    from_sha: str | None
    to_sha: str
    suspect: str | None
    reason: str  # head_is_proven | first_failure | gaps | head_passes
    ops: list[dict[str, Any]] = field(default_factory=list)
    callers_of_changed_symbols: list[dict[str, Any]] = field(default_factory=list)
    checks_used: list[str] = field(default_factory=list)
    via_merge: str | None = None
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_sha": self.from_sha,
            "to_sha": self.to_sha,
            "suspect": self.suspect,
            "reason": self.reason,
            "ops": list(self.ops),
            "callers_of_changed_symbols": list(self.callers_of_changed_symbols),
            "checks_used": list(self.checks_used),
            "via_merge": self.via_merge,
            "missing": list(self.missing),
        }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _check_status(proof: dict[str, Any] | None, name: str) -> bool | None:
    """Return True/False/None for a stored proof's named check."""
    if proof is None:
        return None
    entry = (proof.get("checks") or {}).get(name)
    if entry is None:
        return None
    return bool(entry.get("ok"))


def _op_mentions(op: dict[str, Any], symbol: str) -> bool:
    if op.get("op") == "rename_symbol":
        return op.get("from") == symbol or op.get("to") == symbol
    return op.get("name") == symbol


def _range_oldest_first(root: Path, from_sha: str, to_sha: str) -> list[str]:
    """Commits in ``(from_sha, to_sha]`` along first-parent, oldest first."""
    newest_first = gx.log_shas(root, to_sha)
    in_range: list[str] = []
    for sha in newest_first:
        if sha == from_sha:
            break
        in_range.append(sha)
    in_range.reverse()
    return in_range


def _classify(
    root: Path, sha: str, name: str, cmd: list[str] | None,
) -> bool | None:
    """Return True/False if known, else None. Runs ``cmd`` to fill gaps."""
    proof = proofs_mod.read(root, sha)
    status = _check_status(proof, name)
    if status is not None:
        return status
    if cmd is None:
        return None
    with gx.checkout_then_restore(root, sha):
        proofs_mod.record(root, name, cmd=list(cmd))
    proof = proofs_mod.read(root, sha)
    return _check_status(proof, name)


def _collect_ops_and_callers(
    root: Path, suspect: str, symbol: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = history_mod.manifest_for(root, suspect)
    ops: list[dict[str, Any]] = []
    for fe in manifest.get("files", []):
        for op in fe.get("ops", []):
            if symbol is not None and not _op_mentions(op, symbol):
                continue
            ops.append({"path": fe["path"], **op})
    callers_list: list[dict[str, Any]] = []
    seen: set[str] = set()
    for op in ops:
        for key in ("name", "to", "from"):
            nm = op.get(key)
            if not isinstance(nm, str) or nm in seen:
                continue
            seen.add(nm)
            try:
                hits = callers_mod.find(root, nm, ref=suspect)
            except Exception:
                continue
            for h in hits:
                callers_list.append({"symbol": nm, **h})
    return ops, callers_list


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def run(
    root: Path,
    name: str,
    *,
    cmd: list[str] | None = None,
    symbol: str | None = None,
    ref: str = "HEAD",
) -> BisectResult:
    """Narrow a regression in check ``name`` between last-proven and ``ref``."""
    try:
        from_sha = proofs_mod.last_proven(root, name=name)
    except proofs_mod.NoProofs as exc:
        raise NoBaseline(name) from exc

    to_sha = gx.rev_parse(root, ref)
    checks = [name]

    if from_sha == to_sha:
        return BisectResult(
            from_sha=from_sha, to_sha=to_sha, suspect=None,
            reason="head_is_proven", checks_used=checks,
        )

    # If we have a cmd we may need to checkout commits; refuse a dirty tree.
    if cmd is not None and gx.status(root):
        raise proofs_mod.DirtyTree(
            "working tree has uncommitted changes; commit or stash before bisect"
        )

    # If a cmd is provided, try HEAD first — maybe the regression is gone.
    if cmd is not None:
        head_status = _classify(root, to_sha, name, cmd)
        if head_status:
            return BisectResult(
                from_sha=from_sha, to_sha=to_sha, suspect=None,
                reason="head_is_proven", checks_used=checks,
            )

    in_range = _range_oldest_first(root, from_sha, to_sha)
    suspect: str | None = None
    missing: list[str] = []
    for sha in in_range:
        status = _classify(root, sha, name, cmd)
        if status is None:
            missing.append(sha)
            continue
        if status is False:
            suspect = sha
            break

    if suspect is None:
        if missing:
            return BisectResult(
                from_sha=from_sha, to_sha=to_sha, suspect=None,
                reason="gaps", checks_used=checks, missing=missing,
            )
        return BisectResult(
            from_sha=from_sha, to_sha=to_sha, suspect=None,
            reason="head_passes", checks_used=checks,
        )

    # If the suspect is a merge commit, recurse one hop into the merged-in
    # branch (second parent) to find the true offending commit on the side.
    if gx.is_merge_commit(root, suspect):
        parents = gx.merge_parents(root, suspect)
        if len(parents) >= 2:
            try:
                sub = run(
                    root, name, cmd=cmd, symbol=symbol, ref=parents[1],
                )
            except NoBaseline:
                sub = None
            if sub is not None and sub.suspect is not None:
                return BisectResult(
                    from_sha=from_sha, to_sha=to_sha, suspect=sub.suspect,
                    reason="first_failure", ops=sub.ops,
                    callers_of_changed_symbols=sub.callers_of_changed_symbols,
                    checks_used=checks, via_merge=suspect,
                )

    ops, callers_list = _collect_ops_and_callers(root, suspect, symbol)
    return BisectResult(
        from_sha=from_sha, to_sha=to_sha, suspect=suspect,
        reason="first_failure", ops=ops,
        callers_of_changed_symbols=callers_list, checks_used=checks,
    )


__all__ = ["BisectResult", "NoBaseline", "run"]
