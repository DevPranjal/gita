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
from ..context.rank import Intent
from ..context.resolve import Ambiguous, resolve_entity
from ..revisions import diff_revisions
from ..telemetry import annotate, record, timed
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
    gita history <base> <head>  entity changes throughout a commit range
  gita show <entity>          exact hunks for one entity

One `gita diff` should answer the question; it says so when output was cut.
Run `gita <command> --help` for details."""

DEFAULT_BUDGET = DEFAULT_BUDGET


class _IndexedUsageError(ValueError):
    pass


class _IndexedParser(argparse.ArgumentParser):
    def error(self, message):
        raise _IndexedUsageError(message)


def _parser(indexed_errors: bool = False) -> argparse.ArgumentParser:
    # Global flags are accepted on either side of the subcommand; SUPPRESS keeps
    # an unused subcommand copy from clobbering the top-level value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-C", "--repo", metavar="PATH", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", dest="as_json",
                        default=argparse.SUPPRESS)
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)

    parser_type = _IndexedParser if indexed_errors else argparse.ArgumentParser
    parser = parser_type(
        prog="gita",
        description="context diffs for agent coders — what changed, not which bytes",
    )
    parser.add_argument("-C", "--repo", default=".", metavar="PATH",
                        help="repository to inspect (default: current directory)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit machine-readable output")
    parser.add_argument("--no-color", action="store_true", help="disable colour")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help=f"token budget (default: {DEFAULT_BUDGET})")
    # git's own global flag. It means nothing here, but agents type it out of
    # habit and a rejected guess costs a whole turn.
    parser.add_argument("--no-pager", action="store_true", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")

    def revisions(sp, worktree_default: bool = False):
        # `gita diff` must mean what `git diff` means. Defaulting to HEAD^..HEAD
        # silently showed the last commit when the agent asked about uncommitted
        # work -- one evaluation task spent 12 turns on that.
        sp.add_argument("base", nargs="?", default="HEAD" if worktree_default else "HEAD^")
        sp.add_argument("head", nargs="?",
                        default=None if worktree_default else "HEAD")
        # Agents reached for these 12 times; they mean the positionals.
        sp.add_argument("--base", dest="base_flag", default=None,
                        help=argparse.SUPPRESS)
        sp.add_argument("--head", dest="head_flag", default=None,
                        help=argparse.SUPPRESS)

    def budget(sp):
        sp.add_argument("--budget", type=int, default=argparse.SUPPRESS,
                        help=f"token budget (default: {DEFAULT_BUDGET})")

    def indexed_query(sp):
        sp.add_argument("--scope", default=None)
        sp.add_argument("--at", default=None, metavar="COMMITISH")
        sp.add_argument("--generation", default=None)
        sp.add_argument("--cursor", default=None)

    index = sub.add_parser("index", parents=[common],
                           help="build or inspect a committed entity index")
    actions = index.add_mutually_exclusive_group()
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--verify", action="store_true")
    actions.add_argument("--gc", action="store_true")
    index.add_argument("--scope", default=None)
    index.add_argument("--ref", default=None, metavar="COMMITISH")
    index.add_argument("--since", default=None, metavar="COMMITISH")
    index.add_argument("--rebuild", action="store_true")
    index.add_argument("--workers", type=int, default=None, metavar="N",
                       help="parallel parse workers, 1-32 (default: up to 4 CPUs)")
    index.add_argument("--parse-timeout", type=float, default=None, metavar="SECONDS",
                       help="per-file worker deadline (default: 5 seconds)")
    index.add_argument("--generation", default=None)
    index.add_argument("--source-check", action="store_true")
    budget(index)

    identity = sub.add_parser("id", parents=[common],
                              help="resolve a persistent indexed entity identity")
    identity.add_argument("entity")
    indexed_query(identity)
    identity.add_argument("--historical", action="store_true")
    identity.add_argument("--limit", type=int, default=20)
    budget(identity)

    diff = sub.add_parser("diff", aliases=["darshan"], parents=[common],
                          help="context diff between two revisions")
    revisions(diff, worktree_default=True)
    budget(diff)
    diff.add_argument("--filter", default="", metavar="TERM",
                      help="only entities whose name or path contains TERM")
    # Filtering answers "show me only this". Intent answers "I am here to do
    # this" -- nothing is hidden, the relevant entities just come first.
    diff.add_argument("--intent", default="", metavar="TEXT",
                      help="what you are trying to do; ranks matching entities "
                           "first without hiding the rest")
    diff.add_argument("--interface-only", action="store_true",
                      help="only changes that can break a caller")
    # `--stat` and `--oneline` are git's words for "less detail".
    diff.add_argument("--brief", "--stat", "--oneline", action="store_true",
                      help="summary only, without the code")
    diff.add_argument("--patch", action="store_true",
                      help="unified diff with noise removed")
    # The indexed engine decides identity once, strictly, at build time rather
    # than re-guessing it from similarity on every call. It reads a published
    # generation and never builds on demand, so this is opt-in: it needs
    # `gita index` first and cannot see uncommitted work.
    diff.add_argument("--indexed", action="store_true",
                      help="compare using the committed entity index instead of "
                           "re-deriving entities (requires gita index)")
    diff.add_argument("--scope", default=None,
                      help="named index scope for --indexed (default: default)")
    diff.add_argument("--generation", default=None,
                      help="pin --indexed to one published generation")

    show = sub.add_parser("show", aliases=["shloka"], parents=[common],
                          help="exact hunks for one entity")
    show.add_argument("entity")
    revisions(show)

    exp = sub.add_parser("expand", aliases=["vistaar"], parents=[common],
                         help="drill into a rolled-up entity")
    exp.add_argument("entity")
    revisions(exp)
    budget(exp)
    exp.add_argument("--intent", default="", metavar="TEXT",
                     help="what you are trying to do; ranks matching entities first")

    hist = sub.add_parser("history", aliases=["katha"], parents=[common],
                          help="how entities changed across a range of commits")
    hist.add_argument("entity", nargs="?",
                      help="entity name, or the first of two commits; one token always means an entity")
    # Every other command takes revisions positionally. Requiring --since here
    # cost two turns per run: the agent tried `gita history <name> <rev>`, read
    # the help, then retried.
    hist.add_argument("base", nargs="?", default=None,
                      help="range end when preceded by a commit, otherwise the entity's oldest revision")
    hist.add_argument("head", nargs="?", default=None,
                      help="newest revision to look at (default HEAD)")
    hist.add_argument("--since", default=None)
    hist.add_argument("--until", default=None)
    hist.add_argument("--limit", type=int, default=20)
    hist.add_argument("--brief", "--stat", "--oneline", action="store_true",
                      help="history summaries only, without code patches")
    hist.add_argument("--indexed", action="store_true",
                      help="query the published committed entity index")
    indexed_query(hist)
    hist.add_argument("--order", choices=("newest", "oldest"), default=None)
    budget(hist)

    serve = sub.add_parser("serve", aliases=["sarathi"], parents=[common],
                           help="run the MCP server for agents")
    serve.add_argument("--transport", default="stdio", choices=["stdio"])

    # Keyed by every name and alias, so an unknown flag can be reported against
    # the subcommand the agent actually used rather than the whole command list.
    parser.subcommands = {name: sp for name, sp in sub.choices.items()}

    return parser


def _resolve(repo: Repo, *revs: str) -> None:
    """Fail fast with a readable message rather than an empty diff."""
    for rev in revs:
        if not repo.resolve(rev):
            raise GitError(f"unknown revision: {rev}")


def _normalize_diff_revisions(repo: Repo, args) -> None:
    """Expand Git's ``LEFT...RIGHT`` diff shorthand to its merge-base form."""
    if args.head is not None or args.base.count("...") != 1:
        return
    left, right = args.base.split("...")
    left = left or "HEAD"
    right = right or "HEAD"
    _resolve(repo, left, right)
    args.base = repo.merge_base(left, right)
    args.head = right


