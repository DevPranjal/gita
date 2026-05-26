"""Commit-keyed check results — the substrate for ``gita prove`` / ``last-proven``.

Long-running agents commit often; some commits pass tests, some don't, some
are mid-thought checkpoints. ``proofs.record`` runs a named check (pytest,
mypy, anything) and stores its result against the current ``HEAD`` commit.
``proofs.last_proven`` walks history newest-first and answers
"what's the last commit where this check was ok?", optionally constrained
to commits where a particular symbol exists.

Storage shape — ``.git/gita/proofs/<sha>.json``::

    {
      "commit": "<sha>",
      "checks": {
        "pytest": {
          "ok": true,
          "exit_code": 0,
          "duration_ms": 4218,
          "ran_at": "2026-05-26T12:08:14Z",
          "cmd": ["python", "-m", "pytest"],
          "stdout_head": "...",
          "stdout_tail": "...",
          "truncated": true
        }
      }
    }

Three deliberate non-features (see ``docs/v0.2.md``):

* Not signed — proofs are local hints, not attestations.
* Not retroactive — you can only prove the commit currently checked out.
* Not tree-keyed — proofs attach to commits, so a dirty tree is refused.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import git as gx
from . import parse


# 4 KB head + 4 KB tail. Anything larger goes to the agent's own logs.
_STDOUT_CAP = 4096


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------


class DirtyTree(RuntimeError):
    """Raised by :func:`record` when the working tree has uncommitted changes."""


class NoProofs(LookupError):
    """Raised by :func:`last_proven` when no commit reachable from ``ref``
    has any recorded proof matching the filter."""


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProofResult:
    name: str
    ok: bool
    exit_code: int
    duration_ms: int


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


def proofs_dir(root: Path) -> Path:
    return root / ".git" / "gita" / "proofs"


def proof_path(root: Path, sha: str) -> Path:
    return proofs_dir(root) / f"{sha}.json"


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def record(
    root: Path,
    name: str,
    *,
    cmd: list[str],
    stdout_max: int = _STDOUT_CAP,
) -> ProofResult:
    """Run ``cmd`` and record the result under ``name`` at the current HEAD.

    Raises :exc:`DirtyTree` if the working tree has uncommitted changes —
    proofs are commit-keyed, so attaching them to a stale commit would lie.
    """
    if gx.status(root):
        raise DirtyTree(
            "working tree has uncommitted changes; commit or stash before proving"
        )

    sha = gx.rev_parse(root, "HEAD")

    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=False)
    duration_ms = int((time.monotonic() - started) * 1000)

    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    combined = stdout + (("\n" + stderr) if stderr else "")
    head, tail, truncated = _split_output(combined, stdout_max)

    check = {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "ran_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cmd": list(cmd),
        "stdout_head": head,
        "stdout_tail": tail,
        "truncated": truncated,
    }

    existing = read(root, sha) or {"commit": sha, "checks": {}}
    existing["commit"] = sha
    existing["checks"][name] = check

    proofs_dir(root).mkdir(parents=True, exist_ok=True)
    proof_path(root, sha).write_text(
        json.dumps(existing, indent=2), encoding="utf-8"
    )

    return ProofResult(
        name=name,
        ok=check["ok"],
        exit_code=check["exit_code"],
        duration_ms=duration_ms,
    )


def _split_output(text: str, cap: int) -> tuple[str, str, bool]:
    if len(text) <= cap * 2:
        return text, "", False
    return text[:cap], text[-cap:], True


# ---------------------------------------------------------------------------
# read / query
# ---------------------------------------------------------------------------


def read(root: Path, sha: str) -> dict[str, Any] | None:
    p = proof_path(root, sha)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def last_proven(
    root: Path,
    name: str | None = None,
    *,
    symbol: str | None = None,
    ref: str = "HEAD",
) -> str:
    """Newest commit reachable from ``ref`` whose proofs satisfy the filter.

    * ``name=None`` — *every* recorded check on the commit must be ``ok``.
      Commits with no recorded checks at all are skipped.
    * ``name="pytest"`` — that specific check must exist and be ``ok``.
    * ``symbol="foo"`` — additionally requires the symbol to resolve at the
      commit (uses :func:`gita.lookup.get`, so rename-walking is *not*
      applied here — we want the actual presence at the commit).

    Raises :exc:`NoProofs` if nothing matches.
    """
    saw_any = False
    for sha in gx.log_shas(root, ref):
        stored = read(root, sha)
        if stored is None:
            continue
        checks = stored.get("checks") or {}
        if not checks:
            continue
        saw_any = True

        if name is not None:
            entry = checks.get(name)
            if entry is None or not entry.get("ok"):
                continue
        else:
            if not all(c.get("ok") for c in checks.values()):
                continue

        if symbol is not None and not _symbol_present(root, sha, symbol):
            continue

        return sha

    if not saw_any:
        raise NoProofs(f"no proofs recorded under {ref}")
    raise NoProofs(f"no commit reachable from {ref} satisfies the filter")


def _symbol_present(root: Path, sha: str, name: str) -> bool:
    """True iff ``name`` resolves to at least one symbol in tree at ``sha``."""
    try:
        for entry in gx.ls_tree(root, sha):
            if not entry.path.endswith(".py"):
                continue
            try:
                source = gx.cat_blob(root, entry.blob_sha)
            except UnicodeDecodeError:
                continue
            view = parse.parse_module(source)
            if view is None:
                continue
            if parse.enumerate_matches(view, name):
                return True
    except gx.GitError:
        return False
    return False


# ---------------------------------------------------------------------------
# glyphs — single source of truth for symbol-log / explain / mcp
# ---------------------------------------------------------------------------

GLYPH_OK = "\u2713"        # ✓
GLYPH_FAIL = "\u2717"      # ✗
GLYPH_UNKNOWN = "\u00b7"   # ·


def glyph(proof: dict[str, Any] | None) -> str:
    """Three-state glyph for a stored proof dict (or ``None`` = unknown).

    ``✓`` = every recorded check is ok, ``✗`` = at least one failed,
    ``·`` = no proof recorded at all. Never two states.
    """
    if proof is None:
        return GLYPH_UNKNOWN
    checks = proof.get("checks") or {}
    if not checks:
        return GLYPH_UNKNOWN
    if all(c.get("ok") for c in checks.values()):
        return GLYPH_OK
    return GLYPH_FAIL


# ---------------------------------------------------------------------------
# auto-prove config — drives the post-commit hook (gita.hooks)
# ---------------------------------------------------------------------------


def auto_config_path(root: Path) -> Path:
    return root / ".git" / "gita" / "auto.json"


def read_auto_config(root: Path) -> dict[str, Any]:
    """Load the auto-prove config. Returns an empty shell if missing/invalid."""
    p = auto_config_path(root)
    if not p.exists():
        return {"checks": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"checks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("checks"), dict):
        return {"checks": {}}
    return data


def write_auto_config(root: Path, cfg: dict[str, Any]) -> Path:
    p = auto_config_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(cfg, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return p


def set_auto(
    root: Path,
    name: str,
    *,
    cmd: list[str],
    enabled: bool = True,
) -> None:
    """Enable (or re-enable) a check for auto-prove with the given command."""
    cfg = read_auto_config(root)
    cfg.setdefault("checks", {})[name] = {
        "cmd": list(cmd),
        "enabled": bool(enabled),
    }
    write_auto_config(root, cfg)


def disable_auto(root: Path, name: str) -> None:
    """Mark a configured check disabled. No-op if the check isn't configured."""
    cfg = read_auto_config(root)
    entry = cfg.get("checks", {}).get(name)
    if entry is None:
        return
    entry["enabled"] = False
    write_auto_config(root, cfg)


