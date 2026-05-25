"""Project-wide caller index.

Builds an index of ``{symbol_name → [(file, caller, line)]}`` for every
top-level def/class call site, walking ALL .py files in a git tree (not just
one). The index is cached on disk per *tree sha* — so as long as the tree
hasn't changed (or even if you switch between branches that share a tree)
the second query is instant.

Cache layout::

    .git/gita/callers/<tree_sha>.json   # {name: [{file,caller,line}, ...]}

Tree sha is taken from ``git rev-parse <ref>^{tree}`` so the cache survives
across commits that have identical content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import libcst as cst

from . import git as gx
from .store import gita_dir


@dataclass(frozen=True)
class CallSite:
    file: str
    caller: str  # enclosing top-level symbol, or "<module>"
    line: int    # 1-based line number


def _callers_cache_dir(root: Path) -> Path:
    d = gita_dir(root) / "callers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tree_sha_for_ref(root: Path, ref: str) -> str:
    proc = gx._run(root, ["rev-parse", f"{ref}^{{tree}}"])
    return proc.stdout.strip()


def _scan_module(source: str) -> dict[str, list[tuple[str, int]]]:
    """Return ``{name: [(caller, line), ...]}`` for one module."""
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return {}
    out: dict[str, list[tuple[str, int]]] = {}
    scope: list[str] = []
    wrapper = cst.MetadataWrapper(module)
    positions = wrapper.resolve(cst.metadata.PositionProvider)

    class V(cst.CSTVisitor):
        def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
            scope.append(node.name.value)

        def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
            scope.pop()

        def visit_ClassDef(self, node: cst.ClassDef) -> None:
            scope.append(node.name.value)

        def leave_ClassDef(self, node: cst.ClassDef) -> None:
            scope.pop()

        def visit_Call(self, node: cst.Call) -> None:
            func = node.func
            target: str | None = None
            if isinstance(func, cst.Name):
                target = func.value
            elif isinstance(func, cst.Attribute):
                target = func.attr.value
            if target is None:
                return
            pos = positions.get(node)
            line = pos.start.line if pos is not None else 0
            caller = scope[-1] if scope else "<module>"
            out.setdefault(target, []).append((caller, line))

    wrapper.visit(V())
    return out


def _build_index_for_tree(root: Path, ref: str) -> dict[str, list[dict[str, Any]]]:
    """Walk the tree at ``ref``, scan every .py blob."""
    index: dict[str, list[dict[str, Any]]] = {}
    for entry in gx.ls_tree(root, ref):
        if not entry.path.endswith(".py"):
            continue
        try:
            source = gx.cat_blob(root, entry.blob_sha)
        except UnicodeDecodeError:
            continue
        per_file = _scan_module(source)
        for name, hits in per_file.items():
            for caller, line in hits:
                index.setdefault(name, []).append(
                    {"file": entry.path, "caller": caller, "line": line}
                )
    return index


def _build_index_for_working_tree(root: Path) -> dict[str, list[dict[str, Any]]]:
    proc = gx._run(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    index: dict[str, list[dict[str, Any]]] = {}
    for path in proc.stdout.split("\x00"):
        if not path or not path.endswith(".py"):
            continue
        source = gx.working_tree_text(root, path)
        if source is None:
            continue
        per_file = _scan_module(source)
        for name, hits in per_file.items():
            for caller, line in hits:
                index.setdefault(name, []).append(
                    {"file": path, "caller": caller, "line": line}
                )
    return index


def get_index(root: Path, ref: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Get the caller index for ``ref`` (default HEAD) or the working tree.

    Cached per tree sha at ``.git/gita/callers/<tree>.json``.
    """
    if ref is None:
        head = gx.head_sha(root)
        if head is None:
            return _build_index_for_working_tree(root)
        ref = head
    tree_sha = _tree_sha_for_ref(root, ref)
    cache = _callers_cache_dir(root) / f"{tree_sha}.json"
    if cache.is_file():
        return json.loads(cache.read_text(encoding="utf-8"))
    index = _build_index_for_tree(root, ref)
    cache.write_text(
        json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return index


def find(root: Path, name: str, *, ref: str | None = None) -> list[dict[str, Any]]:
    """Call sites of ``name`` in ``ref`` (default HEAD). Sorted by file, line."""
    index = get_index(root, ref)
    hits = list(index.get(name, []))
    hits.sort(key=lambda h: (h["file"], h["line"], h["caller"]))
    return hits
