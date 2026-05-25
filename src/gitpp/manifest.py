"""Structural change manifest — the agent-facing diff format.

A manifest is what ``gitpp diff`` produces and what ``gitpp commit`` persists.
It expresses a change as a list of *operations on symbols* rather than as a
patch on lines, so an agent can:

* read a 200-token summary instead of a 4000-token unified diff,
* filter by op category (``--only logic`` vs ``--exclude formatting``),
* query history by symbol without re-parsing every file at every revision.

Schema (versioned via the ``schema`` field on the root)::

    {
      "kind": "manifest", "schema": 1,
      "from": "<commit_sha or null>",   # null = empty tree / first commit
      "to":   "<commit_sha or null>",   # null = working tree
      "files": [
        {
          "path": "<repo-relative path>",
          "status": "added" | "modified" | "deleted",
          "ops": [<Op>, ...]
        },
        ...
      ],
      "summary": {
        "logic_ops": <int>,
        "signature_ops": <int>,
        "cosmetic_ops": <int>,
        "symbols_touched": ["<name>", ...]
      }
    }

Op kinds (closed set for v0.1)::

    {"op": "add_symbol",      "symbol": "<id>", "name": "...", "kind": "function|class"}
    {"op": "remove_symbol",   "symbol": "<id>", "name": "...", "kind": "function|class"}
    {"op": "rename_symbol",   "symbol": "<id>", "from": "...", "to": "...",
                              "references": <int>}
    {"op": "modify_signature","symbol": "<id>", "name": "...", "detail": "..."}
    {"op": "modify_body",     "symbol": "<id>", "name": "...",
                              "added": <int>, "removed": <int>, "detail": "..."}
    {"op": "add_import",      "module": "...", "names": [...]}
    {"op": "remove_import",   "module": "...", "names": [...]}
    {"op": "reorder_imports", "count": <int>}
    {"op": "format_only",     "detail": "..."}

Categories used in the summary:
  - logic_ops:     modify_body, add_symbol, remove_symbol
  - signature_ops: rename_symbol, modify_signature, add_import, remove_import
  - cosmetic_ops:  reorder_imports, format_only
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

import libcst as cst


# ---------------------------------------------------------------------------
# stable-ish symbol IDs
# ---------------------------------------------------------------------------


def symbol_id(kind: str, name: str) -> str:
    """Deterministic ID for a top-level symbol.

    v0.1 keys on (kind, name) only — good enough to be a useful handle in
    the manifest. A real GumTree-style matcher that survives rename is a
    v0.2 concern; for now the *diff* phase detects renames explicitly and
    emits a `rename_symbol` op that carries both names.
    """
    return hashlib.sha256(f"{kind}:{name}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# parsed view of a module (just the bits we need for diffing)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Symbol:
    kind: str           # "function" | "class"
    name: str
    signature: str      # rendered "def name(args) -> ret" line, sans body
    body_hash: str      # hash of body text with own name stripped
    body_lines: int
    node: cst.CSTNode   # original node for downstream inspection


@dataclass(frozen=True)
class _Imports:
    # We model imports as (module, sorted-tuple-of-names) so reorder is a no-op
    # and add/remove fall out of set difference.
    items: tuple[tuple[str, tuple[str, ...]], ...]
    # Source order, to detect pure reorders.
    order: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class _ModuleView:
    symbols: dict[str, _Symbol]  # keyed by name
    imports: _Imports
    raw_code: str


def _render(node: cst.CSTNode) -> str:
    return cst.Module(body=[]).code_for_node(node)


def _strip_name(text: str, name: str) -> str:
    # Replace just the def/class name token to neutralize it for body-hash.
    # Crude but adequate — only used to pair rename candidates.
    return text.replace(f" {name}(", " __SYM__(").replace(f" {name}:", " __SYM__:")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _body_text(node: cst.CSTNode) -> str:
    # We render the whole def then strip leading whitespace and the def line.
    rendered = _render(node)
    return rendered


def _parse_view(source: str) -> _ModuleView:
    module = cst.parse_module(source)
    symbols: dict[str, _Symbol] = {}
    import_items: list[tuple[str, tuple[str, ...]]] = []

    for stmt in module.body:
        if isinstance(stmt, cst.FunctionDef):
            name = stmt.name.value
            sig = _render_signature(stmt)
            body_text = _strip_name(_render(stmt.body), name)
            symbols[name] = _Symbol(
                kind="function",
                name=name,
                signature=sig,
                body_hash=_hash(body_text),
                body_lines=body_text.count("\n"),
                node=stmt,
            )
        elif isinstance(stmt, cst.ClassDef):
            name = stmt.name.value
            sig = f"class {name}"
            body_text = _strip_name(_render(stmt.body), name)
            symbols[name] = _Symbol(
                kind="class",
                name=name,
                signature=sig,
                body_hash=_hash(body_text),
                body_lines=body_text.count("\n"),
                node=stmt,
            )
        elif isinstance(stmt, cst.SimpleStatementLine):
            for small in stmt.body:
                if isinstance(small, cst.Import):
                    for alias in small.names:
                        mod = _dotted(alias.name)
                        import_items.append((mod, ()))
                elif isinstance(small, cst.ImportFrom):
                    mod = _dotted(small.module) if small.module else ""
                    if isinstance(small.names, cst.ImportStar):
                        names: tuple[str, ...] = ("*",)
                    else:
                        names = tuple(sorted(a.name.value for a in small.names))
                    import_items.append((mod, names))

    imports = _Imports(
        items=tuple(sorted(import_items)),
        order=tuple(import_items),
    )
    return _ModuleView(symbols=symbols, imports=imports, raw_code=source)


def _dotted(name_node: cst.CSTNode | None) -> str:
    if name_node is None:
        return ""
    return _render(name_node).strip()


def _render_signature(fn: cst.FunctionDef) -> str:
    params = _render(fn.params).strip()
    ret = ""
    if fn.returns is not None:
        ret = " -> " + _render(fn.returns.annotation).strip()
    prefix = "async def " if fn.asynchronous is not None else "def "
    return f"{prefix}{fn.name.value}({params}){ret}"


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def diff_sources(
    prev: str | None,
    curr: str | None,
    *,
    path: str = "<unknown>",
) -> dict[str, Any]:
    """Produce a per-file manifest entry comparing two source strings.

    Either side may be ``None`` (meaning the file was added or deleted).
    """
    if prev is None and curr is None:
        return {"path": path, "status": "unchanged", "ops": []}
    if prev is None:
        view = _parse_view(curr or "")
        ops = _ops_for_added_file(view)
        return {"path": path, "status": "added", "ops": ops}
    if curr is None:
        view = _parse_view(prev)
        ops = _ops_for_removed_file(view)
        return {"path": path, "status": "deleted", "ops": ops}
    if prev == curr:
        return {"path": path, "status": "unchanged", "ops": []}

    prev_v = _parse_view(prev)
    curr_v = _parse_view(curr)
    ops = _diff_views(prev_v, curr_v)
    if not ops:
        # Source bytes differ but no semantic change we can name.
        ops = [{"op": "format_only", "detail": "whitespace or comment only"}]
    status = "modified"
    return {"path": path, "status": status, "ops": ops}


def _ops_for_added_file(view: _ModuleView) -> list[dict[str, Any]]:
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


def _ops_for_removed_file(view: _ModuleView) -> list[dict[str, Any]]:
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


def _diff_imports(prev: _Imports, curr: _Imports) -> list[dict[str, Any]]:
    prev_set = set(prev.items)
    curr_set = set(curr.items)
    added = sorted(curr_set - prev_set)
    removed = sorted(prev_set - curr_set)
    ops: list[dict[str, Any]] = []
    for mod, names in added:
        ops.append({"op": "add_import", "module": mod, "names": list(names)})
    for mod, names in removed:
        ops.append({"op": "remove_import", "module": mod, "names": list(names)})
    if not added and not removed and prev.order != curr.order:
        ops.append({"op": "reorder_imports", "count": len(prev.order)})
    return ops


def _diff_symbols(prev: _ModuleView, curr: _ModuleView) -> list[dict[str, Any]]:
    prev_names = set(prev.symbols)
    curr_names = set(curr.symbols)

    common = prev_names & curr_names
    only_prev = prev_names - curr_names
    only_curr = curr_names - prev_names

    # --- Rename detection (pass 1): pair leftover prev/curr by body_hash ----
    # Body_hash is computed with the def's own name stripped, so it survives
    # the rename itself.
    prev_left = {n: prev.symbols[n] for n in only_prev}
    curr_left = {n: curr.symbols[n] for n in only_curr}
    rename_pairs: list[tuple[_Symbol, _Symbol]] = []

    for p_name, p_sym in list(prev_left.items()):
        match: _Symbol | None = None
        for c_name, c_sym in curr_left.items():
            if c_sym.kind == p_sym.kind and c_sym.body_hash == p_sym.body_hash:
                match = c_sym
                break
        if match is not None:
            rename_pairs.append((p_sym, match))
            del prev_left[p_name]
            del curr_left[match.name]

    # --- Build rename substitution map ---------------------------------------
    # When we compare bodies of *other* (same-name) symbols, we need to
    # neutralize call-site rewrites caused by the rename, otherwise every
    # caller of a renamed function shows up as a spurious modify_body op.
    rename_subs: dict[str, str] = {}
    for i, (ps, cs) in enumerate(rename_pairs):
        token = f"__RENAMED_{i}__"
        rename_subs[ps.name] = token
        rename_subs[cs.name] = token

    # --- Emit ops -----------------------------------------------------------
    ops: list[dict[str, Any]] = []

    # Same-name symbols: compare with rename subs applied to bodies.
    for name in sorted(common):
        ps, cs = prev.symbols[name], curr.symbols[name]
        ops.extend(
            _diff_symbol_pair(ps, cs, renamed_to=None, extra_subs=rename_subs)
        )

    # Renamed symbols: emit rename_symbol op + any incidental changes.
    for ps, cs in rename_pairs:
        refs = _count_name_uses(curr.raw_code, cs.name) + 1
        ops.append({
            "op": "rename_symbol",
            "symbol": symbol_id(cs.kind, cs.name),
            "from": ps.name,
            "to": cs.name,
            "references": refs,
        })
        ops.extend(
            _diff_symbol_pair(ps, cs, renamed_to=cs.name, extra_subs=rename_subs)
        )

    # Remaining unmatched
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


def _diff_symbol_pair(
    ps: _Symbol,
    cs: _Symbol,
    *,
    renamed_to: str | None,
    extra_subs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Compare two symbols that we believe represent the same logical thing.

    ``extra_subs`` is a name→placeholder map applied to both bodies before
    comparison, used to mask out call-site renames of OTHER symbols.
    """
    ops: list[dict[str, Any]] = []
    sym = symbol_id(cs.kind, cs.name)

    # Signature change (ignoring the bare name itself when this is a rename).
    if ps.signature != cs.signature:
        ps_sig_norm = ps.signature.replace(ps.name, "__SYM__", 1)
        cs_sig_norm = cs.signature.replace(cs.name, "__SYM__", 1)
        if ps_sig_norm != cs_sig_norm:
            ops.append({
                "op": "modify_signature",
                "symbol": sym,
                "name": cs.name,
                "detail": f"{ps.signature.strip()}  →  {cs.signature.strip()}",
            })

    # Body change. Strip the def's own name (handles self-rename + recursion)
    # and apply rename subs for OTHER symbols so callers don't look modified.
    ps_body = _strip_name(_render(ps.node), ps.name)
    cs_body = _strip_name(_render(cs.node), cs.name)
    if renamed_to is not None:
        ps_body = ps_body.replace(ps.name, "__SYM__")
        cs_body = cs_body.replace(cs.name, "__SYM__")
    if extra_subs:
        for old, token in extra_subs.items():
            # Skip the symbol's own name — already handled above.
            if old in (ps.name, cs.name):
                continue
            ps_body = ps_body.replace(old, token)
            cs_body = cs_body.replace(old, token)

    if ps_body != cs_body:
        added, removed = _line_delta(ps_body, cs_body)
        ops.append({
            "op": "modify_body",
            "symbol": sym,
            "name": cs.name,
            "added": added,
            "removed": removed,
            "detail": _summarize_body_change(ps_body, cs_body),
        })

    return ops


