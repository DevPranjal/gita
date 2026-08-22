"""EntityTree x EntityTree -> ChangeSet.

Matching runs in three passes, cheapest and most certain first:

1. by stable id      -- the entity kept its place in the tree
2. by content hash   -- identical body somewhere else: moved or renamed
3. by similarity     -- near-identical body: renamed and edited

Whatever survives all three is genuinely added or removed.
"""

from __future__ import annotations

import re

from collections import defaultdict

from ..entities.model import Entity, EntityTree
from .changes import ChangeKind, ChangeSet, EntityChange

#: Jaccard floor for calling two entities the same thing under a new name.
RENAME_THRESHOLD = 0.6

#: Bodies smaller than this are too generic to match on hash alone -- a dozen
#: stubs may all be `return None`.
MIN_BODY_TOKENS = 3

_SHINGLE = 3


def _unique_by(entities, key):
    """Index entities by ``key``, keeping only keys occurring exactly once.

    Uniqueness is the guard against false positives: an identical body seen
    twice on either side is not evidence of anything.
    """
    buckets: dict[str, list] = defaultdict(list)
    for entity in entities:
        buckets[key(entity)].append(entity)
    return {key_: found[0] for key_, found in buckets.items() if len(found) == 1}


def _shingles(tokens: tuple[str, ...]) -> set[tuple[str, ...]]:
    if len(tokens) < _SHINGLE:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i + _SHINGLE]) for i in range(len(tokens) - _SHINGLE + 1)}


def similarity(left: Entity, right: Entity) -> float:
    """Jaccard overlap of token shingles. Deterministic, no model involved."""
    a, b = _shingles(left.tokens), _shingles(right.tokens)
    if not a or not b:
        return 1.0 if a == b else 0.0
    return len(a & b) / len(a | b)


def _pair_up(removed: dict, added: dict, key, guard=lambda e: True) -> list[EntityChange]:
    """Consume unambiguous matches from ``removed``/``added``, returning the pairs.

    Both dicts are mutated: anything matched here is no longer an add or a delete.
    """
    matched: list[EntityChange] = []
    sources = _unique_by((e for e in removed.values() if guard(e)), key)
    targets = _unique_by((e for e in added.values() if guard(e)), key)

    for hash_value, target in targets.items():
        source = sources.get(hash_value)
        if source is None or source.kind is not target.kind:
            continue
        kind = ChangeKind.MOVED if source.name == target.name else ChangeKind.RENAMED
        matched.append(EntityChange(
            kind, target, source,
            signature_changed=source.signature_hash != target.signature_hash,
            body_changed=source.content_hash != target.content_hash,
            similarity=1.0,
        ))
        added.pop(target.id, None)
        removed.pop(source.id, None)

    return matched


def reconcile_moves(changes: list[EntityChange]) -> list[EntityChange]:
    """Second pass across file boundaries.

    ``diff_trees`` only sees one file at a time, so extracting a helper into a
    new module looks like a delete plus an unrelated add -- precisely the noise
    gita exists to remove. Matching is hash-only here: fuzzy scoring across
    every file pair is quadratic, and a cross-file guess is a bad trade.
    """
    added = {c.current.id: c.current for c in changes
             if c.kind is ChangeKind.ADDED and c.current is not None
             and not c.current.synthetic}
    removed = {c.previous.id: c.previous for c in changes
               if c.kind is ChangeKind.REMOVED and c.previous is not None
               and not c.previous.synthetic}
    if not added or not removed:
        return changes

    matched = _pair_up(removed, added, lambda e: e.content_hash)
    matched += _pair_up(removed, added, lambda e: e.body_hash,
                        guard=lambda e: e.body_size >= MIN_BODY_TOKENS)
    if not matched:
        return changes

    consumed = {m.current.id for m in matched} | {m.previous.id for m in matched}
    kept = [
        c for c in changes
        if not (c.kind is ChangeKind.ADDED and c.current.id in consumed)
        and not (c.kind is ChangeKind.REMOVED and c.previous.id in consumed)
    ]
    return _sorted(kept + matched)


_ORDINAL = re.compile(r"#\d+$")


def _slot_group(entity_id: str) -> str:
    """The id with its position stripped: what it is, not which slot it sits in."""
    return _ORDINAL.sub("", entity_id)


def _resolve_group(sources: list[Entity], targets: list[Entity]) -> list[EntityChange]:
    """Decide a set of same-named siblings together.

    `url`, `url#2`, `url#3` are slots, not identities. Inserting one member
    shifts every later one, so matching by id reports untouched members as
    edited. Content is matched first, then equal ids, then whatever is left in
    order -- so a genuine edit is still an edit.
    """
    changes: list[EntityChange] = []
    pool = list(sources)
    pending: list[Entity] = []

    for target in targets:
        moved = next((i for i, s in enumerate(pool)
                      if s.raw_hash == target.raw_hash and s.kind is target.kind), None)
        if moved is None:
            pending.append(target)
        else:
            changes.append(EntityChange(ChangeKind.UNCHANGED, target, pool.pop(moved)))

    for target in list(pending):
        same = next((i for i, s in enumerate(pool) if s.id == target.id), None)
        if same is not None:
            changes.append(_classify_matched(pool.pop(same), target))
            pending.remove(target)

    while pending and pool:
        changes.append(_classify_matched(pool.pop(0), pending.pop(0)))

    changes += [EntityChange(ChangeKind.ADDED, current=t) for t in pending]
    changes += [EntityChange(ChangeKind.REMOVED, previous=s) for s in pool]
    return changes


