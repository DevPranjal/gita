"""``gita`` command-line interface.

All subcommands operate on the enclosing git repository (auto-discovered
from cwd) and persist manifests under ``.git/gita/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from gita._manifest import render_manifest

from . import callers as callers_mod
from . import context as context_mod
from . import git as gx
from . import history as history_mod
from . import lookup as lookup_mod
from . import notes as notes_mod
from . import proofs as proofs_mod
from . import store
from . import who as who_mod
from . import bisect as bisect_mod
from . import hooks as hooks_mod
from .diff import build_for_commit, build_for_refs, build_for_working_tree


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_OP_CATEGORIES = {
    "logic": {"modify_body", "add_symbol", "remove_symbol"},
    "signature": {"rename_symbol", "modify_signature", "add_import", "remove_import"},
    "cosmetic": {"reorder_imports", "format_only"},
}


def _filter_manifest(
    manifest: dict,
    *,
    only: list[str] | None,
    exclude: list[str] | None,
    symbol: str | None = None,
) -> dict:
    if not only and not exclude and not symbol:
        return manifest
    allowed = set().union(*(_OP_CATEGORIES[c] for c in (only or _OP_CATEGORIES)))
    if exclude:
        for c in exclude:
            allowed -= _OP_CATEGORIES[c]
    new_files = []
    for fe in manifest["files"]:
        if symbol is not None and fe.get("parseable") is False:
            # Symbol filter is python-symbol scoped; drop textual entries.
            continue
        kept = [
            op for op in fe.get("ops", [])
            if op["op"] in allowed and (symbol is None or _op_mentions(op, symbol))
        ]
        if symbol is not None:
            if not kept:
                continue
        elif not kept and fe["status"] == "modified":
            continue
        new_files.append({**fe, "ops": kept})
    return {**manifest, "files": new_files}


def _op_mentions(op: dict, symbol: str) -> bool:
    if op["op"] == "rename_symbol":
        return op.get("from") == symbol or op.get("to") == symbol
    return op.get("name") == symbol


def _discover_root() -> Path:
    try:
        return gx.discover_root(Path.cwd())
    except FileNotFoundError as e:
        sys.stderr.write(f"gita: {e}\n")
        raise SystemExit(2)


def _emit(manifest: dict, *, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    else:
        sys.stdout.write(render_manifest(manifest))


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve()
    if not gx.is_git_dir(target):
        gx.init(target)
        sys.stdout.write(f"Initialized git repository in {target / '.git'}\n")
    store.init(target)
    sys.stdout.write(f"Initialized gita store in {store.gita_dir(target)}\n")
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    root = _discover_root()
    if args.staged:
        manifest = build_for_working_tree(root, staged=True)
    elif args.from_ref is None and args.to_ref is None:
        manifest = build_for_working_tree(root, staged=False)
    elif args.to_ref is None:
        # `gita diff <ref>` → HEAD vs <ref> (so users can say "what changed since X").
        manifest = build_for_refs(root, args.from_ref, "HEAD")
    else:
        manifest = build_for_refs(root, args.from_ref, args.to_ref)
    manifest = _filter_manifest(
        manifest, only=args.only, exclude=args.exclude, symbol=args.symbol
    )
    _emit(manifest, as_json=args.json)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    root = _discover_root()
    entries = gx.status(root)
    manifest = build_for_working_tree(root, staged=False)
    summary = manifest["summary"]
    branch = gx.current_branch(root) or "(detached)"
    head = gx.head_sha(root)
    sys.stdout.write(f"on branch {branch}")
    if head:
        sys.stdout.write(f" at {head[:12]}")
    sys.stdout.write("\n\n")

    if not entries:
        sys.stdout.write("working tree clean\n")
    else:
        sys.stdout.write("changes:\n")
        for e in entries:
            marker = f"{e.index_status}{e.work_status}"
            if e.orig_path:
                sys.stdout.write(f"  {marker}  {e.orig_path} -> {e.path}\n")
            else:
                sys.stdout.write(f"  {marker}  {e.path}\n")
        sys.stdout.write("\n")

    sys.stdout.write(
        f"manifest (HEAD → working): "
        f"{summary['logic_ops']} logic / {summary['signature_ops']} signature / "
        f"{summary['cosmetic_ops']} cosmetic op(s), "
        f"{len(summary['symbols_touched'])} symbol(s)\n"
    )
    if args.verbose and manifest["files"]:
        sys.stdout.write("\n")
        sys.stdout.write(render_manifest(manifest))
    return 0


def _cmd_commit(args: argparse.Namespace) -> int:
    root = _discover_root()
    store.init(root)
    sha = gx.commit(root, args.message, allow_empty=args.allow_empty)
    manifest = build_for_commit(root, sha)
    store.write(root, sha, manifest)
    branch = gx.current_branch(root) or "(detached)"
    s = manifest["summary"]
    sys.stdout.write(
        f"[{branch} {sha[:12]}] {args.message}\n"
        f"  manifest: {s['logic_ops']} logic / {s['signature_ops']} signature / "
        f"{s['cosmetic_ops']} cosmetic op(s)\n"
    )
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    root = _discover_root()
    sha = gx.rev_parse(root, args.ref)
    manifest = history_mod.manifest_for(root, sha)
    manifest = _filter_manifest(manifest, only=args.only, exclude=args.exclude)
    proof = proofs_mod.read(root, sha)
    if args.json:
        payload: dict = {"commit": sha, **manifest}
        if proof is not None:
            payload["proofs"] = proof.get("checks", {})
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    else:
        meta = gx.commit_meta(root, sha)
        when = datetime.fromtimestamp(meta.timestamp, tz=timezone.utc).isoformat()
        sys.stdout.write(f"commit {sha}\n")
        sys.stdout.write(f"  author: {meta.author_name} <{meta.author_email}>\n")
        sys.stdout.write(f"  date:   {when}\n\n")
        sys.stdout.write(f"    {meta.message.splitlines()[0] if meta.message else ''}\n\n")
        sys.stdout.write(render_manifest(manifest))
        if proof is not None and proof.get("checks"):
            sys.stdout.write("\nproofs:\n")
            for cname, c in proof["checks"].items():
                g = proofs_mod.GLYPH_OK if c.get("ok") else proofs_mod.GLYPH_FAIL
                sys.stdout.write(f"  {g} {cname}  (exit {c.get('exit_code')})\n")
    return 0


def _cmd_symbol_log(args: argparse.Namespace) -> int:
    root = _discover_root()
    entries = history_mod.symbol_log(root, args.name, max_count=args.max_count)
    if args.json:
        sys.stdout.write(json.dumps(entries, indent=2) + "\n")
        return 0
    if not entries:
        sys.stdout.write(f"no commits touch symbol {args.name!r}\n")
        return 0
    for entry in entries:
        g = proofs_mod.glyph(proofs_mod.read(root, entry["sha"]))
        sys.stdout.write(
            f"{entry['sha'][:12]}  {g}  {entry['message'].splitlines()[0]}\n"
        )
        for op in entry["ops"]:
            sys.stdout.write(f"    [{op['path']}] {_render_history_op(op)}\n")
    return 0


def _render_history_op(op: dict) -> str:
    k = op["op"]
    if k == "rename_symbol":
        return f"rename {op['from']} → {op['to']}"
    if k in ("add_symbol", "remove_symbol"):
        verb = "add" if k == "add_symbol" else "remove"
        return f"{verb} {op.get('kind','symbol')} {op['name']}"
    if k == "modify_body":
        return f"body {op['name']}: +{op.get('added',0)}/-{op.get('removed',0)}"
    if k == "modify_signature":
        return f"signature {op['name']}: {op.get('detail','')}"
    return f"{k} {op.get('name','')}"


def _cmd_callers(args: argparse.Namespace) -> int:
    root = _discover_root()
    name, ref = _split_at_ref(args.target)
    hits = callers_mod.find(root, name, ref=ref)
    if args.json:
        sys.stdout.write(json.dumps({"name": name, "ref": ref, "callers": hits}, indent=2) + "\n")
        return 0
    if not hits:
        sys.stdout.write(f"no callers of {name!r}\n")
        return 0
    sys.stdout.write(f"{len(hits)} call site(s) of {name!r}:\n")
    for h in hits:
        sys.stdout.write(f"  {h['file']}:{h['line']}  in {h['caller']}\n")
    return 0


def _split_at_ref(target: str) -> tuple[str, str | None]:
    if "@" in target:
        name, ref = target.split("@", 1)
        return name, ref or None
    return target, None


def _cmd_get(args: argparse.Namespace) -> int:
    root = _discover_root()
    name, ref = _split_at_ref(args.target)
    rev = ref or "HEAD"
    try:
        sym = lookup_mod.get(root, name, rev=rev)
    except lookup_mod.Ambiguous as exc:
        sys.stderr.write(f"gita: {name!r} is ambiguous; candidates:\n")
        for c in exc.candidates:
            sys.stderr.write(f"  - {c}\n")
        return 2
    except lookup_mod.NotFound as exc:
        sys.stderr.write(f"gita: {exc.name!r} not found at {rev}\n")
        return 1

    if args.json:
        payload = {
            "name": sym.name,
            "kind": sym.kind,
            "path": sym.path,
            "line_start": sym.line_start,
            "line_end": sym.line_end,
            "signature": sym.signature,
            "body": sym.body,
            "rev": sym.rev,
            "requested_as": sym.requested_as,
            "parent": sym.parent,
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    qual = sym.name if sym.parent is None else f"{sym.parent}.{sym.name}"
    sys.stdout.write(
        f"{sym.path}:{sym.line_start}-{sym.line_end}  {sym.kind} {qual}\n\n{sym.body}"
    )
    if not sym.body.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    root = _discover_root()
    result = history_mod.reindex(root, force=args.force)
    sys.stdout.write(
        f"reindex: computed {result['computed']}, skipped {result['skipped']}\n"
    )
    return 0


def _cmd_prove(args: argparse.Namespace) -> int:
    root = _discover_root()
    cmd = list(args.cmd or [])
    # argparse.REMAINDER includes the literal "--" — strip the first one.
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        sys.stderr.write("gita: prove requires a command after '--'\n")
        return 2
    try:
        result = proofs_mod.record(root, args.name, cmd=cmd)
    except proofs_mod.DirtyTree as exc:
        sys.stderr.write(f"gita: {exc}\n")
        return 4
    glyph = proofs_mod.GLYPH_OK if result.ok else proofs_mod.GLYPH_FAIL
    sys.stdout.write(
        f"{glyph} {result.name}  (exit {result.exit_code}, {result.duration_ms} ms)\n"
    )
    return 0 if result.ok else result.exit_code


def _cmd_last_proven(args: argparse.Namespace) -> int:
    root = _discover_root()
    try:
        sha = proofs_mod.last_proven(root, args.name, symbol=args.symbol)
    except proofs_mod.NoProofs:
        sys.stderr.write(
            "gita: no proofs recorded; try 'gita prove <check> -- <cmd>'\n"
        )
        return 3
    if args.json:
        proof = proofs_mod.read(root, sha) or {}
        checks = proof.get("checks", {})
        if args.name is not None:
            entry = checks.get(args.name, {})
        else:
            # No specific check requested — surface the most recently ran one.
            entry = max(
                checks.values(),
                key=lambda c: c.get("ran_at", ""),
                default={},
            )
        payload = {
            "commit": sha,
            "check": args.name,
            "ok": entry.get("ok"),
            "ran_at": entry.get("ran_at"),
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return 0
    sys.stdout.write(f"{sha}\n")
    return 0


def _cmd_who(args: argparse.Namespace) -> int:
    root = _discover_root()
    rec = who_mod.describe(root, args.rev)
    if args.json:
        sys.stdout.write(
            json.dumps(rec.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        return 0
    ts = datetime.fromtimestamp(rec.timestamp, tz=timezone.utc).isoformat()
    sys.stdout.write(
        f"commit:  {rec.commit}\n"
        f"author:  {rec.author_name} <{rec.author_email}>  {ts}\n"
    )
    if rec.agent is not None:
        bits = []
        model = rec.agent.get("model")
        session = rec.agent.get("session")
        if model:
            bits.append(str(model))
        if session:
            bits.append(f"session={session}")
        extras = {
            k: v for k, v in rec.agent.items() if k not in {"model", "session"}
        }
        if extras:
            bits.append(
                " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
            )
        sys.stdout.write("agent:   " + "  ".join(bits) + "\n")
    sys.stdout.write(f"message: {rec.message}\n")
    return 0


def _cmd_commit_note(args: argparse.Namespace) -> int:
    root = _discover_root()
    pairs = args.sets or []
    if not pairs:
        sys.stderr.write("gita: commit-note requires at least one --set key=value\n")
        return 2
    parsed: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            sys.stderr.write(
                f"gita: --set expects key=value, got {raw!r}\n"
            )
            return 2
        k, v = raw.split("=", 1)
        if not k:
            sys.stderr.write(f"gita: --set expects key=value, got {raw!r}\n")
            return 2
        parsed[k] = v
    if gx.status(root):
        sys.stderr.write(
            "gita: working tree is dirty; commit or stash before writing a note\n"
        )
        return 4
    sha = gx.head_sha(root)
    existing = notes_mod.read(root, sha) or {}
    existing.update(parsed)
    notes_mod.write(root, sha, existing)
    sys.stdout.write(f"wrote note for {sha}: {sorted(parsed)}\n")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    root = _discover_root()
    try:
        rec = context_mod.build(
            root, args.name,
            rev=args.rev,
            budget=args.budget,
            log_limit=args.log_limit,
        )
    except lookup_mod.NotFound as exc:
        sys.stderr.write(f"gita: symbol not found: {exc.name}\n")
        return 2
    except lookup_mod.Ambiguous as exc:
        sys.stderr.write(
            f"gita: ambiguous symbol {exc.name!r}; candidates:\n  "
            + "\n  ".join(exc.candidates) + "\n"
        )
        return 2
    if args.json:
        sys.stdout.write(
            json.dumps(rec.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        return 0
    # Text mode — fixed section order, blank-line separators.
    out = sys.stdout
    sym = rec.symbol
    out.write(f"symbol: {sym.name}  ({sym.kind} at {sym.path}:{sym.line_start})\n")
    out.write(f"  {sym.signature}\n")
    if sym.body:
        for line in sym.body.splitlines():
            out.write(f"    {line}\n")
    out.write("\n")
    out.write("callers:\n")
    if not rec.callers:
        out.write("  (none)\n")
    for c in rec.callers:
        out.write(f"  {c['file']}:{c['line']}  {c['caller']}\n")
    out.write("\n")
    out.write("log:\n")
    if not rec.log:
        out.write("  (none)\n")
    for entry in rec.log:
        short = entry['sha'][:10]
        ops = ", ".join(o.get('op', '?') for o in entry.get('ops', []))
        out.write(f"  {short}  {entry['message']}  [{ops}]\n")
    out.write("\n")
    if rec.last_proven:
        out.write(f"last_proven: {rec.last_proven}\n")
    else:
        out.write("last_proven: (none)\n")
    if rec.dropped:
        out.write(f"dropped (budget): {', '.join(rec.dropped)}\n")
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from . import mcp
    mcp.serve_stdio()
    return 0


def _cmd_bisect_proven(args: argparse.Namespace) -> int:
    root = _discover_root()
    cmd = list(args.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    cmd_arg = cmd if cmd else None
    try:
        result = bisect_mod.run(
            root, args.name, cmd=cmd_arg, symbol=args.symbol, ref=args.ref,
        )
    except bisect_mod.NoBaseline:
        sys.stderr.write(
            f"gita: no baseline for {args.name!r}; "
            f"try 'gita prove {args.name} -- <cmd>' on a known-good commit\n"
        )
        return 3
    except proofs_mod.DirtyTree as exc:
        sys.stderr.write(f"gita: {exc}\n")
        return 4
    if args.json:
        sys.stdout.write(
            json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        return 0 if result.suspect is None else 1
    out = sys.stdout
    from_short = (result.from_sha or "")[:12]
    to_short = result.to_sha[:12]
    out.write(f"range:   {from_short}..{to_short}\n")
    if result.reason == "head_is_proven":
        out.write("HEAD is already the last proven commit; nothing to bisect.\n")
        return 0
    if result.reason == "head_passes":
        out.write("HEAD passes; no failing commit found in range.\n")
        return 0
    if result.reason == "gaps":
        out.write(f"unable to narrow: {len(result.missing)} commit(s) lack proofs:\n")
        for sha in result.missing:
            out.write(f"  {sha[:12]}\n")
        out.write("hint: rerun with '-- <cmd>' to fill the gaps automatically\n")
        return 1
    # first_failure
    out.write(f"suspect: {result.suspect}\n")
    if result.via_merge:
        out.write(f"  via merge {result.via_merge[:12]}\n")
    if result.ops:
        out.write("ops:\n")
        for op in result.ops:
            out.write(f"  [{op.get('path','?')}] {_render_history_op(op)}\n")
    if result.callers_of_changed_symbols:
        out.write("callers of touched symbols:\n")
        for c in result.callers_of_changed_symbols:
            out.write(
                f"  {c.get('symbol','?')}: {c.get('file','?')}:{c.get('line','?')} "
                f" in {c.get('caller','?')}\n"
            )
    out.write(f"hint: 'gita explain {result.suspect[:12]}' for the full manifest\n")
    return 1


# ---------------------------------------------------------------------------
# hooks / auto-prove
# ---------------------------------------------------------------------------


def _cmd_hooks_install(args: argparse.Namespace) -> int:
    root = _discover_root()
    path = hooks_mod.install(root)
    sys.stdout.write(f"installed post-commit hook: {path}\n")
    return 0


def _cmd_hooks_uninstall(args: argparse.Namespace) -> int:
    root = _discover_root()
    hooks_mod.uninstall(root)
    sys.stdout.write("uninstalled gita post-commit hook\n")
    return 0


def _cmd_hooks_status(args: argparse.Namespace) -> int:
    root = _discover_root()
    state = "installed" if hooks_mod.is_installed(root) else "not installed"
    sys.stdout.write(f"post-commit: {state}\n")
    return 0


def _cmd_auto_enable(args: argparse.Namespace) -> int:
    root = _discover_root()
    cmd = list(args.cmd or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        sys.stderr.write("gita: auto enable requires '-- <cmd>'\n")
        return 2
    proofs_mod.set_auto(root, args.name, cmd=cmd)
    sys.stdout.write(f"enabled auto-prove for {args.name!r}\n")
    return 0


def _cmd_auto_disable(args: argparse.Namespace) -> int:
    root = _discover_root()
    proofs_mod.disable_auto(root, args.name)
    sys.stdout.write(f"disabled auto-prove for {args.name!r}\n")
    return 0


def _cmd_auto_list(args: argparse.Namespace) -> int:
    root = _discover_root()
    checks = proofs_mod.list_auto(root)
    if args.json:
        sys.stdout.write(
            json.dumps({"checks": checks}, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n"
        )
        return 0
    if not checks:
        sys.stdout.write("(no auto-prove checks configured)\n")
        return 0
    for name, entry in checks.items():
        state = "on " if entry.get("enabled") else "off"
        cmd = " ".join(entry.get("cmd") or [])
        sys.stdout.write(f"  [{state}] {name}: {cmd}\n")
    return 0


def _cmd_auto_run(args: argparse.Namespace) -> int:
    root = _discover_root()
    results = proofs_mod.auto_prove_for_head(root)
    for r in results:
        glyph = proofs_mod.GLYPH_OK if r.ok else proofs_mod.GLYPH_FAIL
        sys.stdout.write(f"  {glyph} {r.name} (exit={r.exit_code}, {r.duration_ms}ms)\n")
    return 0


# Hidden — invoked by the post-commit hook. Always exits 0 so the hook can't
# break the commit; user-visible failures show up in 'gita auto list'.
def _cmd_auto_prove_hook(args: argparse.Namespace) -> int:
    try:
        root = gx.discover_root(Path.cwd())
    except FileNotFoundError:
        return 0
    try:
        proofs_mod.auto_prove_for_head(root)
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gita", description="Agentic version control on top of git.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="git init (if needed) + create .git/gita/")
    p_init.add_argument("path", nargs="?", default=".")
    p_init.set_defaults(func=_cmd_init)

    p_diff = sub.add_parser("diff", help="Structural manifest (HEAD vs working tree by default).")
    p_diff.add_argument("from_ref", nargs="?", default=None)
    p_diff.add_argument("to_ref", nargs="?", default=None)
    p_diff.add_argument("--staged", action="store_true", help="HEAD vs index.")
    p_diff.add_argument("--only", action="append", choices=list(_OP_CATEGORIES))
    p_diff.add_argument("--exclude", action="append", choices=list(_OP_CATEGORIES))
    p_diff.add_argument("--symbol", default=None, help="Keep only ops mentioning NAME.")
    p_diff.add_argument("--json", action="store_true")
    p_diff.set_defaults(func=_cmd_diff)

    p_status = sub.add_parser("status", help="Porcelain status + manifest summary.")
    p_status.add_argument("-v", "--verbose", action="store_true")
    p_status.set_defaults(func=_cmd_status)

    p_commit = sub.add_parser("commit", help="git commit + write manifest.")
    p_commit.add_argument("-m", "--message", required=True)
    p_commit.add_argument("--allow-empty", action="store_true")
    p_commit.set_defaults(func=_cmd_commit)

    p_explain = sub.add_parser("explain", help="Manifest stored with a commit.")
    p_explain.add_argument("ref")
    p_explain.add_argument("--only", action="append", choices=list(_OP_CATEGORIES))
    p_explain.add_argument("--exclude", action="append", choices=list(_OP_CATEGORIES))
    p_explain.add_argument("--json", action="store_true")
    p_explain.set_defaults(func=_cmd_explain)

    p_symlog = sub.add_parser("symbol-log", help="Commits touching a symbol.")
    p_symlog.add_argument("name")
    p_symlog.add_argument("-n", "--max-count", type=int, default=None)
    p_symlog.add_argument("--json", action="store_true")
    p_symlog.set_defaults(func=_cmd_symbol_log)

    p_callers = sub.add_parser("callers", help='Call sites of NAME ("name@ref" for non-HEAD).')
    p_callers.add_argument("target")
    p_callers.add_argument("--json", action="store_true")
    p_callers.set_defaults(func=_cmd_callers)

    p_get = sub.add_parser(
        "get", help='Source of NAME at a rev ("name@ref" for non-HEAD).'
    )
    p_get.add_argument("target")
    p_get.add_argument("--json", action="store_true")
    p_get.set_defaults(func=_cmd_get)

    p_reindex = sub.add_parser("reindex", help="Backfill stored manifests.")
    p_reindex.add_argument("--force", action="store_true")
    p_reindex.set_defaults(func=_cmd_reindex)

    p_prove = sub.add_parser(
        "prove", help="Run a check and record the result on HEAD."
    )
    p_prove.add_argument("name", help="Check name, e.g. 'pytest'.")
    p_prove.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run after '--'.",
    )
    p_prove.set_defaults(func=_cmd_prove)

    p_lastp = sub.add_parser(
        "last-proven", help="Print last commit where checks passed."
    )
    p_lastp.add_argument(
        "name", nargs="?", default=None,
        help="Optional check name; default = all recorded checks must pass.",
    )
    p_lastp.add_argument(
        "--symbol", default=None,
        help="Additionally require the named symbol to exist at the commit.",
    )
    p_lastp.add_argument("--json", action="store_true")
    p_lastp.set_defaults(func=_cmd_last_proven)

    p_who = sub.add_parser(
        "who", help="Author + optional agent identity for a commit."
    )
    p_who.add_argument("rev", nargs="?", default="HEAD")
    p_who.add_argument("--json", action="store_true")
    p_who.set_defaults(func=_cmd_who)

    p_note = sub.add_parser(
        "commit-note",
        help="Write a note (model, session, ...) for the current HEAD.",
    )
    p_note.add_argument(
        "--set", action="append", dest="sets", metavar="KEY=VALUE",
        help="Repeatable. e.g. --set model=claude --set session=abc.",
    )
    p_note.set_defaults(func=_cmd_commit_note)

    p_ctx = sub.add_parser(
        "context",
        help="Composite read for a symbol (callers + log + proof status).",
    )
    p_ctx.add_argument("name")
    p_ctx.add_argument("--rev", default="HEAD")
    p_ctx.add_argument(
        "--budget", type=int, default=None,
        help="Approximate max characters; drops sections oldest-log-first.",
    )
    p_ctx.add_argument(
        "--log-limit", type=int, default=10,
        help="Max log entries before budget trimming (default 10).",
    )
    p_ctx.add_argument("--json", action="store_true")
    p_ctx.set_defaults(func=_cmd_context)

    p_bisect = sub.add_parser(
        "bisect-proven",
        help="Narrow a regression in a check to its first failing commit.",
    )
    p_bisect.add_argument("name", help="Check name, e.g. 'pytest'.")
    p_bisect.add_argument(
        "--symbol", default=None,
        help="Filter reported ops to those touching SYMBOL.",
    )
    p_bisect.add_argument(
        "--ref", default="HEAD",
        help="Endpoint of the bisect range (default HEAD).",
    )
    p_bisect.add_argument("--json", action="store_true")
    p_bisect.add_argument(
        "cmd", nargs=argparse.REMAINDER,
        help="Optional command after '--' to fill missing proofs.",
    )
    p_bisect.set_defaults(func=_cmd_bisect_proven)

    p_hooks = sub.add_parser("hooks", help="Manage the gita post-commit hook.")
    hooks_sub = p_hooks.add_subparsers(dest="hooks_cmd", required=True)
    hi = hooks_sub.add_parser("install", help="Install the auto-prove post-commit hook.")
    hi.set_defaults(func=_cmd_hooks_install)
    hu = hooks_sub.add_parser("uninstall", help="Remove the gita post-commit hook block.")
    hu.set_defaults(func=_cmd_hooks_uninstall)
    hs = hooks_sub.add_parser("status", help="Show whether the hook is installed.")
    hs.set_defaults(func=_cmd_hooks_status)

    p_auto = sub.add_parser(
        "auto", help="Configure checks the post-commit hook re-runs on every HEAD."
    )
    auto_sub = p_auto.add_subparsers(dest="auto_cmd", required=True)
    a_en = auto_sub.add_parser("enable", help="Enable a check: 'gita auto enable NAME -- cmd ...'")
    a_en.add_argument("name")
    a_en.add_argument("cmd", nargs=argparse.REMAINDER)
    a_en.set_defaults(func=_cmd_auto_enable)
    a_dis = auto_sub.add_parser("disable", help="Disable a configured check.")
    a_dis.add_argument("name")
    a_dis.set_defaults(func=_cmd_auto_disable)
    a_ls = auto_sub.add_parser("list", help="List configured auto-prove checks.")
    a_ls.add_argument("--json", action="store_true")
    a_ls.set_defaults(func=_cmd_auto_list)
    a_run = auto_sub.add_parser("run", help="Run all enabled checks against HEAD now.")
    a_run.set_defaults(func=_cmd_auto_run)

    p_hook_internal = sub.add_parser(
        "_auto-prove-hook",
        help=argparse.SUPPRESS,
    )
    p_hook_internal.set_defaults(func=_cmd_auto_prove_hook)

    p_mcp = sub.add_parser("mcp", help="Run as an MCP server over stdio.")
    p_mcp.set_defaults(func=_cmd_mcp)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