def list_auto(root: Path) -> dict[str, dict[str, Any]]:
    """Public view of configured checks (sorted by name for stable output)."""
    cfg = read_auto_config(root)
    return dict(sorted(cfg.get("checks", {}).items()))


def auto_prove_for_head(root: Path) -> list[ProofResult]:
    """Run every enabled auto-prove check against the current HEAD.

    Designed to be invoked from the post-commit hook, so it must never raise
    on conditions that would block a commit: a dirty tree (rare just after a
    commit, but possible if files were written between commit and hook),
    missing config, or a check command that exits non-zero. The hook is a
    convenience layer; bugs in checks should never break ``git commit``.
    """
    if not gx.is_git_dir(root):
        return []
    cfg = read_auto_config(root)
    checks = cfg.get("checks") or {}
    if not checks:
        return []
    # If anything is dirty we can't honestly attach a proof to HEAD; bail.
    try:
        if gx.status(root):
            return []
    except gx.GitError:
        return []

    results: list[ProofResult] = []
    for name in sorted(checks):
        entry = checks[name]
        if not entry.get("enabled"):
            continue
        cmd = entry.get("cmd")
        if not cmd:
            continue
        try:
            results.append(record(root, name, cmd=list(cmd)))
        except DirtyTree:
            # Concurrent edit landed between status() and record(); skip.
            continue
        except Exception:
            # Never let a misbehaving check break the hook.
            continue
    return results


__all__ = [
    "DirtyTree",
    "NoProofs",
    "ProofResult",
    "record",
    "read",
    "last_proven",
    "proofs_dir",
    "proof_path",
    "glyph",
    "GLYPH_OK",
    "GLYPH_FAIL",
    "GLYPH_UNKNOWN",
    "auto_config_path",
    "read_auto_config",
    "write_auto_config",
    "set_auto",
    "disable_auto",
    "list_auto",
    "auto_prove_for_head",
]