def _ordinal_groups(old_entities: dict, new_entities: dict) -> dict[str, tuple[list, list]]:
    """Sibling groups where more than one entity shares a name."""
    old_groups: dict[str, list[Entity]] = defaultdict(list)
    new_groups: dict[str, list[Entity]] = defaultdict(list)
    for entity in old_entities.values():
        old_groups[_slot_group(entity.id)].append(entity)
    for entity in new_entities.values():
        new_groups[_slot_group(entity.id)].append(entity)

    groups = {}
    for base in old_groups.keys() | new_groups.keys():
        sources, targets = old_groups.get(base, []), new_groups.get(base, [])
        if len(sources) > 1 or len(targets) > 1:
            groups[base] = (sorted(sources, key=lambda e: e.start_line),
                            sorted(targets, key=lambda e: e.start_line))
    return groups


def _sorted(changes: list[EntityChange]) -> list[EntityChange]:
    return sorted(changes, key=lambda c: (c.entity.path, c.entity.start_line, c.entity.id))


def _classify_matched(previous: Entity, current: Entity) -> EntityChange:
    if previous.raw_hash == current.raw_hash:
        return EntityChange(ChangeKind.UNCHANGED, current, previous)

    signature_changed = previous.signature_hash != current.signature_hash
    body_changed = previous.content_hash != current.content_hash

    if not body_changed:
        # bytes moved but the normalised token stream did not: formatting or comments
        return EntityChange(ChangeKind.COSMETIC, current, previous)

    kind = ChangeKind.SIGNATURE_CHANGED if signature_changed else ChangeKind.BODY_CHANGED
    return EntityChange(kind, current, previous,
                        signature_changed=signature_changed, body_changed=True)


def diff_trees(previous: EntityTree | None, current: EntityTree | None,
               rename_threshold: float = RENAME_THRESHOLD) -> list[EntityChange]:
    """Compare two revisions of one file."""
    if previous is None and current is None:
        return []
    if previous is None:
        return [EntityChange(ChangeKind.ADDED, current=e) for e in current.walk()]
    if current is None:
        return [EntityChange(ChangeKind.REMOVED, previous=e) for e in previous.walk()]

    old_entities = {e.id: e for e in previous.walk()}
    new_entities = {e.id: e for e in current.walk()}

    changes: list[EntityChange] = []

    # pass 0 -- same-named siblings, decided together and by content first.
    # `url`, `url#2`, `url#3` are slots: inserting one member shifts the rest,
    # and matching by id would report the untouched ones as edited.
    groups = _ordinal_groups(old_entities, new_entities)
    handled: set[str] = set()
    for sources, targets in groups.values():
        changes += _resolve_group(sources, targets)
        handled.update(e.id for e in sources)
        handled.update(e.id for e in targets)

    # pass 1 -- stable id
    for entity_id in old_entities.keys() & new_entities.keys():
        if entity_id in handled:
            continue
        changes.append(_classify_matched(old_entities[entity_id], new_entities[entity_id]))

    # A module is bound to its file; git handles file renames, so it never
    # participates in move or rename matching.
    removed = {i: e for i, e in old_entities.items()
               if i not in new_entities and i not in handled and not e.synthetic}
    added = {i: e for i, e in new_entities.items()
             if i not in old_entities and i not in handled and not e.synthetic}

    # pass 2 -- identical content, then identical body, matched only where
    # the hash is unambiguous on both sides
    changes += _pair_up(removed, added, lambda e: e.content_hash)
    changes += _pair_up(removed, added, lambda e: e.body_hash,
                        guard=lambda e: e.body_size >= MIN_BODY_TOKENS)

    # pass 3 -- near-identical body under a new name
    for entity_id, entity in list(added.items()):
        best_score, best_source = 0.0, None
        for source in removed.values():
            if source.kind is not entity.kind:
                continue
            score = similarity(source, entity)
            if score > best_score:
                best_score, best_source = score, source

        if best_source is None or best_score < rename_threshold:
            continue

        changes.append(EntityChange(
            ChangeKind.RENAMED, entity, best_source,
            signature_changed=best_source.signature_hash != entity.signature_hash,
            body_changed=best_source.content_hash != entity.content_hash,
            similarity=best_score,
        ))
        added.pop(entity_id)
        removed.pop(best_source.id)

    changes.extend(EntityChange(ChangeKind.ADDED, current=e) for e in added.values())
    changes.extend(EntityChange(ChangeKind.REMOVED, previous=e) for e in removed.values())

    return _sorted(changes)


def diff_files(files, rename_threshold: float = RENAME_THRESHOLD) -> ChangeSet:
    """Aggregate ``(previous_tree, current_tree)`` pairs into one ChangeSet."""
    changeset = ChangeSet()
    collected: list[EntityChange] = []
    for previous, current in files:
        changeset.files_changed += 1
        for tree in (previous, current):
            if tree is not None and tree.parse_error:
                changeset.parse_errors += 1
                break
        collected += diff_trees(previous, current, rename_threshold)

    changeset.extend(reconcile_moves(collected))
    return changeset
