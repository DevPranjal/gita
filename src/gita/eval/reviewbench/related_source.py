"""Bounded, exact-HEAD dependency context, without query-time entity parsing.

Raw declaration locators are grep heuristics, not parsed definitions. Neither unresolved
symbols nor index/parser coverage gaps establish that an implementation is absent.
"""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
from typing import Iterable, Literal, NotRequired, TypedDict

from ...index.model import GitOid
from .common import IntegrityError, ProtocolError, checked_path, git, oid, require, require_integrity
from .evidence import Evidence


class Span(TypedDict):
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


SourceKind = Literal["indexed_definition", "raw_git_file"]


class Source(TypedDict):
    source_id: str
    path: str
    span: Span
    name: str | None
    commit: str
    blob: str
    source_kind: SourceKind
    text: str


class Candidate(TypedDict):
    source_id: str
    path: str
    span: Span | None
    name: str | None
    commit: str
    blob: str
    source_kind: SourceKind
    included: bool
    omission: str | None
    match_kind: NotRequired[str]


class SymbolStatus(TypedDict):
    symbol: str
    status: str
    candidates: list[Candidate]
    ambiguity: bool
    match_count: int
    omitted_by_match_limit: int
    has_more: bool
    declaration_match_count: NotRequired[int]
    reference_match_count: NotRequired[int]
    lookup_kind: NotRequired[str]


class Coverage(TypedDict):
    unresolved: list[str]
    omitted_by_budget: list[str]
    excluded_sources: list[str]
    omitted_non_utf8: list[str]
    serialized_source_characters: int
    max_characters: int
    index_gap: str | None
    source_priority: str
    absence_claim: bool
    limitations: str


class RelatedSources(TypedDict):
    commit: str
    source_kind: SourceKind
    sources: list[Source]
    symbols: list[SymbolStatus]
    coverage: Coverage


_SOURCE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".go",
    ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".rb", ".php", ".swift",
    ".kt", ".kts", ".scala", ".sh", ".vue", ".svelte",
})
_UNAVAILABLE = "Indexed evidence unavailable for this revision; common Git tools remain available"


def _priority(path: str) -> tuple[int, int, str]:
    parts = PurePosixPath(path.lower()).parts
    name = parts[-1]
    nonproduction = any(p in {"test", "tests", "__tests__", "spec", "specs",
                             "doc", "docs", "documentation"} for p in parts)
    nonproduction |= name.startswith(("test_", "test.")) or any(
        marker in name for marker in ("_test.", ".test.", ".spec.", "_spec."))
    return (int(nonproduction), int(PurePosixPath(name).suffix not in _SOURCE_EXTENSIONS), path)


def _source(path: str, span: Span, name: str | None, head: str,
            blob: str, kind: SourceKind, text: str) -> Source:
    return {"source_id": f"{head}:{path}:{span['start_byte']}:{span['end_byte']}",
            "path": path, "span": span, "name": name, "commit": head,
            "blob": blob, "source_kind": kind, "text": text}


def _declaration_pattern(token: str) -> str:
    """Conventional line locators only: no entity spans, grammar, or identity."""
    space = "[[:space:]]"
    modifiers = ("(export|default|public|private|protected|static|abstract|final|sealed|"
                 "async|declare|readonly|override|virtual|inline|extern|unsafe|partial|open)")
    prefix = f"^{space}*({modifiers}{space}+)*"
    named = (f"{prefix}(class|def|function|func|interface|struct|enum|type|trait|record|module)"
             f"{space}+{token}({space}|[<(:={{]|$)")
    variable = f"{prefix}(var|let|const|val){space}+{token}({space}|[:=]|$)"
    property_assignment = f"^{space}*((self|this)[.])?[\"']?{token}[\"']?{space}*(:|=)"
    method = (f"{prefix}([A-Za-z_][A-Za-z0-9_.:<>,?*&]*(\\[\\])*{space}+)?"
              f"{token}{space}*\\([^;]*\\){space}*"
              f"(:[^=;{{]+)?({space}*\\{{|{space}*=>)")
    receiver = f"^{space}*func{space}+\\([^)]*\\){space}+{token}{space}*\\("
    return "|".join((named, variable, property_assignment, method, receiver))


def _grep_paths(evidence: Evidence, head: str, members: dict[str, tuple[str, str]],
                *arguments: str) -> set[str]:
    output = git(evidence.repo, "grep", "-l", "-z", "-I", *arguments,
                 head, "--", accepted=(0, 1))
    paths: set[str] = set()
    prefix = (head + ":").encode("ascii")
    for record in output.split(b"\0"):
        if not record:
            continue
        require_integrity(record.startswith(prefix), "Git grep returned a non-HEAD source")
        path = record[len(prefix):].decode("utf8", "strict")
        checked_path(path)
        require_integrity(path in members, "Git grep returned a nonmember source")
        paths.add(path)
    return paths


