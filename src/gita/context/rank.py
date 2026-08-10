"""Ranking: which changes an agent should see first.

Deterministic weights only. WS-2 will add blast radius as another signal here,
and WS-3 may reorder within a cluster, but neither may invent facts.
"""

from __future__ import annotations

import re

from ..diff.changes import ChangeKind, EntityChange

#: Interface breakage outranks behaviour change, which outranks relocation.
KIND_WEIGHT: dict[ChangeKind, int] = {
    ChangeKind.REMOVED: 100,
    ChangeKind.SIGNATURE_CHANGED: 90,
    ChangeKind.RENAMED: 70,
    ChangeKind.ADDED: 60,
    ChangeKind.MOVED: 40,
    ChangeKind.BODY_CHANGED: 30,
    ChangeKind.COSMETIC: 0,
    ChangeKind.UNCHANGED: 0,
}

#: Test churn is real but rarely what an agent needs first.
TEST_WEIGHT = 0.5

_TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|__tests__|testdata)(/|$)|(^|/)(test_[^/]+|[^/]+[._](test|spec))\.[^/]+$",
    re.IGNORECASE,
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path.replace("\\", "/")))


def score_change(change: EntityChange) -> float:
    if change.is_noise:
        return 0.0

    score = float(KIND_WEIGHT.get(change.kind, 10))
    if change.signature_changed and change.kind is not ChangeKind.SIGNATURE_CHANGED:
        score += 15.0
    if is_test_path(change.entity.path):
        score *= TEST_WEIGHT
    return score