def _emit(out: TextIO, payload: dict, text: str, as_json: bool) -> None:
    render.write(out, json.dumps(payload, indent=2) if as_json else text)


class _Tee:
    """Forwards output while measuring it -- what we emit is what an agent pays."""

    def __init__(self, inner):
        self._inner = inner
        self._chunks: list[str] = []

    def write(self, text: str) -> int:
        # Record only what actually left the process. `render.write` retries a
        # failed encode, and counting the attempt as well as the retry doubled
        # every measurement of the one task whose diff carried non-ASCII text.
        written = self._inner.write(text)
        self._chunks.append(text)
        return written

    def flush(self) -> None:
        self._inner.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._inner, "isatty", lambda: False)())

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    from . import indexed

    out = out or sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    indexed_command = indexed.intent(argv)
    parser = _parser(indexed_errors=indexed_command is not None)
    try:
        args, extras = parser.parse_known_args(argv)
    except _IndexedUsageError as error:
        return indexed.syntax_error(out, indexed_command, argv, str(error), DEFAULT_BUDGET)
    except SystemExit as exit_code:
        # argparse has already printed usage and named the offending argument;
        # printing our own copy gave the agent the same block twice.
        return int(exit_code.code or 2)

    if extras:
        if indexed_command is not None:
            return indexed.syntax_error(out, indexed_command, argv,
                                        f"Unrecognized argument: {extras[0]}", DEFAULT_BUDGET)
        sub = getattr(parser, "subcommands", {}).get(getattr(args, "command", None))
        usage = (sub or parser).format_usage().strip()
        render.write(out, f"gita: unrecognized argument: {extras[0]}\n{usage}")
        return 2

    if not args.command:
        # Full argparse help is expensive for an agent to read; point at the one
        # command that answers the question.
        render.write(out, USAGE)
        return 2

    if getattr(args, "base_flag", None):
        args.base = args.base_flag
    if getattr(args, "head_flag", None):
        args.head = args.head_flag

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
        "exit_code": code,
        "error": None if code == 0 else EXIT_REASON.get(code, "failed"),
        "budget": getattr(args, "budget", None),
        "options": _options_used(args),
    })
    return code


