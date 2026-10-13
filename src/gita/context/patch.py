"""L2: the actual hunks, scoped to a single entity.

The expensive layer. An agent pays for this only after L0/L1 told it which
entity is worth the tokens.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass

from ..diff.differ import diff_trees
from ..entities.extractor import extract_path
from ..entities.model import Entity, EntityTree, SyntaxRegion
from ..entities.store import TREES
from ..diff.changes import EntityChange
from ..vcs.git import Repo


def _parse(blob: bytes, path: str) -> EntityTree | None:
    try:
        return extract_path(blob, path)
    except (ValueError, RecursionError):
        return None


def _load(repo: Repo, rev: str | None, path: str,
          cache: dict | None) -> tuple[EntityTree | None, list[str]]:
    """Parse one revision of one file, at most once per cache.

    A large diff asks for many entities from the same file. Re-reading and
    re-parsing it per entity made `got-new-option` a ten-second call, which is
    long enough that an agent starts reading files by hand instead.
    """
    key = (rev, path)
    if cache is not None and key in cache:
        return cache[key]

    result: tuple[EntityTree | None, list[str]] = (None, [])
    blob = repo.blob(rev, path)
    if blob is not None and b"\x00" not in blob[:8000]:
        tree = TREES.get(blob, path, _parse)
        if tree is not None:
            result = (tree, blob.decode("utf8", "replace").replace("\r\n", "\n")
                      .splitlines(keepends=True))

    if cache is not None:
        cache[key] = result
    return result


def _entity_at(repo: Repo, rev: str, path: str, entity_id: str,
               cache: dict | None = None) -> tuple[Entity | None, list[str]]:
    tree, source = _load(repo, rev, path, cache)
    if tree is None:
        return None, []
    return tree.get(entity_id), source


def _slice(entity: Entity | None, source: list[str]) -> list[str]:
    if entity is None:
        return []
    return source[entity.start_line - 1:entity.end_line]


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _shift(start: str, offset: int) -> str:
    # `0` means "this side is empty", a position no offset applies to.
    number = int(start)
    return str(number + offset) if number else "0"


def _absolute(patch: str, old_offset: int, new_offset: int) -> str:
    """Renumber hunk headers from entity-relative to file-absolute.

    difflib numbers hunks from 1 within the lines it is handed, and it is
    handed only the entity's own lines. An entity starting at line 18 therefore
    reported `@@ +1,2 @@`, so an agent reading that as a file position landed on
    the top of the file. The whole point of naming the entity is to say where to
    look, and a confidently wrong location is worse than none.
    """
    if not old_offset and not new_offset:
        return patch

    out = []
    for line in patch.splitlines(keepends=True):
        match = _HUNK.match(line)
        if match is None:
            out.append(line)
            continue
        old_start, old_len, new_start, new_len = match.groups()
        old = f"-{_shift(old_start, old_offset)}" + (f",{old_len}" if old_len else "")
        new = f"+{_shift(new_start, new_offset)}" + (f",{new_len}" if new_len else "")
        out.append(f"@@ {old} {new} @@{line[match.end():]}")
    return "".join(out)


def entity_diff(repo: Repo, base: str, head: str, entity_id: str,
                context_lines: int = 3, cache: dict | None = None) -> str:
    """Unified diff of one entity between two revisions.

    Returns an empty string when the entity exists on neither side. Pass a
    shared ``cache`` dict when diffing several entities from the same files.
    """
    path = entity_id.split("::", 1)[0]

    previous, old_source = _entity_at(repo, base, path, entity_id, cache)
    current, new_source = _entity_at(repo, head, path, entity_id, cache)
    if previous is None and current is None:
        return ""

    patch = difflib.unified_diff(
        _slice(previous, old_source),
        _slice(current, new_source),
        fromfile=f"a/{entity_id}",
        tofile=f"b/{entity_id}",
        n=context_lines,
    )
    return _absolute(
        "".join(patch),
        previous.start_line - 1 if previous is not None else 0,
        current.start_line - 1 if current is not None else 0,
    )


def change_diff(repo: Repo, base: str, head: str, change: EntityChange,
                context_lines: int = 3, cache: dict | None = None) -> str:
    """Unified diff between Tree-sitter entities matched by a change."""
    _, old_source = _load(repo, base, change.previous.path, cache) \
        if change.previous is not None else (None, [])
    _, new_source = _load(repo, head, change.current.path, cache) \
        if change.current is not None else (None, [])

    patch = difflib.unified_diff(
        _slice(change.previous, old_source),
        _slice(change.current, new_source),
        fromfile=f"a/{change.previous.id}" if change.previous else "/dev/null",
        tofile=f"b/{change.current.id}" if change.current else "/dev/null",
        n=context_lines,
    )
    return _absolute(
        "".join(patch),
        change.previous.start_line - 1 if change.previous is not None else 0,
        change.current.start_line - 1 if change.current is not None else 0,
    )


def _owned_patch(old: list[tuple[int, str]], new: list[tuple[int, str]],
                 context_lines: int, *, old_trivia: frozenset[int] = frozenset(),
                 new_trivia: frozenset[int] = frozenset()) -> str:
    """Diff owned lines, splitting hunks at holes left by descendant entities.

    Coordinates remain file-relative: removing a child from the input must not
    make the remaining, nonadjacent source lines look contiguous.
    """
    def anchor(source, index):
        if index < len(source):
            return source[index][0]
        return source[-1][0] + 1 if source else 0

    hunks: list[str] = []
    rows: list[str] = []
    start_old = start_new = next_old = next_new = 0
    old_count = new_count = 0
    meaningful = False

    def flush():
        if meaningful:
            left = f"{start_old + bool(old_count)},{old_count}"
            right = f"{start_new + bool(new_count)},{new_count}"
            hunks.append(f"@@ -{left} +{right} @@\n" + "\n".join(rows) + "\n")
        rows.clear()

    def append(sign, text, i, j):
        nonlocal start_old, start_new, next_old, next_new, old_count, new_count, meaningful
        if rows and (i != next_old or j != next_new):
            flush()
        if not rows:
            start_old, start_new = i, j
            old_count = new_count = 0
            meaningful = False
        rows.append(sign + text.rstrip("\r\n"))
        if sign in "+-":
            trivia = old_trivia if sign == "-" else new_trivia
            number = i + 1 if sign == "-" else j + 1
            meaningful |= bool(text.strip()) or number not in trivia
        old_count += sign != "+"
        new_count += sign != "-"
        next_old, next_new = i + (sign != "+"), j + (sign != "-")

    matcher = difflib.SequenceMatcher(
        a=[text for _, text in old], b=[text for _, text in new], autojunk=False,
    )
    for group in matcher.get_grouped_opcodes(n=context_lines):
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    append(" ", old[i][1], old[i][0], new[j][0])
            else:
                for i in range(i1, i2):
                    append("-", old[i][1], old[i][0], anchor(new, j1))
                for j in range(j1, j2):
                    append("+", new[j][1], anchor(old, i2), new[j][0])
        flush()
    return "".join(hunks)


@dataclass(slots=True)
class DisplayDiffs:
    patches: dict[str, str]
    required: dict[str, set[str]]

    def covered(self, shown: set[str]) -> set[str]:
        return {entity_id for entity_id, owners in self.required.items() if owners <= shown}


def _region_lines(region: SyntaxRegion, source: list[str]) -> list[tuple[int, str]]:
    start = region.start_line - 1
    stop = region.end_line - (region.end_column == 0)
    if start >= stop or stop > len(source):
        return []
    if (_line_parts(region, start, source[start])[0].strip()
            or _line_parts(region, stop - 1, source[stop - 1])[2].strip()):
        return []  # Shared code lines need the ordinary ownership fallback.
    return list(enumerate(source[start:stop], start))


def _atomic_patch(old: list[tuple[int, str]], new: list[tuple[int, str]], label: str) -> str:
    if not old and not new:
        return ""
    left = f"{old[0][0] + 1},{len(old)}" if old else "0,0"
    right = f"{new[0][0] + 1},{len(new)}" if new else "0,0"
    rows = ["-" + text.rstrip("\r\n") for _, text in old]
    rows += ["+" + text.rstrip("\r\n") for _, text in new]
    return f"@@ -{left} +{right} @@ {label}\n" + "\n".join(rows) + "\n"


def _join_patches(parts: list[str]) -> str:
    hunks = [hunk for part in parts for hunk in re.split(r"(?=^@@ )", part, flags=re.M) if hunk]

    def position(hunk):
        match = re.match(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@", hunk)
        return int(match[3]) if int(match[4]) else int(match[1])

    return "".join(sorted(hunks, key=position))


def _comment_slot(region: SyntaxRegion, lines: list[tuple[int, str]], tree: EntityTree,
                  source: list[str], paired: dict[tuple[int, str], str], side: int) -> tuple | None:
    """Anchor a standalone comment without skipping intervening syntax."""
    children = [tree.entities[key] for key in tree.entities[region.parent_id].children]
    before = next((e for e in reversed(children)
                   if e.end_line - (e.end_column == 0) <= lines[0][0]), None)
    after = next((e for e in children if e.start_line - 1 > lines[-1][0]), None)
    if before is None or after is None:
        return None
    gaps = source[before.end_line - (before.end_column == 0):lines[0][0]]
    gaps += source[lines[-1][0] + 1:after.start_line - 1]
    if any(line.strip() for line in gaps):
        return None

    def anchor(entity):
        identity = paired.get((side, entity.id))
        if identity is None:
            return None
        first = entity.start_line - 1
        last = entity.end_line - (entity.end_column == 0) - 1
        prefix = _line_parts(entity, first, source[first])[0]
        suffix = _line_parts(entity, last, source[last])[2]
        return identity, entity.kind, prefix.strip(), suffix.strip()

    left, right = anchor(before), anchor(after)
    if left is None or right is None:
        return None
    return region.container_path, left, right


def _comment_patches(snapshots, identities, allowed, whole_files, claimed, parts, paired):
    groups: dict[str, list[list[list[tuple[int, str]]]]] = {}
    slots: dict[tuple[int, str, int], tuple | None] = {}
    pair_counts = Counter((side, identity) for (side, _), identity in paired.items())
    paired = {key: identity for key, identity in paired.items()
              if pair_counts[0, identity] == pair_counts[1, identity] == 1}
    for (side, path), (tree, source) in snapshots.items():
        if tree is None or path in whole_files:
            continue
        other = snapshots[1 - side, path][0]
        if tree.parse_error or (other is not None and other.parse_error):
            continue
        for region in tree.regions:
            owner = identities[side, region.parent_id]
            if region.kind != "comment" or owner not in allowed:
                continue
            lines = _region_lines(region, source)
            if not lines or any(number in claimed[side, path] for number, _ in lines):
                continue
            groups.setdefault(owner, [[], []])[side].append(lines)
            slots[side, owner, lines[0][0]] = _comment_slot(region, lines, tree, source, paired, side)
            claimed[side, path].update(number for number, _ in lines)

    for owner, (old, new) in groups.items():
        keys = [[tuple(text.rstrip("\r\n") for _, text in block) for block in side]
                for side in (old, new)]
        if keys[0] == keys[1]:
            continue
        counts = [Counter(side) for side in keys]
        anchors = [
            [(key, None if counts[0][key] == counts[1][key] == 1 else (side, index))
             for index, key in enumerate(values)]
            for side, values in enumerate(keys)
        ]
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                a=anchors[0], b=anchors[1], autojunk=False).get_opcodes():
            if (tag == "replace" and i2 - i1 == j2 - j1 == 1
                    and counts[0][keys[0][i1]] == counts[1][keys[1][j1]] == 1):
                slot = slots[0, owner, old[i1][0][0]]
                if slot is not None and slot == slots[1, owner, new[j1][0][0]]:
                    parts.setdefault(owner, []).append(
                        _atomic_patch(old[i1], new[j1], "changed comment"))
                    continue
            if tag != "equal":
                parts.setdefault(owner, []).extend(
                    _atomic_patch(block, [], "removed comment") for block in old[i1:i2])
                parts.setdefault(owner, []).extend(
                    _atomic_patch([], block, "added comment") for block in new[j1:j2])


def _unchanged_blank_gaps(snapshots, identities, claimed):
    """Keep identical gaps between uniquely matched syntax neighbors aligned."""
    scopes = {}
    for (side, path), (tree, source) in snapshots.items():
        if tree is None or tree.parse_error:
            continue
        anchors = []
        for entity in tree.walk():
            if entity.parent_id is None:
                continue
            start = entity.start_line - 1
            stop = min(entity.end_line - (entity.end_column == 0), len(source))
            key = ("entity", identities[side, entity.id],
                   tuple(text.rstrip("\r\n") for text in source[start:stop]))
            anchors.append((entity.parent_id, start, stop, key))
        for region in tree.regions:
            if region.kind != "comment":
                continue
            lines = _region_lines(region, source)
            if lines:
                anchors.append((region.parent_id, lines[0][0], lines[-1][0] + 1,
                                ("comment", tuple(text.rstrip("\r\n")
                                                  for _, text in lines))))
        for parent, start, stop, key in anchors:
            scopes.setdefault(identities[side, parent], [[], []])[side].append(
                (start, stop, key, path))

    for old, new in scopes.values():
        counts = [Counter(item[2] for item in side) for side in (old, new)]
        gaps = [{}, {}]
        for side, anchors in enumerate((old, new)):
            ordered = sorted(anchors, key=lambda item: (item[0], item[1]))
            for left, right in zip(ordered, ordered[1:]):
                if left[1] > right[0] or not all(
                        counts[0][key] == counts[1][key] == 1
                        for key in (left[2], right[2])):
                    continue
                path = left[3]
                lines = list(enumerate(
                    snapshots[side, path][1][left[1]:right[0]], left[1]))
                if all(not text.strip() for _, text in lines):
                    gaps[side][left[2], right[2]] = (path, lines)
        for key in gaps[0].keys() & gaps[1].keys():
            before, after = gaps[0][key], gaps[1][key]
            if ([text.rstrip("\r\n") for _, text in before[1]]
                    == [text.rstrip("\r\n") for _, text in after[1]]):
                for side, (path, lines) in enumerate((before, after)):
                    claimed[side, path].update(number for number, _ in lines)
def _statement_patches(snapshots, identities, owners, material, matches,
                       whole_files, claimed, parts) -> dict[str, set[str]]:
    """Coalesce cross-owner edits, never promote a parent for changes elsewhere."""
    selected = {c.id: c for c in material}
    all_material = {
        identities[side, entity.id]
        for c in [*matches, *material] if not c.is_noise
        for side, entity in enumerate((c.previous, c.current)) if entity is not None
    }
    unselected = all_material - selected.keys()
    own: dict[tuple[int, str], list[tuple[int, str]]] = {}
    groups = {}
    for (side, path), (tree, source) in snapshots.items():
        if tree is None or path in whole_files:
            continue
        comment_lines = {
            number for region in tree.regions if region.kind == "comment"
            for number, _ in _region_lines(region, source)
        }
        for number, owner in enumerate(owners[side, path]):
            if owner in selected and number not in comment_lines:
                own.setdefault((side, owner), []).append((number, source[number]))
        for region in tree.regions:
            parent = identities[side, region.parent_id]
            change = selected.get(parent)
            if (region.kind != "expression_statement" or change is None
                    or change.previous is None or change.current is None):
                continue
            previous_tree = snapshots[0, change.previous.path][0]
            current_tree = snapshots[1, change.current.path][0]
            if (previous_tree is None or current_tree is None
                    or previous_tree.parse_error or current_tree.parse_error):
                continue
            lines = _region_lines(region, source)
            if lines:
                groups.setdefault((parent, region.container_path), [[], []])[side].append(
                    (path, lines))

    edited = {key: set() for key in snapshots}
    for owner, change in selected.items():
        old, new = own.get((0, owner), []), own.get((1, owner), [])
        matcher = difflib.SequenceMatcher(
            a=[text.rstrip("\r\n") for _, text in old],
            b=[text.rstrip("\r\n") for _, text in new], autojunk=False,
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            for side, entity, lines in ((0, change.previous, old[i1:i2]),
                                        (1, change.current, new[j1:j2])):
                if entity is not None:
                    edited[side, entity.path].update(number for number, text in lines if text.strip())

    promoted: dict[str, set[str]] = {}
    for (parent, _), (old, new) in sorted(
            groups.items(), key=lambda item: (selected[item[0][0]].entity.depth, item[0])):
        keys = [[tuple(text.rstrip("\r\n") for _, text in lines) for _, lines in side]
                for side in (old, new)]
        unique = all(count == 1 for side in keys for count in Counter(side).values())
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                a=keys[0], b=keys[1], autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            blocks = [(0, path, lines) for path, lines in old[i1:i2]]
            blocks += [(1, path, lines) for path, lines in new[j1:j2]]
            own_edit = child_edit = blocked = cut = False
            for side, path, lines in blocks:
                own_here = child_here = False
                for number, _ in lines:
                    owner = owners[side, path][number]
                    blocked |= number in claimed[side, path] or owner in unselected
                    if number in edited[side, path]:
                        own_here |= owner == parent
                        child_here |= owner != parent and owner in selected
                own_edit |= own_here
                child_edit |= child_here
                cut |= own_here and child_here
            paired = unique and i2 - i1 == j2 - j1 == 1
            if blocked or not (cut or (paired and own_edit and child_edit)):
                continue
            if paired:
                parts.setdefault(parent, []).append(
                    _atomic_patch(old[i1][1], new[j1][1], "statement replaced"))
            else:
                parts.setdefault(parent, []).extend(
                    _atomic_patch(lines if side == 0 else [], lines if side == 1 else [],
                                  "removed statement" if side == 0 else "added statement")
                    for side, _, lines in blocks)
            for side, path, lines in blocks:
                for number, _ in lines:
                    owner = owners[side, path][number]
                    if owner in selected and owner != parent:
                        promoted.setdefault(owner, set()).add(parent)
                    claimed[side, path].add(number)
    return promoted
def display_change_diffs(repo: Repo, base: str, head: str | None,
                         changes: list[EntityChange], context_lines: int = 1,
                         cache: dict | None = None) -> DisplayDiffs:
    """Disjoint, entity-owned hunks with Tree-sitter boundaries as anchors.

    Match all entities before filtering: an unselected child must not leak into
    its parent's detail. Equal delimiters from different entities never compete
    in the same text diff. Standalone entity_diff/change_diff remain full-source.
    """
    material = [change for change in changes if not change.is_noise]
    cache = cache if cache is not None else {}
    paths = {e.path for c in material for e in (c.previous, c.current) if e}
    snapshots = {
        (side, path): _load(repo, rev, path, cache)
        for path in sorted(paths) for side, rev in enumerate((base, head))
    }
    matches = [
        c for path in sorted(paths)
        for c in diff_trees(snapshots[0, path][0], snapshots[1, path][0])
    ]
    identities: dict[tuple[int, str], str] = {}
    paired: dict[tuple[int, str], str] = {}
    # Selected matches include cross-file moves reconciled above diff_trees.
    for change in [*matches, *material]:
        for side, entity in enumerate((change.previous, change.current)):
            if entity is not None:
                identities[side, entity.id] = change.id
                if change.previous is not None and change.current is not None:
                    paired[side, entity.id] = change.id

    peers = {
        (side, identities[side, entity.id]): entity
        for (side, _), (tree, _) in snapshots.items() if tree
        for entity in tree.walk()
    }
    whole_files = {
        c.id for c in material if c.entity.synthetic
        and (c.previous is None or c.current is None)
    }
    selected = {c.id for c in material}
    owners_by_source: dict[tuple[int, str], list[str]] = {}
    for (side, path), (tree, source) in snapshots.items():
        if tree is None:
            continue
        owners = [tree.root_id] * len(source)
        if tree.root_id not in whole_files:
            for entity in sorted(tree.walk(), key=lambda e: (e.depth, -e.line_count, e.id)):
                key = identities[side, entity.id]
                peer = peers.get((1 - side, key))
                stop = min(entity.end_line - (entity.end_column == 0), len(source))
                for line in range(entity.start_line - 1, stop):
                    # A callback may share its boundary line with changed call
                    # arguments outside the AST node. Keep those edits with its
                    # parent if the node is unchanged or filtered out.
                    if peer:
                        peer_source = snapshots[1 - side, peer.path][1]
                        boundary = (
                            peer.start_line - 1 if line == entity.start_line - 1
                            else peer.end_line - 1 if line == entity.end_line - 1
                            else None
                        )
                        if boundary is not None and boundary < len(peer_source):
                            left, node, right = _line_parts(entity, line, source[line])
                            old_left, old_node, old_right = _line_parts(
                                peer, boundary, peer_source[boundary],
                            )
                            if ((left, right) != (old_left, old_right)
                                    and (key not in selected or node == old_node)):
                                continue
                    owners[line] = key
        owners_by_source[side, path] = owners

    parts: dict[str, list[str]] = {}
    claimed = {key: set() for key in snapshots}
    promoted = _statement_patches(snapshots, identities, owners_by_source, material, matches,
                                  whole_files, claimed, parts)
    _comment_patches(snapshots, identities, {c.id for c in changes},
                     whole_files, claimed, parts, paired)
    _unchanged_blank_gaps(snapshots, identities, claimed)
    owned: dict[tuple[int, str], list[tuple[int, str]]] = {}
    for (side, path), owners in owners_by_source.items():
        for line, (owner, text) in enumerate(zip(owners, snapshots[side, path][1])):
            if line in claimed[side, path]:
                continue
            if owner in selected:
                owned.setdefault((side, owner), []).append((line, text))

    for change in material:
        old_tree = snapshots[0, (change.previous or change.entity).path][0]
        new_tree = snapshots[1, (change.current or change.entity).path][0]
        safe = not any(tree is not None and tree.parse_error for tree in (old_tree, new_tree))
        parts.setdefault(change.id, []).append(_owned_patch(
            owned.get((0, change.id), []), owned.get((1, change.id), []), context_lines,
            old_trivia=old_tree.layout_lines if safe and old_tree is not None else frozenset(),
            new_trivia=new_tree.layout_lines if safe and new_tree is not None else frozenset(),
        ))
    patches = {owner: _join_patches(fragments) for owner, fragments in parts.items()}
    required = {owner: {owner} if patch else set() for owner, patch in patches.items()}
    for owner, parents in promoted.items():
        required[owner].update(parents)
    return DisplayDiffs(patches, required)


def _line_parts(entity: Entity | SyntaxRegion, line: int, text: str) -> tuple[bytes, bytes, bytes]:
    """Tree-sitter columns count UTF-8 bytes, not Python string characters."""
    start = (entity.start_column or 0) if line == entity.start_line - 1 else 0
    end = entity.end_column if line == entity.end_line - 1 else None
    raw = text.rstrip("\r\n").encode("utf8")
    end = len(raw) if end is None else end
    return raw[:start], raw[start:end], raw[end:]
