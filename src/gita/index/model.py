"""Immutable evidence records and canonical identifiers for the entity index."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from hashlib import blake2b
import re
from typing import TypeAlias


CanonicalValue: TypeAlias = bytes | str | int | None | tuple["CanonicalValue", ...]

_DOMAINS = frozenset({
    "par1", "ver1", "def1", "occ1", "ctx1", "eid1", "oeid1", "lnk1",
    "evt1", "bnd1", "evd1",
})
_ID_PATTERN = re.compile(r"([a-z]+1)_([0-9a-f]{64})")
_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_BOUNDARIES = frozenset({"root", "bounded", "shallow", "missing_parent"})
_GENESIS_REASONS = frozenset({"root", "observed_addition_with_complete_predecessor"})
_OBSERVED_REASONS = frozenset({
    "bounded", "shallow", "missing_parent", "parse_gap", "ambiguous", "unmatched",
    "kind_changed", "side_parent_origin_unresolved",
})
_ACCEPTED_METHODS = frozenset({
    "structural_locator", "exact_own", "exact_body", "exact_subtree", "exact_blob",
})
_MATERIAL_FLAGS = frozenset({
    "added", "removed", "renamed", "moved", "signature_changed", "body_changed",
    "own_content_changed",
})
_EVENT_FLAGS = _MATERIAL_FLAGS | frozenset({
    "cosmetic", "descendant_changed", "lineage_gap", "observed",
    "representation_unknown", "mode_changed",
})


class IndexContractError(ValueError):
    """A typed validation or integrity failure in the index contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IndexContractError("invalid_record", message)


def _uint64(value: int) -> None:
    _require(type(value) is int and 0 <= value < 1 << 64, "expected uint64, excluding bool")


@lru_cache(maxsize=8192)
def _short_utf8(value: str) -> bytes:
    return value.encode("utf-8", errors="strict")


def _text(value: str, *, nonempty: bool = True) -> bytes:
    _require(type(value) is str, "expected strict UTF-8 text")
    _require(not nonempty or bool(value), "text must not be empty")
    try:
        return _short_utf8(value) if len(value) <= 128 else value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise IndexContractError("invalid_record", "text is not strict UTF-8") from error


def _digest(value: bytes) -> None:
    _require(type(value) is bytes and len(value) == 32, "expected a 32-byte digest")


def _tuple(value: tuple[object, ...]) -> None:
    _require(type(value) is tuple, "expected an immutable tuple")


def _identifier(value: str, prefixes: tuple[str, ...]) -> None:
    _text(value)
    match = _ID_PATTERN.fullmatch(value)
    _require(match is not None and match.group(1) in prefixes, "invalid typed content identifier")


def _path(value: bytes) -> None:
    _require(type(value) is bytes, "Git paths must be raw bytes")
    _require(b"\0" not in value, "Git paths cannot contain NUL")
    _require(
        all(component not in (b"", b".", b"..") for component in value.split(b"/")),
        "Git paths must be relative with nonempty, non-dot components",
    )


@lru_cache(maxsize=8192)
def _encode_short_text(value: str) -> bytes:
    encoded = _text(value, nonempty=False)
    return b"\x01t" + len(encoded).to_bytes(8, "big") + encoded


def _encode(value: CanonicalValue, depth: int) -> bytes:
    if value is None:
        return b"\x00"
    if type(value) is bytes:
        return b"\x01b" + len(value).to_bytes(8, "big") + value
    if type(value) is str:
        if len(value) <= 128:
            return _encode_short_text(value)
        encoded = _text(value, nonempty=False)
        return b"\x01t" + len(encoded).to_bytes(8, "big") + encoded
    if type(value) is int:
        _uint64(value)
        return b"\x01u" + value.to_bytes(8, "big")
    if type(value) is tuple:
        _require(depth < 64, "canonical tuples exceed maximum depth 64")
        return (b"\x01l" + len(value).to_bytes(8, "big")
                + b"".join(_encode(item, depth + 1) for item in value))
    raise IndexContractError("invalid_record", "unsupported canonical value type")


def canonical_encode(domain: str, fields: tuple[CanonicalValue, ...]) -> bytes:
    """Encode domain, format version 1, and a typed tuple, without normalization."""
    encoded_domain = _text(domain)
    _require(encoded_domain.isascii() and len(encoded_domain) <= 64, "domain must be 1-64 ASCII bytes")
    _tuple(fields)
    return (len(encoded_domain).to_bytes(8, "big") + encoded_domain
            + (1).to_bytes(8, "big") + _encode(fields, 0))