def _line_delta(a: str, b: str) -> tuple[int, int]:
    a_lines = [l for l in a.splitlines() if l.strip()]
    b_lines = [l for l in b.splitlines() if l.strip()]
    a_set = set(a_lines)
    b_set = set(b_lines)
    added = len([l for l in b_lines if l not in a_set])
    removed = len([l for l in a_lines if l not in b_set])
    return added, removed


def _summarize_body_change(a: str, b: str) -> str:
    a_set = {l.strip() for l in a.splitlines() if l.strip()}
    b_set = {l.strip() for l in b.splitlines() if l.strip()}
    only_b = [l for l in b.splitlines() if l.strip() and l.strip() not in a_set]
    only_a = [l for l in a.splitlines() if l.strip() and l.strip() not in b_set]
    bits = []
    if only_b:
        bits.append(f"+{only_b[0].strip()[:80]}")
        if len(only_b) > 1:
            bits.append(f"(+{len(only_b) - 1} more)")
    if only_a:
        bits.append(f"-{only_a[0].strip()[:80]}")
        if len(only_a) > 1:
            bits.append(f"(-{len(only_a) - 1} more)")
    return " ".join(bits) or "internal change"


def _count_name_uses(source: str, name: str) -> int:
    """Cheap, lexical count of how many times a bare name token appears.

    Intentionally simple — used only to populate the ``references`` field on a
    rename op, where being approximately right is fine.
    """
    import re
    return len(re.findall(rf"\b{re.escape(name)}\b", source)) - 1  # minus the def


