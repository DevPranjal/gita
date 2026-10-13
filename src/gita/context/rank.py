"""Ranking: which changes an agent should see first.

Deterministic weights only. WS-2 will add blast radius as another signal here,
and WS-3 may reorder within a cluster, but neither may invent facts.

Intent is the second signal. Kind alone answers "what is most severe", which is
not the same question as "what is this agent here for": a body edit to the one
function named in the request outranks an unrelated removal, and by kind alone it
never could. Intent only ever *reorders* -- it cannot hide a change, resurrect
filtered noise, or invent one, so a request that mentions nothing in the diff
degrades to exactly the ordering that existed before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


#: Request scaffolding: words that appear in almost every task description and
#: so separate nothing. Dropping them is what keeps "fix the bug in the parser"
#: from boosting every entity whose name happens to contain `fix`. Domain nouns
#: are deliberately *not* here -- `cache`, `token` and `parser` are the signal.
_STOPWORDS = frozenset("""
a an the and or but if of to in on at for with from by as is are was were be been
being it its this that these those there then than when where which who whom why
how what do does did can could should would will shall may might must not no
i we you they he she my our your their me us them
please make made need needs want use using used work works working
code codes change changes changed file files line lines function functions
method methods class classes module modules test tests
fix fixes fixed fixing add adds added adding update updates updated updating
implement implements implemented implementing refactor refactors refactored
review check checks look looks see find finds show shows tell explain
bug bugs issue issues problem problems error errors failure failures
new old current latest something anything everything thing things
""".split())

#: Weights for where an intent term was found. A leaf name is the strongest
#: evidence the request is about this entity; a path match says only that the
#: request is about the right area. The maxima are set so that a full leaf-name
#: match (80) can lift a body edit (30) above an unrelated removal (100),
#: because that reordering is the whole point, while a path-only match (25)
#: cannot.
INTENT_NAME_WEIGHT = 80.0
INTENT_CONTAINER_WEIGHT = 40.0
INTENT_PATH_WEIGHT = 25.0
INTENT_SIGNATURE_WEIGHT = 12.0

#: Shortest prefix/suffix overlap allowed to count as a match, so `parse`
#: matches `parser` and `parsed` but `id` does not match `identity`.
_MIN_OVERLAP = 4

_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _identifier_tokens(text: str) -> frozenset[str]:
    """Lowercase word tokens of an identifier, split on case and punctuation."""
    if not text:
        return frozenset()
    return frozenset(t for t in _SPLIT.split(_CAMEL.sub(" ", text).lower()) if t)


def _related(term: str, token: str) -> bool:
    """Whether an intent term and an identifier token refer to the same thing."""
    if term == token:
        return True
    shorter, longer = (term, token) if len(term) <= len(token) else (token, term)
    return len(shorter) >= _MIN_OVERLAP and shorter in longer


@dataclass(frozen=True, slots=True)
class Intent:
    """What the caller is trying to do, reduced to matchable terms.

    Built from free text the caller already has -- a task description, an issue
    title, the prompt an agent was handed. It is never inferred from the diff,
    which would be the tool marking its own homework.
    """

    terms: tuple[str, ...] = ()

    @staticmethod
    def parse(text: str | None) -> "Intent":
        if not text:
            return Intent()
        words = _SPLIT.split(_CAMEL.sub(" ", text).lower())
        # Order-preserving dedupe: two mentions of a word are not twice the
        # evidence, but the order is worth keeping for a readable label.
        seen: dict[str, None] = {}
        for word in words:
            if len(word) > 2 and word not in _STOPWORDS:
                seen.setdefault(word, None)
        return Intent(tuple(seen))

    def __bool__(self) -> bool:
        return bool(self.terms)


def intent_bonus(change: EntityChange, intent: Intent | None) -> float:
    """How strongly one change answers the caller's stated intent.

    Each field scores the share of *its own* tokens the request accounts for --
    "does the request name this entity", not "how much of the request does this
    entity cover". The distinction decides real cases: `parse_tokens returns the
    wrong count` identifies `parse_tokens` completely, but only two of its five
    content words could ever match an identifier, and counting the other three
    as misses ranked the named function below an unrelated signature change.
    """
    if not intent:
        return 0.0

    entity = change.entity
    segments = entity.qualname.split("::")
    fields = (
        (INTENT_NAME_WEIGHT, _identifier_tokens(entity.name)),
        (INTENT_CONTAINER_WEIGHT, _identifier_tokens(" ".join(segments[:-1]))),
        (INTENT_PATH_WEIGHT, _identifier_tokens(entity.path)),
        (INTENT_SIGNATURE_WEIGHT, _identifier_tokens(entity.signature or "")),
    )

    bonus = 0.0
    for weight, tokens in fields:
        if not tokens:
            continue
        matched = sum(1 for token in tokens
                      if any(_related(term, token) for term in intent.terms))
        if matched:
            bonus += weight * (matched / len(tokens))
    return bonus


def score_change(change: EntityChange, intent: Intent | None = None) -> float:
    if change.is_noise:
        # Noise stays noise. Intent reorders what survived the filter; letting it
        # revive a whitespace change would make the filter a suggestion.
        return 0.0

    score = float(KIND_WEIGHT.get(change.kind, 10))
    if change.signature_changed and change.kind is not ChangeKind.SIGNATURE_CHANGED:
        score += 15.0

    bonus = intent_bonus(change, intent)
    # "Why is this test failing" is a request about tests. Demoting them by half
    # would bury the one thing that was asked for.
    if is_test_path(change.entity.path) and not bonus:
        score *= TEST_WEIGHT
    return score + bonus