#: Which optional behaviour a call actually asked for. `savings` was removed
#: after the logs showed 0 uses in 1,209 invocations; nothing else can be
#: retired on evidence without recording this.
_OPTIONS = ("filter", "intent", "interface_only", "patch", "brief", "as_json", "depth",
            "indexed")


def _options_used(args) -> list[str]:
    used = []
    for name in _OPTIONS:
        value = getattr(args, name, None)
        if value not in (None, False, ""):
            used.append(name)
    return used


#: A failure is only actionable once the log says which kind it was. `ok: false`
#: on its own turned twenty recorded failures into a mystery.
EXIT_REASON = {
    2: "usage",
    3: "bad-revision-or-repo",
    5: "ambiguous-entity",
    6: "budget-too-small",
    130: "interrupted",
}


def _normalize_history_arguments(repo, args) -> None:
    if (args.entity is not None and args.base is not None and args.head is None
            and repo.resolve(args.entity)):
        args.entity, args.base, args.head = None, args.entity, args.base


def _dispatch(command, out, repo, args, colour, parser) -> int:
    try:
        if command == "history":
            _normalize_history_arguments(repo, args)
        if command in ("index", "id") or (
            command == "history" and (
                args.indexed or any(getattr(args, name, None) is not None
                                    for name in ("scope", "at", "generation", "cursor", "order"))
            )
        ):
            from . import indexed

            return indexed.handle(command, out, repo, args)

        if command == "serve":
            from ..mcp.server import serve

            return serve(repo_path=args.repo)

        if command == "history":
            return _cmd_history(out, repo, args, colour)

        if command == "diff":
            _normalize_diff_revisions(repo, args)
            _resolve(repo, args.base, args.head)
            return _cmd_diff(out, repo, args, colour)
        _resolve(repo, args.base, args.head)
        if command == "show":
            return _cmd_show(out, repo, args, colour)
        if command == "expand":
            return _cmd_expand(out, repo, args, colour)

    except GitError as error:
        # A bad revision and a missing repository look identical from inside git,
        # and "unknown revision: HEAD" sent agents hunting for a branch.
        if not repo.is_repository():
            render.write(out, f"gita: not a git repository: {repo.root}")
        else:
            render.write(out, f"gita: {error}")
        return 3
    except KeyboardInterrupt:  # pragma: no cover
        return 130

    render.write(out, USAGE)
    return 2


