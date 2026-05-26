"""Symbol lookup across a whole tree at a given rev.

``gita.parse.find`` resolves a name *within a single module*. ``gita.lookup.get``
extends that across the entire tree at a rev, and additionally walks at most
one backward ``rename_symbol`` op when a name doesn't exist at the requested
rev but did get renamed *to* that name between the requested rev and HEAD.

This is the Pillar-1 substrate for ``gita get`` (v0.2): the place where
"give me this symbol's source as-of some rev" stops being a grep.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import git as gx
from . import history as history_mod
from . import parse


# ---------------------------------------------------------------------------
# data + exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    """A resolved symbol at a specific rev, suitable for ``gita get`` output."""

    name: str           # actual name in source at rev (may differ from requested_as)
    kind: str           # "function" | "class" | "method"
    path: str           # tree-relative path of the file containing the symbol
    line_start: int     # 1-based first line of the def/class header
    line_end: int       # 1-based last line of the body
    signature: str      # rendered "def name(args) -> ret"
    body: str           # full source text of the def/class
    rev: str            # resolved commit sha the lookup was satisfied at
    requested_as: str   # the name the caller asked for (rename-aware)
    parent: str | None = None  # enclosing class name, for methods


class NotFound(LookupError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class Ambiguous(LookupError):
    def __init__(self, name: str, candidates: list[str]) -> None:
        super().__init__(f"{name}: {candidates}")
        self.name = name
        self.candidates = candidates


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def get(root: Path, name: str, *, rev: str = "HEAD") -> Symbol:
    """Resolve ``name`` to a single symbol at ``rev``.

    * Searches every ``.py`` file in the tree at ``rev``.
    * If exactly one match: returns it.
    * If multiple matches across files/classes: raises :exc:`Ambiguous` with
      ``<path>:<qualified>`` candidates.
    * If zero matches and ``rev != HEAD``: walks commits from ``rev`` forward
      to HEAD looking for a ``rename_symbol`` op with ``to == name`` (one hop
      max). If found, retries the lookup at ``rev`` under the old name.
    * If still zero: raises :exc:`NotFound`.
    """
    sha = gx.rev_parse(root, rev)
    matches = _find_in_tree(root, sha, name)
    if len(matches) == 1:
        return _to_symbol(matches[0], sha, requested_as=name)
    if len(matches) > 1:
        raise Ambiguous(name, sorted(_qualify(m) for m in matches))

    # Zero matches. Try one-hop rename walk only when we have somewhere to walk.
    try:
        head_sha = gx.rev_parse(root, "HEAD")
    except Exception:
        head_sha = sha
    if sha == head_sha:
        raise NotFound(name)

    old_name = _find_rename_back(root, sha, head_sha, name)
    if old_name is None:
        raise NotFound(name)

    matches = _find_in_tree(root, sha, old_name)
    if len(matches) == 1:
        return _to_symbol(matches[0], sha, requested_as=name)
    raise NotFound(name)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _find_in_tree(root: Path, sha: str, name: str) -> list[tuple[str, parse.Symbol]]:
    """Return every ``(path, parse.Symbol)`` matching ``name`` at tree ``sha``."""
    out: list[tuple[str, parse.Symbol]] = []
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
        for sym in parse.enumerate_matches(view, name):
            out.append((entry.path, sym))
    return out


def _qualify(match: tuple[str, parse.Symbol]) -> str:
    path, sym = match
    qual = sym.name if sym.parent is None else f"{sym.parent}.{sym.name}"
    return f"{path}:{qual}"


def _to_symbol(
    match: tuple[str, parse.Symbol], rev: str, *, requested_as: str
) -> Symbol:
    path, sym = match
    return Symbol(
        name=sym.name,
        kind=sym.kind,
        path=path,
        line_start=sym.line_start,
        line_end=sym.line_end,
        signature=sym.signature,
        body=parse._render(sym.node),
        rev=rev,
        requested_as=requested_as,
        parent=sym.parent,
    )


def _find_rename_back(
    root: Path, rev_sha: str, head_sha: str, name: str
) -> str | None:
    """Scan manifests from ``rev_sha`` forward to ``head_sha`` for a
    ``rename_symbol`` op renaming *to* ``name``. Returns the old name on
    the first hit, else ``None``.
    """
    try:
        proc = gx._run(root, ["rev-list", "--reverse", f"{rev_sha}..{head_sha}"])
    except gx.GitError:
        return None
    for sha in proc.stdout.split():
        sha = sha.strip()
        if not sha:
            continue
        try:
            manifest = history_mod.manifest_for(root, sha)
        except Exception:
            continue
        for fe in manifest.get("files", []):
            for op in fe.get("ops", []):
                if op.get("op") == "rename_symbol" and op.get("to") == name:
                    old = op.get("from")
                    if isinstance(old, str):
                        return old
    return None


__all__ = ["Symbol", "Ambiguous", "NotFound", "get"]
