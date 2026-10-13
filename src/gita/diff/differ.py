"""EntityTree x EntityTree -> ChangeSet.

Matching runs in three passes, cheapest and most certain first:

1. by matched parent and sibling content -- ordinals are positions, not identities
2. by content hash   -- identical body somewhere else: moved or renamed
3. by similarity     -- near-identical body: renamed and edited

Unmatched entities remain added or removed; this does not prove absent continuity.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from ..entities.model import Entity, EntityKind, EntityTree, digest
from .changes import ChangeKind, ChangeSet, EntityChange

#: Jaccard floor for calling two entities the same thing under a new name.
RENAME_THRESHOLD = 0.6

#: Bodies smaller than this are too generic to match on hash alone -- a dozen
#: stubs may all be `return None`.
MIN_BODY_TOKENS = 3

_SHINGLE = 3
_RENAME_MARGIN = 0.1
_ORDERED_BODY_THRESHOLD = 0.8
_MIN_EDITED_BODY_TOKENS = 20


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
    """Jaccard overlap of own-token shingles, including the signature."""
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


def _resolve_group(sources: list[Entity], targets: list[Entity],
                   old_hashes: dict[str, str], new_hashes: dict[str, str]) -> list[EntityChange]:
    """Decide a set of same-named siblings together.

    `url`, `url#2`, `url#3` are slots, not identities. Inserting one member
    shifts every later one, so matching by id reports untouched members as
    edited. Match complete subtrees before own content or sibling slots, so
    nested callbacks and checkout line endings cannot steal an unchanged peer.
    """
    changes: list[EntityChange] = []
    pool = list(sources)
    pending: list[Entity] = []

    for target in targets:
        moved = next((i for i, s in enumerate(pool)
                      if old_hashes[s.id] == new_hashes[target.id]), None)
        if moved is None:
            pending.append(target)
        else:
            changes.append(_classify_matched(pool.pop(moved), target))

    sources_by_hash = _unique_by(pool, lambda e: e.content_hash)
    targets_by_hash = _unique_by(pending, lambda e: e.content_hash)
    for key in sources_by_hash.keys() & targets_by_hash.keys():
        source, target = sources_by_hash[key], targets_by_hash[key]
        changes.append(_classify_matched(source, target))
        pool.remove(source)
        pending.remove(target)

    for target in list(pending):
        slot = target.id.removeprefix(f"{target.parent_id}::")
        same = next((i for i, s in enumerate(pool)
                     if s.id.removeprefix(f"{s.parent_id}::") == slot), None)
        if same is not None:
            changes.append(_classify_matched(pool.pop(same), target))
            pending.remove(target)

    while pending and pool:
        changes.append(_classify_matched(pool.pop(0), pending.pop(0)))

    changes += [EntityChange(ChangeKind.ADDED, current=t) for t in pending]
    changes += [EntityChange(ChangeKind.REMOVED, previous=s) for s in pool]
    return changes


def _subtree_hashes(tree: EntityTree) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for entity in reversed(list(tree.walk())):
        hashes[entity.id] = digest(" ".join(
            [entity.content_hash, *(hashes[child] for child in entity.children)]))
    return hashes


def _child_groups(tree: EntityTree, parent: Entity) -> dict[tuple[EntityKind, str], list[Entity]]:
    groups: dict[tuple[EntityKind, str], list[Entity]] = defaultdict(list)
    for child_id in parent.children:
        child = tree.entities[child_id]
        groups[child.kind, child.name].append(child)
    return groups


def _rename_score(source: Entity, target: Entity, parents: dict[str, str]) -> float:
    score = similarity(source, target)
    if (source.kind not in (EntityKind.FUNCTION, EntityKind.METHOD)
            or source.parent_id is None
            or parents.get(source.parent_id) != target.parent_id
            or min(len(source.body_tokens), len(target.body_tokens)) < _MIN_EDITED_BODY_TOKENS):
        return score
    if source.signature_shape is None or source.signature_shape != target.signature_shape:
        return score
    ordered = min(SequenceMatcher(None, left, right, autojunk=False).ratio()
                  for left, right in ((source.body_tokens, target.body_tokens),
                                      (target.body_tokens, source.body_tokens)))
    return max(score, ordered) if ordered >= _ORDERED_BODY_THRESHOLD else score


def _pair_similar(removed: dict[str, Entity], added: dict[str, Entity],
                  parents: dict[str, str], threshold: float) -> list[EntityChange]:
    sources: dict[str, list[tuple[float, Entity]]] = defaultdict(list)
    targets: dict[str, list[tuple[float, Entity]]] = defaultdict(list)

    def consider(groups, key, score, entity):
        candidates = groups[key]
        candidates.append((score, entity))
        candidates.sort(key=lambda item: (-item[0], item[1].id))
        del candidates[2:]

    for source in removed.values():
        for target in added.values():
            if source.kind is not target.kind:
                continue
            score = _rename_score(source, target, parents)
            consider(sources, source.id, score, target)
            consider(targets, target.id, score, source)

    def best(ranked):
        if (not ranked or ranked[0][0] < threshold
                or (len(ranked) > 1 and ranked[0][0] < ranked[1][0] + _RENAME_MARGIN)):
            return None
        return ranked[0]

    source_choices = {key: best(values) for key, values in sources.items()}
    matched = []
    for target_id, candidates in targets.items():
        chosen = best(candidates)
        if chosen is None:
            continue
        score, source = chosen
        reciprocal = source_choices[source.id]
        if reciprocal is None or reciprocal[1].id != target_id:
            continue
        target = added.pop(target_id)
        removed.pop(source.id)
        matched.append(EntityChange(
            ChangeKind.MOVED if source.name == target.name else ChangeKind.RENAMED,
            target, source, signature_changed=source.signature_hash != target.signature_hash,
            body_changed=source.content_hash != target.content_hash, similarity=score,
        ))
    return matched


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
    old_hashes, new_hashes = _subtree_hashes(previous), _subtree_hashes(current)
    if previous.root_id == current.root_id:
        assert previous.root is not None and current.root is not None
        changes.append(_classify_matched(previous.root, current.root))
    # Grow parent correspondence top-down; a child's old ordinal path is not an identity.
    for match in changes:
        assert match.previous is not None and match.current is not None
        old_groups = _child_groups(previous, match.previous)
        new_groups = _child_groups(current, match.current)
        for key in old_groups.keys() & new_groups.keys():
            paired = _resolve_group(old_groups[key], new_groups[key], old_hashes, new_hashes)
            changes.extend(c for c in paired if c.previous is not None and c.current is not None)
    old_handled = {c.previous.id for c in changes}
    new_handled = {c.current.id for c in changes}

    # A module is bound to its file; git handles file renames, so it never
    # participates in move or rename matching.
    removed = {i: e for i, e in old_entities.items()
               if i not in old_handled and not e.synthetic}
    added = {i: e for i, e in new_entities.items()
             if i not in new_handled and not e.synthetic}

    # pass 2 -- identical content, then identical body, matched only where
    # the hash is unambiguous on both sides
    changes += _pair_up(removed, added, lambda e: e.content_hash)
    changes += _pair_up(removed, added, lambda e: e.body_hash,
                        guard=lambda e: e.body_size >= MIN_BODY_TOKENS)

    # Fuzzy continuation requires a mutual winner, not whichever target is visited first.
    parents = {c.previous.id: c.current.id for c in changes
               if c.previous is not None and c.current is not None}
    changes += _pair_similar(removed, added, parents, rename_threshold)

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