def _indexed_changeset(repo, args):
    """``ChangeSet`` from the indexed engine, or a message saying why not.

    Returns ``(changeset, coverage, None)`` on success and
    ``(None, None, message)`` when the request cannot be served. The failure is
    never silently converted into a legacy-engine answer: two engines that
    disagree about identity must not be indistinguishable in the output.
    """
    from ..index.compare import compare
    from ..index.query import QueryError
    from ..vcs.git import CommittedRepo, GitObjectError

    if not args.head:
        return None, None, ("gita: --indexed compares committed revisions; it "
                            "cannot see uncommitted work.\ngive two revisions, "
                            "e.g. `gita diff HEAD~1 HEAD --indexed`")
    scope = args.scope or "default"
    try:
        with CommittedRepo(repo.root) as repository:
            changeset, coverage = compare(repository, args.base, args.head,
                                          scope=scope, generation=args.generation)
            return changeset, coverage, None
    except QueryError as error:
        if error.code == "not_built":
            return None, None, (f"gita: no index for scope '{scope}'.\n"
                                f"run `gita index --scope {scope}` first, or drop "
                                "--indexed to use the re-derived comparison")
        if error.code == "outside_scope":
            return None, None, (f"gita: {args.base}..{args.head} is not on the "
                                f"first-parent history recorded for scope "
                                f"'{scope}'.\nre-run `gita index --scope {scope}` "
                                "to extend it, or drop --indexed")
        return None, None, f"gita: indexed compare unavailable ({error.code}): {error}"
    except GitObjectError as error:
        return None, None, f"gita: {error}"


def _cmd_diff(out, repo, args, colour) -> int:
    coverage = None
    if getattr(args, "indexed", False):
        changeset, coverage, problem = _indexed_changeset(repo, args)
        if problem:
            _emit(out, {"error": "indexed unavailable", "detail": problem},
                  problem, args.as_json)
            return 3
    else:
        changeset = diff_revisions(repo, args.base, args.head)
    selected = focus(changeset, args.filter, args.interface_only)
    intent = Intent.parse(getattr(args, "intent", ""))

    if args.patch:
        result = material_patch(repo, args.base, args.head, selected, budget=args.budget)
        material = selected.material()
        payload = {
            "patch": result.patch, "tokens": result.tokens,
            "status": result.status, "truncated": result.truncated,
            "budget": result.budget, "requested_budget": result.requested_budget,
            "detailed": result.detailed, "omitted": result.omitted,
            "omitted_count": len(result.omitted),
            "changes": [render.change_payload(c) for c in material],
        }
        if result.status == "nothing_fits":
            payload["error"] = "budget too small"
        text = render.render_patch(result.patch, colour)
        if not text and not result.truncated:
            text = (f"{len(material)} material changes; no textual hunks"
                    if material else "no material changes")
        if args.as_json or text:
            _emit(out, payload, text, args.as_json)
        if result.truncated and not args.as_json:
            status = "partial patch" if result.patch else "nothing fits"
            cap = "; raw-diff cap" if result.budget < result.requested_budget else ""
            render.write(sys.stderr,
                         f"gita: {status}; {len(result.omitted)} entities omitted; "
                         f"effective budget {result.budget} tokens "
                         f"(requested {result.requested_budget}{cap}). "
                         "Use --json for omitted IDs.")
        annotate(truncated=result.truncated, files=selected.files_changed,
                 changes=len(material), detailed=len(result.detailed),
                 noise_filtered=len(changeset) - len(changeset.material()))
        return 6 if result.status == "nothing_fits" else 0

    # One call should answer the question: a follow-up costs a whole turn of
    # re-sent context, which dwarfs anything saved by withholding detail.
    result = compose(repo, args.base, args.head, selected,
                     budget=args.budget, detail=not args.brief, intent=intent)

    # Silence that exits zero is the worst answer available: an agent cannot tell
    # "nothing changed" from "your budget bought nothing".
    if not result.text.strip() and selected.material():
        needed = count_tokens(compose(repo, args.base, args.head, selected,
                                      budget=DEFAULT_BUDGET, detail=False).text)
        _emit(out, {"error": "budget too small", "budget": args.budget,
                    "summary_tokens": needed},
              f"gita: budget {args.budget} is too small to say anything; "
              f"the summary alone needs about {needed} tokens",
              args.as_json)
        return 6

    payload = {
        "base": args.base,
        "head": args.head,
        "text": result.text,
        "tokens": result.tokens,
        "budget": result.budget,
        "truncated": result.truncated,
        "detailed": result.detailed,
        "unreferenced": result.unreferenced,
        "filter": args.filter,
        "engine": "indexed" if getattr(args, "indexed", False) else "derived",
        "coverage": coverage,
        "intent": getattr(args, "intent", ""),
        "intent_terms": list(intent.terms),
        "interface_only": args.interface_only,
        "files_changed": selected.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
        "changes": [render.change_payload(c) for c in selected.material()],
    }
    # An incomplete answer is the moment an agent decides whether to trust gita
    # or go and read the file itself. The log has to be able to tell them apart.
    annotate(truncated=result.truncated,
             files=selected.files_changed,
             changes=len(selected.material()),
             detailed=len(result.detailed),
             noise_filtered=len(changeset) - len(changeset.material()))

    text = render.render_answer(result.text, colour)
    if coverage:
        from ..index.compare import coverage_warning

        # Appended after the budgeted answer, not inside it. A caveat about how
        # complete the evidence is must not be the thing the budget drops.
        caveat = coverage_warning(coverage)
        if caveat:
            text = f"{text}\n\n{caveat}" if text else caveat
    _emit(out, payload, text, args.as_json)
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
        changeset = diff_revisions(repo, args.base, args.head)
        message = _absent_entity_message(repo, changeset, args.entity,
                                         args.base, args.head)
        _emit(out, {"entity": args.entity, "patch": "", "error": "not found"},
              message, args.as_json)
        return 4

    _emit(out,
          {"entity": entity, "patch": patch, "tokens": count_tokens(patch)},
          render.render_patch(patch, colour),
          args.as_json)
    return 0