def _raw_sources(evidence: Evidence, symbols: list[str], head: str,
                 members: dict[str, tuple[str, str]], excluded: set[str],
                 result: RelatedSources) -> RelatedSources:
    coverage = result["coverage"]
    coverage["source_priority"] = (
        "heuristic declaration locators first; global non-test/non-doc paths, "
        "normal source extensions, lexical path before any source budget allocation")
    declaration_paths: set[str] = set()
    for token in dict.fromkeys(symbols):
        declarations = _grep_paths(evidence, head, members, "-E", "-e", _declaration_pattern(token))
        references = _grep_paths(evidence, head, members, "-F", "-w", "-e", token)
        require_integrity(declarations <= references, "Declaration locator is not a literal token match")
        paths = sorted(declarations or references, key=_priority)
        declaration_paths.update(paths[:20] if declarations else ())
        candidates: list[Candidate] = [
            {"source_id": f"{head}:{path}:file", "path": path, "span": None,
             "name": None, "commit": head, "blob": members[path][1],
             "source_kind": "raw_git_file", "included": False,
             "omission": None if declarations else "reference_only_not_loaded",
             "match_kind": "declaration_locator_heuristic" if declarations else "reference_only"}
            for path in paths[:20]
        ]
        if not declarations:
            coverage["unresolved"].append(token)
        result["symbols"].append({
            "symbol": token,
            "status": ("declaration_locator_heuristic" if declarations else
                       ("reference_only_not_absence" if references else "unresolved_not_absence")),
            "lookup_kind": "declaration_locator_heuristic" if declarations else "reference_only",
            "candidates": candidates, "ambiguity": len(paths) > 1,
            "match_count": len(paths), "declaration_match_count": len(declarations),
            "reference_match_count": len(references - declarations),
            "omitted_by_match_limit": max(0, len(paths) - 20), "has_more": len(paths) > 20,
        })
    delivered: dict[str, Candidate] = {}
    for path in sorted(declaration_paths, key=_priority):
        candidate: Candidate = {
            "source_id": f"{head}:{path}:file", "path": path, "span": None,
            "name": None, "commit": head, "blob": members[path][1],
            "source_kind": "raw_git_file", "included": False, "omission": None,
            "match_kind": "declaration_locator_heuristic",
        }
        key = candidate["source_id"]
        if path in excluded:
            candidate["omission"] = "already_included_full_head_file"
            coverage["excluded_sources"].append(key)
        else:
            raw = evidence.blob(path, head)
            try:
                text = raw.decode("utf8", "strict")
            except UnicodeDecodeError:
                candidate["omission"] = "non_utf8_whole_file_not_loaded"
                coverage["omitted_non_utf8"].append(path)
                delivered[path] = candidate
                continue
            span = Span(start_byte=0, end_byte=len(raw), start_line=1,
                        end_line=max(1, raw.count(b"\n") + int(not raw.endswith(b"\n"))))
            source = _source(path, span, None, head, members[path][1], "raw_git_file", text)
            candidate["source_id"] = source["source_id"]
            candidate["span"] = span
            key = source["source_id"]
            size = len(json.dumps([*result["sources"], source], ensure_ascii=True))
            if size > coverage["max_characters"]:
                candidate["omission"] = "whole_source_exceeds_budget"
                coverage["omitted_by_budget"].append(key)
            else:
                result["sources"].append(source)
                candidate["included"] = True
                coverage["serialized_source_characters"] = size
        delivered[path] = candidate
    for status in result["symbols"]:
        for candidate in status["candidates"]:
            if candidate["path"] in delivered:
                locator_kind = candidate["match_kind"]
                candidate.update(delivered[candidate["path"]])
                candidate["match_kind"] = locator_kind
    return result