def content_id(domain: str, fields: tuple[CanonicalValue, ...]) -> str:
    """Return a full, domain-separated BLAKE2b-256 identifier."""
    _text(domain)
    _require(domain in _DOMAINS, "unsupported content identifier domain")
    return domain + "_" + blake2b(canonical_encode(domain, fields), digest_size=32).hexdigest()


def require_same_payload(existing: bytes, incoming: bytes) -> None:
    """Refuse unequal canonical payloads sharing a digest key."""
    _require(type(existing) is bytes and type(incoming) is bytes, "payloads must be bytes")
    if existing != incoming:
        raise IndexContractError("digest_collision", "different payloads share a content identifier")


def _representation(value: tuple[str, ...] | None) -> None:
    if value is not None:
        _tuple(value)
        for token in value:
            _text(token, nonempty=False)


def _named_pairs(values: tuple[tuple[str, CanonicalValue], ...], *, integers: bool = False) -> None:
    """Validate sorted unique ASCII keys and values at their record nesting depth."""
    _tuple(values)
    previous = ""
    for pair in values:
        _tuple(pair)
        _require(len(pair) == 2, "named entries must be pairs")
        key, value = pair
        _text(key)
        _require(_KEY_PATTERN.fullmatch(key) is not None, "expected a stable ASCII key")
        _require(key > previous, "named pairs must have sorted unique keys")
        if integers:
            _uint64(value)
        else:
            _encode(value, 3)
        previous = key


class ParseStatus(str, Enum):
    """Outcomes determined only from available source bytes and parse policy."""

    COMPLETE = "complete"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    PARSE_ERROR = "parse_error"
    OVERSIZED = "oversized"
    DEFINITION_LIMIT = "definition_limit"
    REPRESENTATION_LIMIT = "representation_limit"


#: Deliberate exclusions: a file the parse policy will never cover. A LICENSE
#: has no grammar and never will, so this is a settled answer about that file,
#: not an absence of one.
EXCLUDED_EXTRACTION = ("unsupported_language", "non_source_object")

#: Extraction outcomes that cannot break entity continuity. Everything else --
#: parse errors, missing objects, oversized files, definition and representation
#: limits -- means evidence really is missing, and an entity may be reported as
#: removed and added rather than changed.
SETTLED_EXTRACTION = (ParseStatus.COMPLETE.value, "removed") + EXCLUDED_EXTRACTION

#: Categories recording work deferred by policy rather than evidence missing.
#: Cached parses are reused without rechecking every old object's availability;
#: `source()` verifies bytes on demand, so this cannot orphan a lineage.
DEFERRED_COVERAGE = ("source_availability",)


def _quoted(values) -> str:
    return ", ".join(f"'{value}'" for value in values)


#: True for a `coverage` row that can actually cost a reader entity continuity.
#:
#: Deliberately narrower than `UNCOVERED_SQL`. Both numbers are real and they
#: answer different questions: "is every file in this tree indexed" is not the
#: same as "could this index report an edit as a delete plus an add". Indexing
#: gita itself produced 27 uncovered records -- 15 unsupported files, 10
#: unchecked cached sources and 2 real omissions -- and reporting all 27 as gaps
#: told the reader to rebuild an index that could never improve.
GAP_SQL = (
    "(CASE WHEN category = 'extraction' "
    f"THEN reason NOT IN ({_quoted(SETTLED_EXTRACTION)}) "
    f"WHEN category IN ({_quoted(DEFERRED_COVERAGE)}) THEN 0 ELSE 1 END)"
)

#: True for a `coverage` row meaning some part of the tree is not indexed.
#:
#: This is what `status` reports, and an unsupported file belongs in it: the
#: index genuinely does not cover that file. It just cannot break a lineage,
#: which is why matching ignores it and `GAP_SQL` excludes it.
UNCOVERED_SQL = (
    f"(category != 'extraction' OR reason NOT IN ({_quoted((ParseStatus.COMPLETE.value, 'removed'))}))"
)

#: True for a `coverage` row that records a deliberate exclusion.
EXCLUDED_SQL = (
    f"(category = 'extraction' AND reason IN ({_quoted(EXCLUDED_EXTRACTION)}))"
)


