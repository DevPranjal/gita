"""Minimal CLI surface for gitpp."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import libcst as cst

from .manifest import render_manifest
from .merge import merge_modules
from .repo import Repo


# ---------------------------------------------------------------------------
# file-based merge (no repo required) — useful for the scenario harness
# ---------------------------------------------------------------------------


def _cmd_merge_files(args: argparse.Namespace) -> int:
    base = cst.parse_module(Path(args.base).read_text(encoding="utf-8"))
    ours = cst.parse_module(Path(args.ours).read_text(encoding="utf-8"))
    theirs = cst.parse_module(Path(args.theirs).read_text(encoding="utf-8"))

    merged, conflicts = merge_modules(base, ours, theirs)

    if args.out:
        Path(args.out).write_text(merged.code, encoding="utf-8")
    else:
        sys.stdout.write(merged.code)

    if conflicts:
        sys.stderr.write(f"gitpp: {len(conflicts)} conflict(s):\n")
        for c in conflicts:
            sys.stderr.write(f"  [{c.kind}] {c.key}: {c.detail}\n")
        return 1
    return 0


# ---------------------------------------------------------------------------
# repo commands
# ---------------------------------------------------------------------------


def _cmd_init(args: argparse.Namespace) -> int:
    repo = Repo.init(Path(args.path))
    sys.stdout.write(f"Initialized empty gitpp repository in {repo.gitpp}\n")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    repo = Repo.discover(Path.cwd())
    for p in args.paths:
        sha = repo.add(Path(p))
        sys.stdout.write(f"added {p}  {sha[:12]}\n")
    return 0


def _cmd_commit(args: argparse.Namespace) -> int:
    repo = Repo.discover(Path.cwd())
    sha = repo.commit(args.message)
    sys.stdout.write(f"[{repo.current_branch()} {sha[:12]}] {args.message}\n")
    return 0


def _cmd_log(args: argparse.Namespace) -> int:
    repo = Repo.discover(Path.cwd())
    for entry in repo.log():
        when = datetime.fromtimestamp(entry["timestamp"], tz=timezone.utc).isoformat()
        sys.stdout.write(f"commit {entry['sha']}\n")
        if entry["parents"]:
            sys.stdout.write(f"  parents: {', '.join(p[:12] for p in entry['parents'])}\n")
        sys.stdout.write(f"  date:    {when}\n")
        sys.stdout.write(f"  tree:    {entry['tree'][:12]}\n\n")
        sys.stdout.write(f"    {entry['message']}\n\n")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    repo = Repo.discover(Path.cwd())
    result = repo.merge(args.ref, message=args.message)
    if result.status == "up-to-date":
        sys.stdout.write("Already up to date.\n")
        return 0
    if result.status == "fast-forward":
        sys.stdout.write(f"Fast-forward to {result.commit[:12]}\n")
        return 0
    if result.status == "merged":
        sys.stdout.write(f"Merge made by gitpp: {result.commit[:12]}\n")
        return 0
    sys.stderr.write("Merge conflict(s):\n")
    for path, conflicts in result.conflicts:
        sys.stderr.write(f"  {path}:\n")
        for c in conflicts:
            sys.stderr.write(f"    [{c.kind}] {c.key}: {c.detail}\n")
    return 1


def _cmd_diff(args: argparse.Namespace) -> int:
    """Show the structural manifest between two commits (or HEAD vs index)."""
    repo = Repo.discover(Path.cwd())
    from_sha = repo.resolve_ref(args.from_ref) if args.from_ref else repo.head_commit()
    to_sha = repo.resolve_ref(args.to_ref) if args.to_ref else None
    manifest = repo.diff_commits(from_sha, to_sha)
    manifest = _filter_manifest(manifest, only=args.only, exclude=args.exclude)
    if args.json:
        sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    else:
        sys.stdout.write(render_manifest(manifest))
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    """Read back the manifest persisted with a commit."""
    repo = Repo.discover(Path.cwd())
    sha = repo.resolve_ref(args.ref)
    manifest = repo.read_manifest(sha)
    if manifest is None:
        # Older commit without a stored manifest: recompute on demand.
        c_obj = repo._tree_of  # noqa: F841  (kept for clarity that we touch repo state)
        from . import objects as obj
        c = obj.read_object(repo.root, sha)
        parent = c["parents"][0] if c["parents"] else None
        manifest = repo.diff_commits(parent, sha)
    manifest = _filter_manifest(manifest, only=args.only, exclude=args.exclude)
    if args.json:
        sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    else:
        sys.stdout.write(f"commit {sha}\n")
        sys.stdout.write(render_manifest(manifest))
    return 0


_OP_CATEGORIES = {
    "logic": {"modify_body", "add_symbol", "remove_symbol"},
    "signature": {"rename_symbol", "modify_signature", "add_import", "remove_import"},
    "cosmetic": {"reorder_imports", "format_only"},
}


def _cmd_symbol_log(args: argparse.Namespace) -> int:
    """Show every commit whose manifest mentions a symbol."""
    repo = Repo.discover(Path.cwd())
    entries = repo.symbol_log(args.name)
    if args.json:
        sys.stdout.write(json.dumps(entries, indent=2) + "\n")
        return 0
    if not entries:
        sys.stdout.write(f"no commits touch symbol {args.name!r}\n")
        return 0
    for entry in entries:
        short = entry["sha"][:12]
        sys.stdout.write(f"{short}  {entry['message']}\n")
        for op in entry["ops"]:
            sys.stdout.write(f"    [{op['path']}] {_render_history_op(op)}\n")
    return 0


def _cmd_callers(args: argparse.Namespace) -> int:
    """List call sites of a symbol in a given tree (default HEAD)."""
    repo = Repo.discover(Path.cwd())
    name, ref = _split_at_ref(args.target)
    hits = repo.find_callers(name, ref)
    if args.json:
        sys.stdout.write(json.dumps({"name": name, "ref": ref, "callers": hits}, indent=2) + "\n")
        return 0
    if not hits:
        sys.stdout.write(f"no callers of {name!r}\n")
        return 0
    sys.stdout.write(f"{len(hits)} call site(s) of {name!r}:\n")
    for h in hits:
        sys.stdout.write(f"  {h['file']}: {h['caller']}\n")
    return 0


def _split_at_ref(target: str) -> tuple[str, str | None]:
    """``"name@ref"`` → ``("name", "ref")``; bare ``"name"`` → ``("name", None)``."""
    if "@" in target:
        name, ref = target.split("@", 1)
        return name, ref or None
    return target, None


def _render_history_op(op: dict) -> str:
    """One-line label for a symbol-touching op (compact for terminal output)."""
    kind = op["op"]
    if kind == "rename_symbol":
        return f"rename {op['from']} → {op['to']}"
    if kind in ("add_symbol", "remove_symbol"):
        verb = "add" if kind == "add_symbol" else "remove"
        return f"{verb} {op.get('kind','symbol')} {op['name']}"
    if kind == "modify_body":
        return f"body {op['name']}: +{op.get('lines_added',0)}/-{op.get('lines_removed',0)}"
    if kind == "modify_signature":
        return f"signature {op['name']}: {op.get('old_signature','')} → {op.get('new_signature','')}"
    return f"{kind} {op.get('name','')}"


def _filter_manifest(
    manifest: dict, *, only: list[str] | None, exclude: list[str] | None
) -> dict:
    """Drop ops whose category isn't selected. Pure transform; returns a new dict."""
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


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gitpp", description="Agentic version control (MVP).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create an empty gitpp repository.")
    p_init.add_argument("path", nargs="?", default=".", help="Directory (default: cwd).")
    p_init.set_defaults(func=_cmd_init)

    p_add = sub.add_parser("add", help="Stage Python file(s) into the index.")
    p_add.add_argument("paths", nargs="+")
    p_add.set_defaults(func=_cmd_add)

    p_commit = sub.add_parser("commit", help="Record a new commit from the index.")
    p_commit.add_argument("-m", "--message", required=True)
    p_commit.set_defaults(func=_cmd_commit)

    p_log = sub.add_parser("log", help="Show commit history (first-parent only in v0.0).")
    p_log.set_defaults(func=_cmd_log)

    p_merge = sub.add_parser("merge", help="3-way semantic merge of another ref into HEAD.")
    p_merge.add_argument("ref", help="Branch name or commit sha to merge.")
    p_merge.add_argument("-m", "--message", help="Merge commit message.")
    p_merge.set_defaults(func=_cmd_merge)

    p_diff = sub.add_parser(
        "diff",
        help="Show structural change manifest (the agent-facing diff).",
    )
    p_diff.add_argument(
        "from_ref", nargs="?", default=None,
        help="From commit/branch (default: HEAD).",
    )
    p_diff.add_argument(
        "to_ref", nargs="?", default=None,
        help="To commit/branch (default: current index / working tree).",
    )
    p_diff.add_argument(
        "--only", action="append", choices=list(_OP_CATEGORIES),
        help="Restrict to op categories (logic/signature/cosmetic). Repeatable.",
    )
    p_diff.add_argument(
        "--exclude", action="append", choices=list(_OP_CATEGORIES),
        help="Drop op categories. Repeatable.",
    )
    p_diff.add_argument("--json", action="store_true", help="Emit raw JSON manifest.")
    p_diff.set_defaults(func=_cmd_diff)

    p_explain = sub.add_parser(
        "explain",
        help="Show the manifest stored with a commit (compact, ~200 tokens).",
    )
    p_explain.add_argument("ref", help="Commit sha or branch name.")
    p_explain.add_argument(
        "--only", action="append", choices=list(_OP_CATEGORIES),
        help="Restrict to op categories. Repeatable.",
    )
    p_explain.add_argument(
        "--exclude", action="append", choices=list(_OP_CATEGORIES),
        help="Drop op categories. Repeatable.",
    )
    p_explain.add_argument("--json", action="store_true", help="Emit raw JSON manifest.")
    p_explain.set_defaults(func=_cmd_explain)

    p_mf = sub.add_parser(
        "merge-files",
        help="One-shot file-based merge (no repo required). Useful for testing.",
    )
    p_mf.add_argument("--base", required=True)
    p_mf.add_argument("--ours", required=True)
    p_mf.add_argument("--theirs", required=True)
    p_mf.add_argument("--out", help="Write merged output here instead of stdout.")
    p_mf.set_defaults(func=_cmd_merge_files)

    p_symlog = sub.add_parser(
        "symbol-log",
        help="Commits whose stored manifest touches a named symbol.",
    )
    p_symlog.add_argument("name", help="Symbol name (function or class).")
    p_symlog.add_argument("--json", action="store_true", help="Emit raw JSON.")
    p_symlog.set_defaults(func=_cmd_symbol_log)

    p_callers = sub.add_parser(
        "callers",
        help="Call sites of NAME in the tree at REF (NAME[@REF], default HEAD).",
    )
    p_callers.add_argument("target", help='Symbol or "name@ref".')
    p_callers.add_argument("--json", action="store_true", help="Emit raw JSON.")
    p_callers.set_defaults(func=_cmd_callers)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
