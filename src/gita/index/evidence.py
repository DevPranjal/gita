"""Byte-accurate, synchronous evidence for one file, independent of legacy records.

Limits bound input, definitions, and retained token bytes, not native parse time.
Whitespace/comment normalization is not proof of behavioral equivalence (notably
for indentation). SQL has no byte adapter here; no grammar downloads are allowed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from hashlib import blake2b, file_digest, new as new_hash
from importlib.metadata import distribution
from pathlib import Path

from tree_sitter import Node
import tree_sitter_language_pack as language_pack
from tree_sitter_language_pack import get_parser

from gita.entities import extractor, languages
from gita.entities.languages import LanguageSpec

from . import model
from .model import (
    EntityDefinition, EntityVersion, GitOid, IndexContractError, ParseResult,
    ParseStatus, SourceSpan, canonical_encode, content_id,
)


ADAPTER_VERSION = 1
_NATIVE_SUFFIXES = frozenset({".so", ".pyd", ".dll", ".dylib"})
_STRING_NODES = frozenset({
    "string", "string_literal", "raw_string_literal", "char_literal",
    "interpreted_string_literal", "raw_string", "template_string",
})
_INTERPOLATION_NODES = frozenset({"interpolation", "template_substitution"})


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    """Per-file policy limits, including the synthetic root and unique versions."""

    max_blob_bytes: int = 5 * 1024 * 1024
    max_definitions: int = 50000
    max_representation_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        for value in (self.max_blob_bytes, self.max_definitions,
                      self.max_representation_bytes):
            if type(value) is not int or not 0 <= value < 1 << 64:
                raise IndexContractError("invalid_limits", "limits must be uint64, excluding bool")


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """A parse result and its first-seen, unique, location-free versions."""

    result: ParseResult
    versions: tuple[EntityVersion, ...]


def _validate_limits(limits: EvidenceLimits) -> None:
    if type(limits) is not EvidenceLimits:
        raise IndexContractError("invalid_limits", "expected EvidenceLimits")


@lru_cache(maxsize=2048)
def _cached_digest(path: Path, size: int, mtime_ns: int) -> bytes:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").digest()


def _artifact_digest(path: Path) -> bytes:
    stat = path.stat()
    return _cached_digest(path, stat.st_size, stat.st_mtime_ns)


def _artifacts() -> tuple:
    """Hash installed binding code and cached grammars, never their absolute paths."""
    artifacts = []
    try:
        for package_name in ("tree-sitter", "tree-sitter-language-pack"):
            package = distribution(package_name)
            files = package.files
            if files is None:
                raise IndexContractError("parser_unavailable", "parser artifact inventory unavailable")
            binaries = []
            for relative in sorted(files, key=str):
                if relative.suffix.lower() in _NATIVE_SUFFIXES | {".py"}:
                    binaries.append((relative.as_posix(),
                                     _artifact_digest(Path(package.locate_file(relative)))))
            if not any(Path(name).suffix.lower() in _NATIVE_SUFFIXES for name, _ in binaries):
                raise IndexContractError("parser_unavailable", "native parser artifacts unavailable")
            artifacts.append((package_name, package.version, tuple(binaries)))
        cache_dir = getattr(language_pack, "cache_dir", None)
        if cache_dir is not None:
            cache = Path(cache_dir())
            cached = tuple(
                (path.relative_to(cache).as_posix(), _artifact_digest(path))
                for path in sorted(cache.rglob("*"))
                if path.is_file() and path.suffix.lower() in _NATIVE_SUFFIXES
            )
            artifacts.append(("grammar-cache", cached))
        for name, filename in (("adapter", __file__), ("extractor", extractor.__file__),
                               ("languages", languages.__file__), ("model", model.__file__)):
            artifacts.append((name, _artifact_digest(Path(filename))))
    except IndexContractError:
        raise
    except Exception as error:
        raise IndexContractError("parser_unavailable", "parse policy artifacts unavailable") from error
    return tuple(artifacts)


def parse_fingerprint(limits: EvidenceLimits = EvidenceLimits()) -> bytes:
    """Fingerprint effective rules, source code, native artifacts, and all limits."""
    _validate_limits(limits)
    registry = tuple(
        (extension, spec.name, spec.extensions,
         tuple(sorted((node_type, kind.value) for node_type, kind in spec.kinds.items())),
         tuple(sorted(spec.comment_nodes)), tuple(sorted(spec.anonymous_nodes)),
         spec.body_fields, spec.name_children, tuple(sorted(spec.entity_nodes)))
        for extension, spec in sorted(languages.BY_EXTENSION.items())
    )
    fields = (
        ADAPTER_VERSION,
        (limits.max_blob_bytes, limits.max_definitions, limits.max_representation_bytes),
        registry, tuple(sorted(extractor._TRANSPARENT)), extractor._NAME_FIELDS,
        tuple(sorted(extractor._IDENTIFIER_NODES)), _artifacts(),
    )
    return blake2b(canonical_encode("parse-policy", fields), digest_size=32).digest()


def _spec_for_path(path: bytes) -> LanguageSpec | None:
    filename = path.rsplit(b"/", 1)[-1]
    if b"." not in filename:
        return None
    try:
        extension = (b"." + filename.rsplit(b".", 1)[-1]).decode("ascii").lower()
    except UnicodeDecodeError:
        return None
    spec = languages.BY_EXTENSION.get(extension)
    return spec if spec is not None and spec.name != "sql" else None


def _text(node: Node | None, source: bytes) -> str:
    return "" if node is None else source[node.start_byte:node.end_byte].decode("utf-8").strip()


def _label(text: str) -> str:
    if len(text) >= 2 and text[0] in ("\"", "'") and text[-1] == text[0]:
        text = text[1:-1]
    return text or "<anonymous>"


def _binding_name(node: Node, source: bytes) -> str:
    parent = node.parent
    for _ in range(6):
        if parent is None:
            break
        kind = parent.type
        if kind in ("variable_declarator", "public_field_definition", "field_definition",
                    "property_signature", "var_spec", "const_spec"):
            return _label(_text(parent.child_by_field_name("name"), source))
        if kind in ("short_var_declaration", "assignment_expression"):
            return _label(_text(parent.child_by_field_name("left"), source))
        if kind == "pair":
            return _label(_text(parent.child_by_field_name("key"), source))
        if kind == "call_expression":
            callee = _text(parent.child_by_field_name("function"), source) or "call"
            arguments = parent.child_by_field_name("arguments")
            if arguments is not None:
                for argument in arguments.children:
                    if argument.type in ("string", "template_string"):
                        return f"{callee}({_text(argument, source)})"
            return f"{callee}(fn)"
        if kind not in extractor._TRANSPARENT:
            break
        parent = parent.parent
    return "<anonymous>"


def _name(node: Node, spec: LanguageSpec, source: bytes) -> str:
    if node.type in spec.anonymous_nodes:
        return _text(node.child_by_field_name("name"), source) or _binding_name(node, source)
    if node.type == "impl_item":
        implemented = _text(node.child_by_field_name("type"), source)
        trait = _text(node.child_by_field_name("trait"), source)
        return f"{trait} for {implemented}" if trait and implemented else implemented or "<anonymous>"
    for field_name in extractor._NAME_FIELDS:
        text = _text(node.child_by_field_name(field_name), source)
        if text:
            return _label(text)
    for child in node.children:
        if child.type in spec.name_children:
            text = _text(child, source)
            if child.type in ("atx_heading", "setext_heading"):
                text = text.lstrip("#").strip()
            return _label(text)
    for child in node.children:
        if child.type in extractor._IDENTIFIER_NODES:
            return _text(child, source) or "<anonymous>"
    return "<anonymous>"


class _LimitReached(Exception):
    def __init__(self, status: ParseStatus) -> None:
        self.status = status
        super().__init__(status.value)


def _locate(root: Node, spec: LanguageSpec, maximum: int) -> list:
    """Project AST order onto entity-child positions, retaining canonical parents."""
    if maximum == 0:
        raise _LimitReached(ParseStatus.DEFINITION_LIMIT)
    located = [(root, None, ())]
    child_counts = [0]
    stack = [(iter(root.children), 0)]
    while stack:
        children, parent = stack[-1]
        node = next(children, None)
        if node is None:
            stack.pop()
            continue
        if node.type in spec.comment_nodes:
            continue
        if node.type in spec.kinds:
            if len(located) >= maximum:
                raise _LimitReached(ParseStatus.DEFINITION_LIMIT)
            discriminator = located[parent][2] + (child_counts[parent],)
            child_counts[parent] += 1
            located.append((node, parent, discriminator))
            parent = len(located) - 1
            child_counts.append(0)
        stack.append((iter(node.children), parent))
    return located


def _tokens(node: Node, spec: LanguageSpec, source: bytes, *,
            own: bool = False, body_ids: frozenset[int] = frozenset()) -> Iterator[tuple[str, bool]]:
    """Walk leaves and uncovered byte gaps without descending into excluded nodes."""
    stack = [(node, False, False)]
    while stack:
        current, in_body, in_literal = stack.pop()
        if isinstance(current, tuple):
            start, end = current
            text = source[start:end].decode("utf-8")
            if not in_literal:
                text = text.strip()
            if text:
                yield text, in_body
            continue
        if current.type in spec.comment_nodes:
            continue
        if own and current.id != node.id and current.type in spec.kinds:
            continue
        in_body = in_body or current.id in body_ids
        if current.type in _INTERPOLATION_NODES:
            in_literal = False
        elif current.type in _STRING_NODES:
            in_literal = True
            if not any(child.type in _INTERPOLATION_NODES for child in current.children):
                yield source[current.start_byte:current.end_byte].decode("utf-8"), in_body
                continue
        if current.child_count == 0:
            text = source[current.start_byte:current.end_byte].decode("utf-8")
            if not in_literal:
                text = text.strip()
            if text:
                yield text, in_body
            continue
        items = []
        cursor = current.start_byte
        for child in current.children:
            if cursor < child.start_byte:
                items.append(((cursor, child.start_byte), in_body, in_literal))
            items.append((child, in_body, in_literal))
            cursor = child.end_byte
        if cursor < current.end_byte:
            items.append(((cursor, current.end_byte), in_body, in_literal))
        stack.extend(reversed(items))


def _representations(node: Node, spec: LanguageSpec, source: bytes,
                     root: bool, available: int) -> tuple:
    own: list[str] = []
    signature: list[str] = []
    body: list[str] = []
    subtree: list[str] = []
    body_ids = frozenset() if root else extractor._body_nodes(node, spec)
    if not root and not body_ids and spec.name == "toml" and node.type == "pair":
        separator = next((child for child in node.children if child.type == "="), None)
        if separator is not None:
            body_ids = frozenset(child.id for child in node.named_children
                             if child.start_byte >= separator.end_byte)
    used = 0
    for token, in_body in _tokens(node, spec, source, own=True, body_ids=body_ids):
        to_body = in_body or not body_ids
        to_signature = not root and not in_body
        used += len(token.encode("utf-8")) * (1 + int(to_body) + int(to_signature))
        if used > available:
            raise _LimitReached(ParseStatus.REPRESENTATION_LIMIT)
        own.append(token)
        if to_body:
            body.append(token)
        if to_signature:
            signature.append(token)
    for token, _ in _tokens(node, spec, source):
        used += len(token.encode("utf-8"))
        if used > available:
            raise _LimitReached(ParseStatus.REPRESENTATION_LIMIT)
        subtree.append(token)
    return tuple(own), None if root else tuple(signature), tuple(body), tuple(subtree), used


def _build(root: Node, spec: LanguageSpec, source: bytes, parse_id: str,
           fingerprint: bytes, limits: EvidenceLimits) -> tuple:
    located = _locate(root, spec, limits.max_definitions)
    definitions: list[EntityDefinition] = []
    versions: dict[str, EntityVersion] = {}
    duplicate_allowance: dict[tuple[str, bytes], int] = {}
    remaining = limits.max_representation_bytes
    for node, parent_index, discriminator in located:
        synthetic = parent_index is None
        parent = None if synthetic else definitions[parent_index]
        kind = "module" if synthetic else spec.kinds[node.type].value
        if synthetic:
            span = SourceSpan(0, len(source), 1, 1 + source[:-1].count(b"\n") if source else 1)
            names = ("<module>",)
        else:
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            if node.end_byte > node.start_byte and node.end_point[1] == 0:
                end_line -= 1
            span = SourceSpan(node.start_byte, node.end_byte, start_line, max(start_line, end_line))
            names = (() if parent.synthetic_root else parent.names) + (_name(node, spec, source),)
        source_digest = blake2b(source[span.start_byte:span.end_byte], digest_size=32).digest()
        duplicate_key = (kind, source_digest)
        available = remaining + duplicate_allowance.get(duplicate_key, 0)
        own, signature, body, subtree, used = _representations(node, spec, source, synthetic, available)
        version = EntityVersion(fingerprint, spec.name, kind, source_digest,
                                own, signature, body, subtree)
        version_id = version.id
        existing = versions.get(version_id)
        if existing is None:
            if used > remaining:
                raise _LimitReached(ParseStatus.REPRESENTATION_LIMIT)
            remaining -= used
            versions[version_id] = version
            duplicate_allowance[duplicate_key] = max(used, duplicate_allowance.get(duplicate_key, 0))
        elif existing != version:
            raise IndexContractError("digest_collision", "different versions share an identifier")
        definitions.append(EntityDefinition(
            parse_id, version_id, span, kind, names,
            None if parent is None else parent.id, discriminator, synthetic,
        ))
    return tuple(definitions), tuple(versions.values())


def parse_evidence(source: bytes, path: bytes, object_format: str = "sha1",
                   limits: EvidenceLimits = EvidenceLimits()) -> EvidenceBundle:
    """Parse original UTF-8 bytes, publishing all definitions or an explicit unknown."""
    if type(source) is not bytes:
        raise IndexContractError("invalid_source", "source must be bytes")
    model._path(path)
    if type(object_format) is not str or object_format not in ("sha1", "sha256"):
        raise IndexContractError("invalid_object_format", "expected sha1 or sha256")
    _validate_limits(limits)
    fingerprint = parse_fingerprint(limits)
    digest = new_hash(object_format)
    digest.update(b"blob " + str(len(source)).encode("ascii") + b"\0")
    digest.update(source)
    blob = GitOid(object_format, digest.digest())
    spec = _spec_for_path(path)
    language = None if spec is None else spec.name

    def unknown(status: ParseStatus, reason: str) -> EvidenceBundle:
        return EvidenceBundle(ParseResult(
            fingerprint, blob, path, language, status, len(source), (), (reason,),
        ), ())

    if len(source) > limits.max_blob_bytes:
        return unknown(ParseStatus.OVERSIZED, "source exceeds max_blob_bytes")
    if spec is None:
        return unknown(ParseStatus.UNSUPPORTED_LANGUAGE, "no registered byte adapter for extension")
    try:
        source.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return unknown(ParseStatus.UNSUPPORTED_ENCODING, "source is not strict UTF-8")
    if b"\0" in source:
        return unknown(ParseStatus.UNSUPPORTED_ENCODING, "source contains NUL")
    try:
        downloaded = getattr(language_pack, "downloaded_languages", None)
        if downloaded is not None and spec.name not in downloaded():
            raise IndexContractError("parser_unavailable", "grammar is not installed locally")
        tree = get_parser(spec.name).parse(source)
    except IndexContractError:
        raise
    except Exception as error:
        raise IndexContractError("parser_unavailable", "local parser could not produce a tree") from error
    root = tree.root_node
    if root.has_error:
        return unknown(ParseStatus.PARSE_ERROR, "syntax tree contains errors")
    parse_id = content_id("par1", (fingerprint, blob.fields(), path))
    try:
        definitions, versions = _build(root, spec, source, parse_id, fingerprint, limits)
    except _LimitReached as error:
        return unknown(error.status, f"source exceeds max_{error.status.value.removesuffix('_limit')}s"
                       if error.status is ParseStatus.DEFINITION_LIMIT
                       else "source exceeds max_representation_bytes")
    return EvidenceBundle(ParseResult(
        fingerprint, blob, path, language, ParseStatus.COMPLETE, len(source), definitions,
    ), versions)