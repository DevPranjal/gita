"""Plain-text rendering for the CLI.

Kept separate from argument handling so output is testable without a terminal.
Colour is opt-in and disappears when piped, so `gita diff | ...` stays clean.
"""

from __future__ import annotations

import os
import sys

from ..context import ContextView, count_tokens
from ..diff.changes import ChangeSet, EntityChange

_STYLES = {
    "dim": "\033[2m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "reset": "\033[0m",
}

_KIND_COLOUR = {
    "added": "green",
    "removed": "red",
    "renamed": "yellow",
    "moved": "yellow",
    "signature_changed": "red",
    "body_changed": "cyan",
}


def colour_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("GITA_COLOR") == "always":
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, style: str, enabled: bool) -> str:
    if not enabled or style not in _STYLES:
        return text
    return f"{_STYLES[style]}{text}{_STYLES['reset']}"


def render_view(view: ContextView, colour: bool = False) -> str:
    lines = [paint(view.l0, "bold", colour)]
    if view.l1:
        lines.append("")
        for line in view.l1.splitlines():
            lines.append(_paint_kind(line, colour))
    if view.truncated:
        lines.append("")
        lines.append(paint(
            f"[truncated to {view.budget} tokens · raise --budget for more]",
            "dim", colour))
    return "\n".join(lines)


def _paint_kind(line: str, colour: bool) -> str:
    if not colour or "[" not in line:
        return line
    head, _, tail = line.rpartition("[")
    kind = tail.rstrip("]")
    style = _KIND_COLOUR.get(kind)
    return f"{head}[{paint(kind, style, colour)}]" if style else line


def render_lines(lines: list[str], colour: bool = False) -> str:
    return "\n".join(_paint_kind(line, colour) for line in lines)


def render_patch(patch: str, colour: bool = False) -> str:
    if not colour:
        return patch
    out = []
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(paint(line, "green", colour))
        elif line.startswith("-") and not line.startswith("---"):
            out.append(paint(line, "red", colour))
        elif line.startswith("@@"):
            out.append(paint(line, "cyan", colour))
        else:
            out.append(line)
    return "\n".join(out)


def render_savings(raw: str, view: ContextView, colour: bool = False) -> str:
    raw_tokens = count_tokens(raw)
    l0_tokens = count_tokens(view.l0)

    def row(label: str, tokens: int) -> str:
        if not raw_tokens:
            return f"  {label:<16}{tokens:>8,} tokens"
        cut = 1 - tokens / raw_tokens
        return f"  {label:<16}{tokens:>8,} tokens{cut:>10.1%} less"

    return "\n".join([
        paint("token cost of this change", "bold", colour),
        f"  {'raw git diff':<16}{raw_tokens:>8,} tokens",
        row("gita L0", l0_tokens),
        row("gita L0+L1", view.tokens),
    ])


def change_payload(change: EntityChange) -> dict:
    entity = change.entity
    return {
        "id": entity.id,
        "kind": change.kind.value,
        "path": entity.path,
        "name": entity.name,
        "entity_kind": entity.kind.value,
        "start_line": entity.start_line,
        "end_line": entity.end_line,
        "interface": change.affects_interface,
        "previous_id": change.previous.id if change.previous else None,
    }


def view_payload(view: ContextView, changeset: ChangeSet,
                 base: str, head: str) -> dict:
    return {
        "base": base,
        "head": head,
        "l0": view.l0,
        "l1": view.l1,
        "tokens": view.tokens,
        "budget": view.budget,
        "depth": view.depth,
        "truncated": view.truncated,
        "files_changed": changeset.files_changed,
        "noise_filtered": len(changeset) - len(changeset.material()),
        "clusters": [
            {"path": c.path, "title": c.title, "size": len(c), "score": c.score}
            for c in view.clusters
        ],
        "changes": [change_payload(c) for c in changeset.material()],
    }


def write(out, text: str) -> None:
    print(text, file=out)


def stdout_is_tty() -> bool:
    return colour_enabled(sys.stdout)
