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

from gitpp.manifest import render_manifest

from . import callers as callers_mod
from . import git as gx
from . import history as history_mod
from . import store
from .diff import build_for_commit, build_for_refs, build_for_working_tree


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_OP_CATEGORIES = {
    "logic": {"modify_body", "add_symbol", "remove_symbol"},
    "signature": {"rename_symbol", "modify_signature", "add_import", "remove_import"},
    "cosmetic": {"reorder_imports", "format_only"},
}


def _filter_manifest(manifest: dict, *, only: list[str] | None, exclude: list[str] | None) -> dict:
    if not only and not exclude:
        return manifest
    allowed = set().union(*(_OP_CATEGORIES[c] for c in (only or _OP_CATEGORIES)))
    if exclude:
        for c in exclude:
            allowed -= _OP_CATEGORIES[c]
    new_files = []
    for fe in manifest["files"]:
        kept = [op for op in fe["ops"] if op["op"] in allowed]
        if kept or fe["status"] != "modified":
            new_files.append({**fe, "ops": kept})
    return {**manifest, "files": new_files}


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
    manifest = _filter_manifest(manifest, only=args.only, exclude=args.exclude)
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
    if args.json:
        sys.stdout.write(json.dumps({"commit": sha, **manifest}, indent=2) + "\n")
    else:
        meta = gx.commit_meta(root, sha)
        when = datetime.fromtimestamp(meta.timestamp, tz=timezone.utc).isoformat()
        sys.stdout.write(f"commit {sha}\n")
        sys.stdout.write(f"  author: {meta.author_name} <{meta.author_email}>\n")
        sys.stdout.write(f"  date:   {when}\n\n")
        sys.stdout.write(f"    {meta.message.splitlines()[0] if meta.message else ''}\n\n")
        sys.stdout.write(render_manifest(manifest))
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
        sys.stdout.write(f"{entry['sha'][:12]}  {entry['message'].splitlines()[0]}\n")
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


def _cmd_reindex(args: argparse.Namespace) -> int:
    root = _discover_root()
    result = history_mod.reindex(root, force=args.force)
    sys.stdout.write(
        f"reindex: computed {result['computed']}, skipped {result['skipped']}\n"
    )
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    from . import mcp
    mcp.serve_stdio()
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

    p_reindex = sub.add_parser("reindex", help="Backfill stored manifests.")
    p_reindex.add_argument("--force", action="store_true")
    p_reindex.set_defaults(func=_cmd_reindex)

    p_mcp = sub.add_parser("mcp", help="Run as an MCP server over stdio.")
    p_mcp.set_defaults(func=_cmd_mcp)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