class LinkStatus(str, Enum):
    """Stable decision statuses; only accepted links establish continuity."""
    ACCEPTED = "accepted"
    CANDIDATE = "candidate"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


class LifeCycle(str, Enum):
    """Evidence-qualified historical presence, distinct from parse outcomes."""

    PRESENT = "present"
    REMOVED = "removed"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class GitOid:
    """An object-format-qualified raw Git digest."""

    object_format: str
    digest: bytes

    def __post_init__(self) -> None:
        _text(self.object_format)
        _require(self.object_format in ("sha1", "sha256"), "unsupported Git object format")
        size = 20 if self.object_format == "sha1" else 32
        _require(type(self.digest) is bytes and len(self.digest) == size, "invalid Git digest length or type")

    @classmethod
    def from_hex(cls, object_format: str, text: str) -> GitOid:
        """Accept exact-length hexadecimal text, including uppercase, without whitespace."""
        _text(object_format)
        _require(object_format in ("sha1", "sha256"), "unsupported Git object format")
        _text(text)
        size = 40 if object_format == "sha1" else 64
        _require(len(text) == size and re.fullmatch(r"[0-9a-fA-F]+", text) is not None, "invalid Git hex digest")
        return cls(object_format, bytes.fromhex(text))

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.object_format, self.digest)


def _oids(*values: GitOid) -> None:
    _require(all(type(value) is GitOid for value in values), "expected GitOid records")
    _require(len({value.object_format for value in values}) <= 1, "Git object formats must agree")


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Half-open original byte bounds and inclusive 1-based display lines."""

    start_byte: int
    end_byte: int
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        for value in (self.start_byte, self.end_byte, self.start_line, self.end_line):
            _uint64(value)
        _require(self.end_byte >= self.start_byte, "byte bounds are reversed")
        _require(1 <= self.start_line <= self.end_line, "invalid display line bounds")
        _require(self.start_byte != self.end_byte or self.start_line == self.end_line,
                 "empty spans must end on their start line")

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.start_byte, self.end_byte, self.start_line, self.end_line)


@dataclass(frozen=True)
class EntityVersion:
    """Location-free source digest and normalized text tokens; None means unavailable."""

    parse_fingerprint: bytes
    language: str
    kind: str
    source_digest: bytes
    own: tuple[str, ...] | None
    signature: tuple[str, ...] | None
    body: tuple[str, ...] | None
    subtree: tuple[str, ...] | None

    def __post_init__(self) -> None:
        _digest(self.parse_fingerprint)
        _text(self.language)
        _text(self.kind)
        _digest(self.source_digest)
        for value in (self.own, self.signature, self.body, self.subtree):
            _representation(value)

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.parse_fingerprint, self.language, self.kind, self.source_digest,
                self.own, self.signature, self.body, self.subtree)

    @property
    def id(self) -> str:
        current = self.fields()
        cached = self.__dict__.get("_identity_cache")
        if cached is None or cached[0] != current:
            cached = (current, content_id("ver1", current))
            object.__setattr__(self, "_identity_cache", cached)
        return cached[1]


@dataclass(frozen=True, slots=True)
class EntityDefinition:
    """A parse-local definition with literal name components and byte-based identity."""

    parse_id: str
    version_id: str
    span: SourceSpan
    kind: str
    names: tuple[str, ...]
    parent_id: str | None
    discriminator: tuple[int, ...]
    synthetic_root: bool = False

    def __post_init__(self) -> None:
        _identifier(self.parse_id, ("par1",))
        _identifier(self.version_id, ("ver1",))
        _require(type(self.span) is SourceSpan, "expected SourceSpan")
        _text(self.kind)
        _tuple(self.names)
        _require(bool(self.names), "names must contain at least one component")
        for name in self.names:
            _text(name)
        if self.parent_id is not None:
            _identifier(self.parent_id, ("def1",))
        _require(type(self.synthetic_root) is bool, "synthetic_root must be bool")
        _require(not self.synthetic_root or self.parent_id is None,
                 "synthetic roots cannot have a parent")
        _tuple(self.discriminator)
        for ordinal in self.discriminator:
            _uint64(ordinal)

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.parse_id, self.version_id, self.span.start_byte, self.span.end_byte,
                self.kind, self.names, self.parent_id, self.discriminator)

    @property
    def id(self) -> str:
        return content_id("def1", self.fields())


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Complete definitions or unknown children, addressed by a parse cache key."""

    parse_fingerprint: bytes
    blob: GitOid
    path: bytes
    language: str | None
    status: ParseStatus
    byte_length: int
    definitions: tuple[EntityDefinition, ...]
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _digest(self.parse_fingerprint)
        _oids(self.blob)
        _path(self.path)
        _require(type(self.status) is ParseStatus, "expected ParseStatus")
        _uint64(self.byte_length)
        _tuple(self.definitions)
        _tuple(self.diagnostics)
        for diagnostic in self.diagnostics:
            _text(diagnostic)
        if self.language is not None:
            _text(self.language)
        if self.complete:
            _require(self.language is not None, "complete parses require a language")
        else:
            _require(not self.definitions, "incomplete parses cannot publish definitions")
            _require(bool(self.diagnostics), "incomplete parses require diagnostics")

        parse_id = self.id
        definitions_by_id: dict[str, EntityDefinition] = {}
        for definition in self.definitions:
            _require(type(definition) is EntityDefinition, "expected EntityDefinition")
            _require(definition.parse_id == parse_id, "definition belongs to another parse")
            _require(definition.span.end_byte <= self.byte_length, "definition exceeds source length")
            definition_id = definition.id
            _require(definition_id not in definitions_by_id, "duplicate definition identifier")
            definitions_by_id[definition_id] = definition
        for definition in self.definitions:
            if definition.parent_id is None:
                continue
            _require(definition.parent_id in definitions_by_id, "definition parent is missing")
            parent = definitions_by_id[definition.parent_id]
            _require(
                parent.span.start_byte <= definition.span.start_byte
                and definition.span.end_byte <= parent.span.end_byte
                and parent.span.start_line <= definition.span.start_line
                and definition.span.end_line <= parent.span.end_line,
                "parent must enclose child byte and line bounds",
            )

    def key_fields(self) -> tuple[CanonicalValue, ...]:
        """Return only parse identity: fingerprint, qualified blob, and literal path."""
        return (self.parse_fingerprint, self.blob.fields(), self.path)

    def fields(self) -> tuple[CanonicalValue, ...]:
        """Return the full collision-check payload, including outcome and display lines."""
        definitions = tuple(
            (definition.parse_id, definition.version_id, definition.span.fields(),
             definition.kind, definition.names, definition.parent_id, definition.discriminator,
             int(definition.synthetic_root))
            for definition in self.definitions
        )
        return self.key_fields() + (
            self.language, self.status.value, self.byte_length, definitions, self.diagnostics,
        )

    @property
    def id(self) -> str:
        """Hash key_fields(), not the source-dependent outcome in fields()."""
        return content_id("par1", self.key_fields())

    @property
    def complete(self) -> bool:
        """Whether definitions are authoritative, including an empty file."""
        return self.status is ParseStatus.COMPLETE