def _absent_entity_message(repo, changeset, entity: str, base, head) -> str:
    """Absent from a diff is not the same as absent from the codebase.

    Reporting a function that simply did not change as "not found" sends an
    agent hunting for a deletion that never happened.
    """
    name = entity.rsplit("::", 1)[-1]
    exists = bool(repo.text("grep", "-F", "-l", "--", name, check=False).strip())
    where = f"{base}..{head}" if head else "the working tree"
    if exists:
        return (f"gita: {entity} did not change in {where}\n"
                f"it still exists; run `gita diff {base} {head or ''}`".rstrip()
                + " to see what did change")
    return (f"gita: no entity named {entity} in {where}\n"
            f"run `gita diff {base} {head or ''}`".rstrip()
            + " to see what changed")


def _cmd_expand(out, repo, args, colour) -> int:
    changeset = diff_revisions(repo, args.base, args.head)
    entity = args.entity
    intent = Intent.parse(getattr(args, "intent", ""))
    lines = expand(changeset, entity, budget=args.budget, intent=intent)

    if not lines:
        resolved = _fallback_entity(changeset, args.entity, out, args.as_json)
        if resolved is False:
            return 5
        if resolved and resolved != entity:
            entity = resolved
            lines = expand(changeset, entity, budget=args.budget, intent=intent)

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

    # Positional and flag forms mean the same thing; the flags came first.
    since = args.since or args.base
    until = args.until or args.head or "HEAD"
    _resolve(repo, since, until)

    if args.entity:
        events = entity_history(repo, args.entity, since=since,
                                until=until, limit=args.limit)
        if not events:
            _emit(out, {"entity": args.entity, "events": [], "error": "no history"},
                  f"gita: no recorded changes to {args.entity} "
                  f"in the last {args.limit} commits\n"
                  f"run `gita diff {until}^ {until}` to see recent changes, "
                  f"or raise --limit",
                  args.as_json)
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

    summaries = series(repo, since=since, until=until, limit=args.limit)
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