# ---------------------------------------------------------------------------
# top-level manifest assembly
# ---------------------------------------------------------------------------


_CATEGORY = {
    "modify_body": "logic",
    "add_symbol": "logic",
    "remove_symbol": "logic",
    "rename_symbol": "signature",
    "modify_signature": "signature",
    "add_import": "signature",
    "remove_import": "signature",
    "reorder_imports": "cosmetic",
    "format_only": "cosmetic",
}


def build_manifest(
    files: Iterable[dict[str, Any]],
    *,
    from_sha: str | None,
    to_sha: str | None,
) -> dict[str, Any]:
    """Assemble a manifest from per-file entries + compute the summary."""
    file_list = [f for f in files if f["ops"] or f["status"] != "unchanged"]
    counts = {"logic": 0, "signature": 0, "cosmetic": 0}
    symbols_touched: set[str] = set()
    for fe in file_list:
        for op in fe["ops"]:
            cat = _CATEGORY.get(op["op"], "other")
            if cat in counts:
                counts[cat] += 1
            if "name" in op:
                symbols_touched.add(op["name"])
            if op["op"] == "rename_symbol":
                symbols_touched.add(op["from"])
                symbols_touched.add(op["to"])
    return {
        "kind": "manifest",
        "schema": 1,
        "from": from_sha,
        "to": to_sha,
        "files": file_list,
        "summary": {
            "logic_ops": counts["logic"],
            "signature_ops": counts["signature"],
            "cosmetic_ops": counts["cosmetic"],
            "symbols_touched": sorted(symbols_touched),
        },
    }