@dataclass(frozen=True, slots=True)
class PageCounts:
    """Exact page counts, or jointly unknown total and omitted populations."""

    total: int | None
    shown: int
    omitted: int | None

    def __post_init__(self) -> None:
        _uint64(self.shown)
        if self.total is None:
            _require(self.omitted is None, "unknown total requires unknown omitted count")
        else:
            _uint64(self.total)
            _uint64(self.omitted)
            _require(self.total == self.shown + self.omitted, "total must equal shown plus omitted")


@dataclass(frozen=True, slots=True)
class EntityOccurrence:
    """A definition situated at an exact commit and literal Git tree entry."""

    commit: GitOid
    path: bytes
    blob: GitOid
    object_type: str
    mode: int
    definition_id: str

    def __post_init__(self) -> None:
        _oids(self.commit, self.blob)
        _path(self.path)
        _text(self.object_type)
        _require(self.object_type in ("blob", "tree", "commit", "tag"), "invalid Git object type")
        _uint64(self.mode)
        _identifier(self.definition_id, ("def1",))

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.commit.fields(), self.path, self.blob.fields(), self.object_type,
                self.mode, self.definition_id)

    @property
    def id(self) -> str:
        return content_id("occ1", self.fields())


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """Boundary and interpretation provenance, excluding local scope metadata."""

    anchor: GitOid
    boundary: str
    parents: tuple[GitOid, ...]
    parse_fingerprint: bytes
    match_fingerprint: bytes
    limits: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        _tuple(self.parents)
        _oids(self.anchor, *self.parents)
        _text(self.boundary)
        _require(self.boundary in _BOUNDARIES, "invalid context boundary")
        _digest(self.parse_fingerprint)
        _digest(self.match_fingerprint)
        _named_pairs(self.limits, integers=True)

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.anchor.fields(), self.boundary, tuple(parent.fields() for parent in self.parents),
                self.parse_fingerprint, self.match_fingerprint, self.limits)

    @property
    def id(self) -> str:
        return content_id("ctx1", self.fields())


