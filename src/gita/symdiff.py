"""Per-file structural diff with similarity-based rename detection.

This module replaces the strict ``body_hash == body_hash`` rename pairing
from :mod:`gitpp.manifest` with a similarity score. Two unmatched symbols
(one removed, one added) are paired as a rename when:

* they have the same kind (function / class),
* AND their body-text similarity (``difflib.SequenceMatcher.ratio()`` over
  name-neutralized bodies) is ≥ :data:`RENAME_THRESHOLD`.

Pairs are chosen greedily best-score-first, so the strongest candidate wins
and weaker ones fall back to add/remove. This handles the common case of
"rename + small body edit in the same commit" that the v0 pairing missed.

Everything else (signature/body/import op extraction, summary) comes from
:mod:`gitpp.manifest` unchanged.
"""

from __future__ import annotations

import difflib
from typing import Any

# Reuse the heavy lifting from gitpp.manifest. We only override the
# function-level routing so we can swap in similarity-based pairing.
from gitpp.manifest import (
    _Symbol,           # type: ignore[attr-defined]
    _ModuleView,       # type: ignore[attr-defined]
    _parse_view,       # type: ignore[attr-defined]
    _strip_name,       # type: ignore[attr-defined]
    _render,           # type: ignore[attr-defined]
    _diff_imports,     # type: ignore[attr-defined]
    _diff_symbol_pair, # type: ignore[attr-defined]
    _count_name_uses,  # type: ignore[attr-defined]
    symbol_id,
)


RENAME_THRESHOLD = 0.6


def diff_sources(
    prev: str | None,
    curr: str | None,
    *,
    path: str = "<unknown>",
) -> dict[str, Any]:
    """Per-file manifest entry. Either side may be ``None`` (add/delete)."""
    if prev is None and curr is None:
        return {"path": path, "status": "unchanged", "ops": []}
    if prev is None:
        return {"path": path, "status": "added", "ops": _ops_added(_safe_view(curr or ""))}
    if curr is None:
        return {"path": path, "status": "deleted", "ops": _ops_removed(_safe_view(prev))}
    if prev == curr:
        return {"path": path, "status": "unchanged", "ops": []}

    prev_v = _safe_view(prev)
    curr_v = _safe_view(curr)
    if prev_v is None or curr_v is None:
        # Unparseable on one side: fall back to a single opaque op so we
        # still register the change instead of dropping it.
        return {
            "path": path,
            "status": "modified",
            "ops": [{"op": "format_only", "detail": "unparseable on one side"}],
        }
    ops = _diff_views(prev_v, curr_v)
    if not ops:
        ops = [{"op": "format_only", "detail": "whitespace or comment only"}]
    return {"path": path, "status": "modified", "ops": ops}


def _safe_view(source: str) -> _ModuleView | None:
    try:
        return _parse_view(source)
    except Exception:
        return None


def _ops_added(view: _ModuleView) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for mod, names in view.imports.order:
        ops.append({"op": "add_import", "module": mod, "names": list(names)})
    for sym in view.symbols.values():
        ops.append({
            "op": "add_symbol",
            "symbol": symbol_id(sym.kind, sym.name),
            "name": sym.name,
            "kind": sym.kind,
        })
    return ops


def _ops_removed(view: _ModuleView) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for sym in view.symbols.values():
        ops.append({
            "op": "remove_symbol",
            "symbol": symbol_id(sym.kind, sym.name),
            "name": sym.name,
            "kind": sym.kind,
        })
    return ops


def _diff_views(prev: _ModuleView, curr: _ModuleView) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    ops.extend(_diff_imports(prev.imports, curr.imports))
    ops.extend(_diff_symbols(prev, curr))
    return ops


def _normalized_body(sym: _Symbol) -> str:
    """Body text with the symbol's own name neutralized — comparable across rename."""
    return _strip_name(_render(sym.node), sym.name)


def _pair_renames(
    prev_left: dict[str, _Symbol],
    curr_left: dict[str, _Symbol],
) -> list[tuple[_Symbol, _Symbol]]:
    """Greedy best-score-first matching above :data:`RENAME_THRESHOLD`.

    Time is O(P*C) for P removed × C added unmatched symbols — fine for the
    typical-commit sizes we care about.
    """
    if not prev_left or not curr_left:
        return []

    # Pre-render bodies once.
    prev_bodies = {n: _normalized_body(s) for n, s in prev_left.items()}
    curr_bodies = {n: _normalized_body(s) for n, s in curr_left.items()}

    candidates: list[tuple[float, str, str]] = []
    for pn, p_sym in prev_left.items():
        for cn, c_sym in curr_left.items():
            if p_sym.kind != c_sym.kind:
                continue
            ratio = difflib.SequenceMatcher(
                None, prev_bodies[pn], curr_bodies[cn], autojunk=False
            ).ratio()
            if ratio >= RENAME_THRESHOLD:
                candidates.append((ratio, pn, cn))

    candidates.sort(reverse=True)  # best score first
    pairs: list[tuple[_Symbol, _Symbol]] = []
    used_prev: set[str] = set()
    used_curr: set[str] = set()
    for _ratio, pn, cn in candidates:
        if pn in used_prev or cn in used_curr:
            continue
        pairs.append((prev_left[pn], curr_left[cn]))
        used_prev.add(pn)
        used_curr.add(cn)
    return pairs


def _diff_symbols(prev: _ModuleView, curr: _ModuleView) -> list[dict[str, Any]]:
    prev_names = set(prev.symbols)
    curr_names = set(curr.symbols)

    common = prev_names & curr_names
    only_prev = prev_names - curr_names
    only_curr = curr_names - prev_names

    prev_left = {n: prev.symbols[n] for n in only_prev}
    curr_left = {n: curr.symbols[n] for n in only_curr}

    rename_pairs = _pair_renames(prev_left, curr_left)
    for ps, cs in rename_pairs:
        del prev_left[ps.name]
        del curr_left[cs.name]

    # Substitution map so callers of a renamed symbol don't show as modified.
    rename_subs: dict[str, str] = {}
    for i, (ps, cs) in enumerate(rename_pairs):
        token = f"__RENAMED_{i}__"
        rename_subs[ps.name] = token
        rename_subs[cs.name] = token

    ops: list[dict[str, Any]] = []

    for name in sorted(common):
        ps, cs = prev.symbols[name], curr.symbols[name]
        ops.extend(_diff_symbol_pair(ps, cs, renamed_to=None, extra_subs=rename_subs))

    for ps, cs in rename_pairs:
        refs = _count_name_uses(curr.raw_code, cs.name) + 1
        ops.append({
            "op": "rename_symbol",
            "symbol": symbol_id(cs.kind, cs.name),
            "from": ps.name,
            "to": cs.name,
            "references": refs,
        })
        ops.extend(_diff_symbol_pair(ps, cs, renamed_to=cs.name, extra_subs=rename_subs))

    for name in sorted(prev_left):
        s = prev_left[name]
        ops.append({
            "op": "remove_symbol",
            "symbol": symbol_id(s.kind, s.name),
            "name": s.name,
            "kind": s.kind,
        })
    for name in sorted(curr_left):
        s = curr_left[name]
        ops.append({
            "op": "add_symbol",
            "symbol": symbol_id(s.kind, s.name),
            "name": s.name,
            "kind": s.kind,
        })
    return ops
