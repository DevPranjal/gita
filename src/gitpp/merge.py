"""Three-way semantic merge over LibCST trees.

v0.0 scope: just enough to pass `tests/scenarios/parallel-methods`.

Identity model (v0.0): nodes are keyed by *name* for `ClassDef`, `FunctionDef`,
and `Assign`-with-single-target. Other statements key by their rendered text.
This is a temporary stand-in for stable IDs (see SPEC.md §1.1).

Merge policy (v0.0): only `Module.body` and `ClassDef.body` are treated as
keyed sequences. `FunctionDef` body merge is deferred to v0.1 — if both sides
diverge inside a function, that's a conflict for now. This is enough for
scenario #3 (parallel-methods) and a stepping stone for scenarios #1 and #2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Sequence

import libcst as cst


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conflict:
    """A merge conflict that gitpp could not resolve automatically."""

    kind: str  # human-readable category, e.g. "diverged-function-body"
    key: tuple  # the identity key of the conflicting node
    detail: str = ""


# ---------------------------------------------------------------------------
# Identity (v0.0): name-based keys
# ---------------------------------------------------------------------------


Key = tuple[Hashable, ...]


def statement_key(stmt: cst.BaseStatement) -> Key:
    """Compute a stable-ish identity key for a top-level or class-body statement.

    For v0.0 we key by *kind + name* where a name is available, and fall back
    to rendered source. v0.1 will replace this with proper stable IDs.
    """
    if isinstance(stmt, cst.ClassDef):
        return ("class", stmt.name.value)
    if isinstance(stmt, cst.FunctionDef):
        return ("func", stmt.name.value)
    if isinstance(stmt, cst.SimpleStatementLine):
        # Single-target assignment? Key by the target name.
        if len(stmt.body) == 1 and isinstance(stmt.body[0], cst.Assign):
            assign = stmt.body[0]
            if len(assign.targets) == 1 and isinstance(assign.targets[0].target, cst.Name):
                return ("assign", assign.targets[0].target.value)
        # Otherwise: key by the rendered text. Brittle, but adequate for v0.0.
        return ("stmt", _render(stmt))
    return ("other", _render(stmt))


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------


def _nodes_equal(a, b) -> bool:
    """Structural equality. LibCST's `deep_equals` compares whitespace too,
    which is what we want for v0.0 (formatting-preserving merges).

    Handles `None`, individual `CSTNode`s, and sequences (tuples/lists) of
    nodes — LibCST exposes things like `ClassDef.bases` and `.decorators` as
    tuples rather than wrapper nodes.

    For v0.1 we will canonicalize whitespace before comparing — see SPEC.md §2.
    """
    if a is None or b is None:
        return a is b
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        if not (isinstance(a, (tuple, list)) and isinstance(b, (tuple, list))):
            return False
        if len(a) != len(b):
            return False
        return all(_nodes_equal(x, y) for x, y in zip(a, b))
    return a.deep_equals(b)


def _render(node: cst.CSTNode) -> str:
    """Render a node to source text. Used for keying fallback and for tests."""
    if isinstance(node, cst.Module):
        return node.code
    return cst.Module(body=[]).code_for_node(node)


# ---------------------------------------------------------------------------
# Three-way merge: nodes
# ---------------------------------------------------------------------------


def merge_node(
    base: cst.CSTNode | None,
    ours: cst.CSTNode | None,
    theirs: cst.CSTNode | None,
    key: Key,
) -> tuple[cst.CSTNode | None, list[Conflict]]:
    """Generic 3-way merge for a single node identified by `key`.

    Returns `(merged_node_or_None, conflicts)`. A `None` return means "this
    node should be omitted from its parent sequence" (both sides deleted it,
    or one side deleted and the other didn't modify).
    """
    # Case 1: brand new node added on one or both sides.
    if base is None:
        if ours is not None and theirs is not None:
            if _nodes_equal(ours, theirs):
                return ours, []
            return ours, [
                Conflict(
                    kind="both-added-different",
                    key=key,
                    detail="Both sides added a node with the same key but different content.",
                )
            ]
        return (ours if ours is not None else theirs), []

    # Case 2: deletions.
    if ours is None and theirs is None:
        return None, []
    if ours is None:
        if _nodes_equal(base, theirs):
            return None, []  # we deleted, they didn't change → delete
        return theirs, [
            Conflict(
                kind="delete-vs-modify",
                key=key,
                detail="We deleted this node, but theirs modified it.",
            )
        ]
    if theirs is None:
        if _nodes_equal(base, ours):
            return None, []
        return ours, [
            Conflict(
                kind="delete-vs-modify",
                key=key,
                detail="Theirs deleted this node, but we modified it.",
            )
        ]

    # Case 3: all three present. Standard 3-way logic.
    base_eq_ours = _nodes_equal(base, ours)
    base_eq_theirs = _nodes_equal(base, theirs)
    if base_eq_ours and base_eq_theirs:
        return base, []
    if base_eq_ours:
        return theirs, []
    if base_eq_theirs:
        return ours, []
    if _nodes_equal(ours, theirs):
        return ours, []

    # Both sides diverged. Try to recurse into recognized structures.
    if (
        isinstance(base, cst.ClassDef)
        and isinstance(ours, cst.ClassDef)
        and isinstance(theirs, cst.ClassDef)
    ):
        return _merge_class_def(base, ours, theirs)

    # Default for v0.0: conflict (keep ours, flag it).
    return ours, [
        Conflict(
            kind="diverged",
            key=key,
            detail=f"Both sides modified a {type(base).__name__} differently and v0.0 "
            "cannot merge inside this node type yet.",
        )
    ]


# ---------------------------------------------------------------------------
# Three-way merge: keyed sequences
# ---------------------------------------------------------------------------


def merge_keyed_sequence(
    base: Sequence[cst.CSTNode],
    ours: Sequence[cst.CSTNode],
    theirs: Sequence[cst.CSTNode],
    key_fn,
) -> tuple[list[cst.CSTNode], list[Conflict]]:
    """3-way merge of a sequence of nodes, treated as a keyed collection.

    Order of the result:
      1. Members from `base` (in `base` order) that survive the merge.
      2. Members added by `ours` (in `ours` order).
      3. Members added by `theirs` (in `theirs` order).

    Members present in `base` retain their relative ordering even if `ours`
    reordered them — reordering is not yet a tracked operation in v0.0.
    """
    base_keys = [key_fn(n) for n in base]
    ours_keys = [key_fn(n) for n in ours]
    theirs_keys = [key_fn(n) for n in theirs]

    base_map = dict(zip(base_keys, base))
    ours_map = dict(zip(ours_keys, ours))
    theirs_map = dict(zip(theirs_keys, theirs))

    result: list[cst.CSTNode] = []
    conflicts: list[Conflict] = []
    seen: set[Key] = set()

    # Pass 1: base members (preserving base order).
    for k in base_keys:
        if k in seen:
            continue
        seen.add(k)
        merged, c = merge_node(base_map.get(k), ours_map.get(k), theirs_map.get(k), k)
        conflicts.extend(c)
        if merged is not None:
            result.append(merged)

    # Pass 2: ours-only additions.
    for k in ours_keys:
        if k in seen or k in base_map:
            continue
        seen.add(k)
        merged, c = merge_node(None, ours_map[k], theirs_map.get(k), k)
        conflicts.extend(c)
        if merged is not None:
            result.append(merged)

    # Pass 3: theirs-only additions.
    for k in theirs_keys:
        if k in seen or k in base_map:
            continue
        seen.add(k)
        merged, c = merge_node(None, ours_map.get(k), theirs_map[k], k)
        conflicts.extend(c)
        if merged is not None:
            result.append(merged)

    return result, conflicts


# ---------------------------------------------------------------------------
# Class-def merge
# ---------------------------------------------------------------------------


def _merge_class_def(
    base: cst.ClassDef, ours: cst.ClassDef, theirs: cst.ClassDef
) -> tuple[cst.ClassDef, list[Conflict]]:
    """Merge two divergent ClassDefs by treating the body as a keyed set of members."""
    # v0.0: assume bases, decorators, name unchanged across all three. We'll
    # validate that and conflict otherwise.
    conflicts: list[Conflict] = []

    if not (
        _nodes_equal(base.name, ours.name)
        and _nodes_equal(base.name, theirs.name)
        and _nodes_equal(base.bases, ours.bases)
        and _nodes_equal(base.bases, theirs.bases)
        and _nodes_equal(base.decorators, ours.decorators)
        and _nodes_equal(base.decorators, theirs.decorators)
    ):
        conflicts.append(
            Conflict(
                kind="class-header-diverged",
                key=("class", base.name.value),
                detail="v0.0 cannot merge changes to class name, bases, or decorators.",
            )
        )

    body = base.body
    if not (
        isinstance(body, cst.IndentedBlock)
        and isinstance(ours.body, cst.IndentedBlock)
        and isinstance(theirs.body, cst.IndentedBlock)
    ):
        conflicts.append(
            Conflict(
                kind="class-body-unsupported",
                key=("class", base.name.value),
                detail="Class body is not an IndentedBlock; not supported in v0.0.",
            )
        )
        return ours, conflicts

    merged_members, c = merge_keyed_sequence(
        body.body, ours.body.body, theirs.body.body, key_fn=statement_key
    )
    conflicts.extend(c)
    new_body = body.with_changes(body=merged_members)
    return base.with_changes(body=new_body), conflicts


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def merge_modules(
    base: cst.Module, ours: cst.Module, theirs: cst.Module
) -> tuple[cst.Module, list[Conflict]]:
    """Three-way merge of two divergent Python modules against a common ancestor."""
    merged_body, conflicts = merge_keyed_sequence(
        base.body, ours.body, theirs.body, key_fn=statement_key
    )
    return base.with_changes(body=merged_body), conflicts