@dataclass(frozen=True, slots=True)
class LineageSeed:
    """A known genesis or a separately addressed, evidence-qualified observation."""

    context_id: str
    commit: GitOid
    occurrence_id: str
    reason: str
    evidence_digest: bytes | None = None

    def __post_init__(self) -> None:
        _identifier(self.context_id, ("ctx1",))
        _oids(self.commit)
        _identifier(self.occurrence_id, ("occ1",))
        _text(self.reason)
        if self.reason in _GENESIS_REASONS:
            _require(self.evidence_digest is None, "genesis seeds cannot carry ignored evidence")
        else:
            _require(self.reason in _OBSERVED_REASONS, "invalid observed seed reason")
            _digest(self.evidence_digest)

    def fields(self) -> tuple[CanonicalValue, ...]:
        base = (self.context_id, self.commit.fields(), self.occurrence_id, self.reason)
        return base if self.reason in _GENESIS_REASONS else base + (self.evidence_digest,)

    @property
    def id(self) -> str:
        domain = "eid1" if self.reason in _GENESIS_REASONS else "oeid1"
        return content_id(domain, self.fields())


@dataclass(frozen=True, slots=True)
class LineageLink:
    """An explicit edge decision; similarity is never accepted by strict-v1."""

    context_id: str
    parent: GitOid
    commit: GitOid
    source: str
    destination: str
    status: LinkStatus
    method: str
    reason: str
    evidence: tuple[tuple[str, CanonicalValue], ...]

    def __post_init__(self) -> None:
        _identifier(self.context_id, ("ctx1",))
        _oids(self.parent, self.commit)
        _identifier(self.source, ("occ1",))
        _identifier(self.destination, ("occ1",))
        _require(type(self.status) is LinkStatus, "expected LinkStatus")
        _text(self.method)
        _require(self.method in _ACCEPTED_METHODS or self.method == "similarity", "unknown link method")
        _text(self.reason)
        _named_pairs(self.evidence)
        if self.status is LinkStatus.ACCEPTED:
            _require(self.method in _ACCEPTED_METHODS, "similarity cannot establish accepted lineage")
            _require(bool(self.evidence), "accepted links require evidence")

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.context_id, self.parent.fields(), self.commit.fields(), self.source,
                self.destination, self.status.value, self.method, self.reason, self.evidence)

    @property
    def id(self) -> str:
        return content_id("lnk1", self.fields())


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    """Independent transition flags with canonical ordering and derived materiality."""

    context_id: str
    eid: str
    parent: GitOid | None
    commit: GitOid
    source: str | None
    destination: str | None
    flags: tuple[str, ...]
    decision_id: str | None

    def __post_init__(self) -> None:
        _identifier(self.context_id, ("ctx1",))
        _identifier(self.eid, ("eid1", "oeid1"))
        _oids(self.commit)
        if self.parent is not None:
            _oids(self.parent, self.commit)
        _require(self.source is not None or self.destination is not None, "events need at least one endpoint")
        for endpoint in (self.source, self.destination):
            if endpoint is not None:
                _identifier(endpoint, ("occ1",))
        if self.decision_id is not None:
            _identifier(self.decision_id, ("lnk1",))
        _tuple(self.flags)
        _require(bool(self.flags), "events need at least one flag")
        for flag in self.flags:
            _text(flag)
            _require(flag in _EVENT_FLAGS, "unknown event flag")
        _require(len(set(self.flags)) == len(self.flags), "event flags must be unique")

    def fields(self) -> tuple[CanonicalValue, ...]:
        return (self.context_id, self.eid, None if self.parent is None else self.parent.fields(),
                self.commit.fields(), self.source, self.destination, tuple(sorted(self.flags)), self.decision_id)

    @property
    def id(self) -> str:
        return content_id("evt1", self.fields())

    @property
    def material(self) -> bool:
        return any(flag in _MATERIAL_FLAGS for flag in self.flags)