def related_sources(
    evidence: Evidence, symbols: list[str], *, indexed: bool,
    max_characters: int = 100000, excluded_paths: Iterable[str] = (),
) -> RelatedSources:
    """Return whole definitions (H) or declaration-located whole UTF-8 files (B).

    ``excluded_paths`` names full HEAD files already supplied by the caller.
    The budget measures ``json.dumps(sources, ensure_ascii=True)`` characters,
    including metadata and escaping. Whole sources are omitted, never truncated.
    Candidate counts describe lookup matches, not semantic dependency proofs.
    Reference-only raw matches supply candidate paths without loading usage files.
    """
    require(type(symbols) is list and len(symbols) <= 16,
            "Expected at most 16 simple identifier terms")
    require(all(type(s) is str and len(s) <= 80
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s) is not None
                for s in symbols), "Invalid simple identifier term")
    require(type(indexed) is bool, "indexed must be boolean")
    require(type(max_characters) is int and max_characters >= 2,
            "max_characters must fit at least the serialized empty sources list")
    require(not isinstance(excluded_paths, (str, bytes)), "excluded_paths must contain paths")
    excluded = {checked_path(p) for p in excluded_paths}
    head = oid(evidence.head)
    members = evidence.members(head)
    require(excluded <= members.keys(), "Excluded path is not a full HEAD member")
    gap = None
    session = None
    if indexed:
        if not any(head in record["commits"] for record in evidence.config.get("indexes", [])):
            gap = _UNAVAILABLE
        else:
            try:
                session = evidence._index(head)
            except IntegrityError:
                raise
            except ProtocolError as error:
                if str(error) != _UNAVAILABLE:
                    raise
                gap = str(error)
    kind: SourceKind = "indexed_definition" if session is not None else "raw_git_file"
    coverage: Coverage = {
        "unresolved": [], "omitted_by_budget": [], "excluded_sources": [],
        "omitted_non_utf8": [], "serialized_source_characters": 2,
        "max_characters": max_characters, "index_gap": gap,
        "source_priority": "heuristic: non-test/non-doc paths, normal source extensions, lexical path",
        "absence_claim": False,
        "limitations": "Index/parser coverage may be incomplete; raw matches need not be definitions. "
                        "Unresolved is not proof of absence.",
    }
    result: RelatedSources = {"commit": head, "source_kind": kind, "sources": [],
                              "symbols": [], "coverage": coverage}
    if session is None:
        return _raw_sources(evidence, symbols, head, members, excluded, result)
    delivered: dict[str, Candidate] = {}
    for token in dict.fromkeys(symbols):
        sources: list[Source] = []
        total = 0
        more = False
        if session is not None:
            cursor = None
            seen_cursors: set[str] = set()
            seen_occurrences: set[str] = set()
            while True:
                page = session.resolve(token, at=GitOid.from_hex("sha1", head),
                                       historical=False, limit=20, cursor=cursor)
                evidence._occurrences(page, session)
                total = max(total, page["page"]["total"])
                for row in page["results"]:
                    ref = row.get("occurrence")
                    if ref is None:
                        continue
                    require_integrity(ref["commit"]["hex"] == head,
                                      "Related occurrence is not at exact HEAD")
                    evidence._occurrences(ref, session)
                    if ref["id"] in seen_occurrences:
                        continue
                    seen_occurrences.add(ref["id"])
                    path = ref["path"]
                    require_integrity(path in members, "Related source is not a HEAD member")
                    span = Span(start_byte=ref["span"]["start_byte"],
                                end_byte=ref["span"]["end_byte"],
                                start_line=ref["span"]["start_line"],
                                end_line=ref["span"]["end_line"])
                    raw = session.source(ref)
                    require_integrity(raw == evidence.blob(path, head)[span["start_byte"]:span["end_byte"]],
                                      "Related indexed source differs from HEAD Git bytes")
                    sources.append(_source(path, span, ".".join(ref["names"]), head,
                                           members[path][1], kind, raw.decode("utf8", "strict")))
                    if len(sources) == 20:
                        break
                cursor = page["page"]["next_cursor"]
                more = bool(cursor) or total > len(sources)
                if len(sources) >= 20 or not cursor:
                    break
                require_integrity(cursor not in seen_cursors, "Repeated related-source index cursor")
                seen_cursors.add(cursor)
        candidates: list[Candidate] = []
        for source in sources:
            key = source["source_id"]
            if key in delivered:
                candidates.append(delivered[key])
                continue
            candidate: Candidate = {
                "source_id": key, "path": source["path"], "span": source["span"],
                "name": source["name"], "commit": head, "blob": source["blob"],
                "source_kind": kind, "included": False, "omission": None,
            }
            if source["path"] in excluded:
                candidate["omission"] = "already_included_full_head_file"
                coverage["excluded_sources"].append(key)
            else:
                size = len(json.dumps([*result["sources"], source], ensure_ascii=True))
                if size > max_characters:
                    candidate["omission"] = "whole_source_exceeds_budget"
                    coverage["omitted_by_budget"].append(key)
                else:
                    result["sources"].append(source)
                    candidate["included"] = True
                    coverage["serialized_source_characters"] = size
            delivered[key] = candidate
            candidates.append(candidate)
        if not sources:
            coverage["unresolved"].append(token)
        ambiguous = total > 1
        result["symbols"].append({
            "symbol": token,
            "status": ("unresolved_not_absence" if not sources else
                       ("ambiguous" if ambiguous else
                        ("resolved_definition" if session is not None else "text_match_not_definition"))),
            "candidates": candidates, "ambiguity": ambiguous, "match_count": total,
            "omitted_by_match_limit": max(0, total - min(total, 20)), "has_more": more,
        })
    return result