# ---------------------------------------------------------------------------
# human-readable rendering
# ---------------------------------------------------------------------------


def render_manifest(manifest: dict[str, Any]) -> str:
    """Compact human (and agent) readable form. ~200 tokens for typical PRs."""
    lines: list[str] = []
    s = manifest["summary"]
    lines.append(
        f"manifest: {s['logic_ops']} logic / {s['signature_ops']} signature / "
        f"{s['cosmetic_ops']} cosmetic op(s), {len(s['symbols_touched'])} symbol(s)"
    )
    for fe in manifest["files"]:
        lines.append(f"  {fe['status']:<9} {fe['path']}")
        for op in fe["ops"]:
            lines.append("    " + _render_op(op))
    return "\n".join(lines) + "\n"


def _render_op(op: dict[str, Any]) -> str:
    k = op["op"]
    if k == "rename_symbol":
        return f"rename     {op['from']} → {op['to']}  ({op['references']} ref(s))"
    if k == "add_symbol":
        return f"add        {op['kind']} {op['name']}"
    if k == "remove_symbol":
        return f"remove     {op['kind']} {op['name']}"
    if k == "modify_signature":
        return f"signature  {op['name']}: {op['detail']}"
    if k == "modify_body":
        return f"body       {op['name']}: +{op['added']}/-{op['removed']}  {op['detail']}"
    if k == "add_import":
        names = ", ".join(op["names"]) if op["names"] else ""
        suffix = f" ({names})" if names else ""
        return f"import +   {op['module']}{suffix}"
    if k == "remove_import":
        names = ", ".join(op["names"]) if op["names"] else ""
        suffix = f" ({names})" if names else ""
        return f"import -   {op['module']}{suffix}"
    if k == "reorder_imports":
        return f"imports    reordered ({op['count']} entries, no add/remove)"
    if k == "format_only":
        return f"format     {op.get('detail', '')}"
    return repr(op)
