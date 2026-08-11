"""The gita command line.

Conventional verbs are canonical; Gita-inspired names are first-class aliases
(see docs/SCOPE.md section 10).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from ..context import (
    build_view,
    count_tokens,
    entity_diff,
    expand,
    focus,
    focus_label,
)
from ..context.answer import DEFAULT_BUDGET, compose, material_patch
from ..context.layers import fit_text
from ..context.resolve import Ambiguous, resolve_entity
from ..revisions import diff_revisions
from ..telemetry import record, timed
from ..vcs.git import GitError, Repo
from . import render

ALIASES = {
    "darshan": "diff",      # beholding -- seeing what truly changed
    "shloka": "show",       # the verse itself -- exact text
    "vistaar": "expand",    # elaboration
    "katha": "history",     # the story -- how it came to be
    "sarathi": "serve",     # charioteer -- guides the one who acts
}

USAGE = """\
gita - context diffs for agent coders

  gita diff <base> <head>     what changed: summary, files, functions and code
  gita diff <base> <head> --interface-only   only changes that can break a caller
  gita diff <base> <head> --patch            plain unified diff, noise removed
  gita history <entity>       how one function changed over time
  gita show <entity>          exact hunks for one entity
  gita savings                cost versus a raw git diff

One `gita diff` should answer the question; it says so when output was cut.
Run `gita <command> --help` for details."""

DEFAULT_BUDGET = DEFAULT_BUDGET


def _parser() -> argparse.ArgumentParser:
    # Global flags are accepted on either side of the subcommand; SUPPRESS keeps
    # an unused subcommand copy from clobbering the top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--repo", metavar="PATH", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", dest="as_json",
                        default=argparse.SUPPRESS)
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(
        prog="gita",
        description="context diffs for agent coders — what changed, not which bytes",
    )
    parser.add_argument("-C", "--repo", default=".", metavar="PATH",
                        help="repository to inspect (default: current directory)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit machine-readable output")
    parser.add_argument("--no-color", action="store_true", help="disable colour")

    sub = parser.add_subparsers(dest="command")

    def revisions(sp, worktree_default: bool = False):
        # `gita diff` must mean what `git diff` means. Defaulting to HEAD^..HEAD
        # silently showed the last commit when the agent asked about uncommitted
        # work -- one evaluation task spent 12 turns on that.
        sp.add_argument("base", nargs="?", default="HEAD" if worktree_default else "HEAD^")
        sp.add_argument("head", nargs="?",
                        default=None if worktree_default else "HEAD")

    def budget(sp):
        sp.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help=f"token budget (default: {DEFAULT_BUDGET})")

    diff = sub.add_parser("diff", aliases=["darshan"], parents=[common],
                          help="context diff between two revisions")
    revisions(diff, worktree_default=True)
    budget(diff)
    diff.add_argument("--filter", default="", metavar="TERM",
                      help="only entities whose name or path contains TERM")
    diff.add_argument("--interface-only", action="store_true",
                      help="only changes that can break a caller")
    diff.add_argument("--brief", action="store_true",
                      help="summary only, without the code")
    diff.add_argument("--patch", action="store_true",
                      help="unified diff with noise removed")

    show = sub.add_parser("show", aliases=["shloka"], parents=[common],
                          help="exact hunks for one entity")
    show.add_argument("entity")
    revisions(show)

    exp = sub.add_parser("expand", aliases=["vistaar"], parents=[common],
                         help="drill into a rolled-up entity")
    exp.add_argument("entity")
    revisions(exp)
    budget(exp)

    savings = sub.add_parser("savings", parents=[common],
                             help="token cost versus a raw git diff")
    revisions(savings)
    budget(savings)

    hist = sub.add_parser("history", aliases=["katha"], parents=[common],
                          help="how entities changed across a range of commits")
    hist.add_argument("entity", nargs="?",
                      help="limit to one entity; omit for every commit")
    hist.add_argument("--since", default=None)
    hist.add_argument("--until", default="HEAD")
    hist.add_argument("--limit", type=int, default=20)
    hist.add_argument("--brief", action="store_true",
                      help="commits only, without the code")
    budget(hist)

    serve = sub.add_parser("serve", aliases=["sarathi"], parents=[common],
                           help="run the MCP server for agents")
    serve.add_argument("--transport", default="stdio", choices=["stdio"])

    return parser


def _resolve(repo: Repo, *revs: str) -> None:
    """Fail fast with a readable message rather than an empty diff."""
    for rev in revs:
        if not repo.resolve(rev):
            raise GitError(f"unknown revision: {rev}")


def _emit(out: TextIO, payload: dict, text: str, as_json: bool) -> None:
    render.write(out, json.dumps(payload, indent=2) if as_json else text)


class _Tee:
    """Forwards output while measuring it -- what we emit is what an agent pays."""

    def __init__(self, inner):
        self._inner = inner
        self._chunks: list[str] = []

    def write(self, text: str) -> int:
        self._chunks.append(text)
        return self._inner.write(text)

    def flush(self) -> None:
        self._inner.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._inner, "isatty", lambda: False)())

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    parser = _parser()

    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_code:
        if exit_code.code not in (0, None):
            render.write(out, parser.format_usage())
        return int(exit_code.code or 2)

    if not args.command:
        # Full argparse help is expensive for an agent to read; point at the one
        # command that answers the question.
        render.write(out, USAGE)
        return 2

    command = ALIASES.get(args.command, args.command)
    colour = (not args.no_color) and render.colour_enabled(out)
    repo = Repo(args.repo)
    tee = _Tee(out)

    with timed() as elapsed:
        code = _dispatch(command, tee, repo, args, colour, parser)

    record({
        "arm": "gita",
        "tool": command,
        "repo": str(args.repo),
        "output_tokens": count_tokens(tee.text),
        "latency_ms": elapsed.ms,
        "ok": code == 0,
        "budget": getattr(args, "budget", None),
    })
    return code


def _dispatch(command, out, repo, args, colour, parser) -> int:
    try:
        if command == "serve":
            from ..mcp.server import serve

            return serve(repo_path=args.repo)

        if command == "history":
            return _cmd_history(out, repo, args, colour)

        _resolve(repo, args.base, args.head)

        if command == "diff":
            return _cmd_diff(out, repo, args, colour)
        if command == "show":
            return _cmd_show(out, repo, args, colour)
        if command == "expand":
            return _cmd_expand(out, repo, args, colour)
        if command == "savings":
            return _cmd_savings(out, repo, args, colour)

    except GitError as error:
        render.write(out, f"gita: {error}")
        return 3
    except KeyboardInterrupt:  # pragma: no cover
        return 130

    render.write(out, parser.format_usage())
    return 2


def _cmd_diff(out, repo, args, colour) -> int:
    changeset = diff_revisions(repo, args.base, args.head)
    selected = focus(changeset, args.filter, args.interface_only)

    if args.patch:
        patch = material_patch(repo, args.base, args.head, selected,
                               budget=args.budget)
        _emit(out, {"patch": patch, "tokens": count_tokens(patch)},
              render.render_patch(patch, colour) or "no material changes",
              args.as_json)
        return 0

    # One call should answer the question: a follow-up costs a whole turn of
    # re-sent context, which dwarfs anything saved by withholding detail.
    result = compose(repo, args.base, args.head, selected,
                     budget=args.budget, detail=not args.brief)

    payload = {
        "base": args.base,
        "head": args.head,
        "text": result.text,
        "tokens": result.tokens,
        "budget": result.budget,
        "truncated": result.truncated,
        "detailed": result.detailed,
        "filter": args.filter,
        "interface_only": args.interface_only,
        "files_changed": selected.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
        "changes": [render.change_payload(c) for c in selected.material()],
    }
    _emit(out, payload, render.render_answer(result.text, colour), args.as_json)
    return 0


def _fallback_entity(changeset, entity: str, out, as_json: bool):
    """Resolve a bare name only after the literal argument has failed.

    Resolving first is harmful: `app.py::Store` is a valid container prefix, and
    eagerly matching it to `app.py::Store::get` answers a question nobody asked.
    """
    try:
        return resolve_entity([c.entity.id for c in changeset.material()], entity)
    except Ambiguous as error:
        _emit(out, {"entity": entity, "matches": error.matches,
                    "error": "ambiguous"}, f"gita: {error}", as_json)
        return False


def _cmd_show(out, repo, args, colour) -> int:
    patch = entity_diff(repo, args.base, args.head, args.entity)
    entity = args.entity

    if not patch:
        changeset = diff_revisions(repo, args.base, args.head)
        resolved = _fallback_entity(changeset, args.entity, out, args.as_json)
        if resolved is False:
            return 5
        if resolved:
            entity = resolved
            patch = entity_diff(repo, args.base, args.head, entity)

    if not patch:
        message = f"gita: entity not found in either revision: {args.entity}"
        _emit(out, {"entity": args.entity, "patch": "", "error": "not found"},
              message, args.as_json)
        return 4

    _emit(out,
          {"entity": entity, "patch": patch, "tokens": count_tokens(patch)},
          render.render_patch(patch, colour),
          args.as_json)
    return 0


def _cmd_expand(out, repo, args, colour) -> int:
    changeset = diff_revisions(repo, args.base, args.head)
    entity = args.entity
    lines = expand(changeset, entity, budget=args.budget)

    if not lines:
        resolved = _fallback_entity(changeset, args.entity, out, args.as_json)
        if resolved is False:
            return 5
        if resolved and resolved != entity:
            entity = resolved
            lines = expand(changeset, entity, budget=args.budget)

    if not lines:
        message = f"gita: no nested changes under {args.entity}"
        _emit(out, {"entity": args.entity, "lines": [], "error": "not found"},
              message, args.as_json)
        return 4

    _emit(out,
          {"entity": entity, "lines": lines,
           "tokens": count_tokens("\n".join(lines))},
          render.render_lines(lines, colour),
          args.as_json)
    return 0


def _cmd_history(out, repo, args, colour) -> int:
    from ..history import entity_history, series

    if args.entity:
        events = entity_history(repo, args.entity, since=args.since,
                                until=args.until, limit=args.limit)
        if not events:
            _emit(out, {"entity": args.entity, "events": [], "error": "no history"},
                  f"gita: no recorded changes to {args.entity}", args.as_json)
            return 4

        # The budget bounds what lands in the agent's context, and `write` appends
        # a line terminator that the agent also pays for.
        header = fit_text("\n".join(str(event) for event in events),
                          max(0, args.budget - 1))
        lines = [header] if header else []
        spent = count_tokens(header) + 1
        payload_events = []

        for event in events:
            entry = {"sha": event.sha, "subject": event.subject,
                     "date": event.date, "kind": event.kind.value}
            if not args.brief:
                patch = entity_diff(repo, f"{event.sha}^", event.sha,
                                    event.entity_id)
                cost = count_tokens(patch)
                if patch and spent + cost <= args.budget:
                    lines.append(f"\n--- {event.short} {event.entity_id}\n"
                                 f"{patch.rstrip()}")
                    entry["patch"] = patch
                    spent += cost
            payload_events.append(entry)

        _emit(out, {"entity": args.entity, "events": payload_events},
              render.render_answer("\n".join(lines), colour), args.as_json)
        return 0

    summaries = series(repo, since=args.since, until=args.until, limit=args.limit)
    payload = {
        "commits": [{"sha": s.sha, "subject": s.subject, "date": s.date,
                     "changes": [c.entity.id for c in s.material()]}
                    for s in summaries],
    }
    lines = []
    for summary in summaries:
        names = ", ".join(c.entity.qualname for c in summary.material()[:4]) or "-"
        lines.append(f"{summary.short}  {summary.date[:10]}  "
                     f"{summary.subject[:44]:<44}  {names}")
    _emit(out, payload, "\n".join(lines), args.as_json)
    return 0


def _cmd_savings(out, repo, args, colour) -> int:
    changeset = diff_revisions(repo, args.base, args.head)
    view = build_view(changeset, budget=args.budget)
    raw = repo.raw_diff(args.base, args.head, changeset.paths())
    raw_tokens = count_tokens(raw)

    payload = {
        "base": args.base,
        "head": args.head,
        "raw_tokens": raw_tokens,
        "l0_tokens": count_tokens(view.l0),
        "l1_tokens": view.tokens,
        "reduction": (1 - view.tokens / raw_tokens) if raw_tokens else 0.0,
        "files_changed": changeset.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
    }
    _emit(out, payload, render.render_savings(raw, view, colour), args.as_json)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
