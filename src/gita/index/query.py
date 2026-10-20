"""Read-only, generation-pinned queries over persisted entity lineage."""

from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from hashlib import blake2b, file_digest
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

from ..vcs.git import CommittedRepo, GitObjectError, Repo
from . import evidence, matching, model
from .model import (
	EntityDefinition, EntityOccurrence, EntityVersion, GitOid, IdentityContext,
	IndexContractError, LineageSeed, PageCounts, SourceSpan, _identifier, _path,
	canonical_encode, content_id,
)
from .store import IndexStore, IndexStoreError, _dump, _load, _record_errors, _storage_errors


class QueryError(RuntimeError):
	"""A classified query refusal, without any automatic build or repair."""

	def __init__(self, code: str, message: str) -> None:
		self.code = code
		super().__init__(message)


@contextmanager
def _query_errors() -> Iterator[None]:
	try:
		with _record_errors():
			yield
	except IndexStoreError as error:
		raise QueryError(error.code, str(error)) from error


def _check(condition: bool, message: str) -> None:
	if not condition:
		raise QueryError("corrupt", message)


def _oid_json(oid: GitOid | None) -> dict[str, str] | None:
	return None if oid is None else {"object_format": oid.object_format, "hex": oid.digest.hex()}


def _json_oid(value: Any) -> GitOid:
	if (type(value) is not dict or set(value) != {"object_format", "hex"}
			or type(value["object_format"]) is not str or type(value["hex"]) is not str
			or re.fullmatch(r"[0-9a-f]+", value["hex"]) is None):
		raise QueryError("invalid_source", "Expected an exact object-format-qualified Git ID")
	return GitOid.from_hex(value["object_format"], value["hex"])


def open_index(
	repository: CommittedRepo, scope: str = "default", generation: str | None = None,
) -> IndexSession:
	"""Open an existing publication and immediately pin its read snapshot."""
	return IndexSession(repository, scope=scope, generation=generation)


class IndexSession:
	"""An owned read-only snapshot; the caller retains ownership of the repository."""

	def __init__(
		self, repository: CommittedRepo, scope: str = "default", generation: str | None = None,
	) -> None:
		self._stack = ExitStack()
		self._closed = False
		self._coverage_cache: OrderedDict[int, dict[str, Any]] = OrderedDict()
		self._repository = repository
		try:
			with _query_errors():
				if type(scope) is not str or not scope:
					raise QueryError("not_built", "Select a nonempty published scope")
				if generation is not None and (type(generation) is not str or not generation):
					raise QueryError("generation_gone", "Select an existing generation")
				self._store = self._stack.enter_context(
					IndexStore.open(IndexStore.locate(Repo(repository.root)), read_only=True),
				)
				self._stack.enter_context(self._store.snapshot())
				selected = generation if generation is not None else self._store.published_generation(scope)
				if selected is None:
					raise QueryError("not_built", "The selected scope has no published index")
				record = self._store.generation(selected)
				if record["scope"] != scope:
					raise QueryError("outside_scope", "Generation belongs to a different scope")
				if record["state"] != "published":
					raise QueryError("not_built", "Generation has not been published")
				self.scope = scope
				self.generation = selected
				self.context: IdentityContext = self._store._context(record["context_id"])
				self.tip: GitOid = record["tip"]
				self._check_policy()
				tip, self._tip_sequence = self._at(self.tip)
				_check(tip == self.tip, "Published tip is not a member")
				anchor = self._store._member(self.generation, 0)
				_check(self._store._row_oid(anchor, "commit") == self.context.anchor,
					   "Published anchor disagrees with its context")
				self._store.connection.create_function(
					"gita_query_occurrence", 8, self._occurrence_id, deterministic=True,
				)
		except BaseException as error:
			try:
				self.close()
			except BaseException as cleanup:
				error.add_note(f"Query cleanup also failed: {cleanup}")
			raise

	def _check_policy(self) -> None:
		limits = dict(self.context.limits)
		_check(set(limits) == {"max_blob_bytes", "max_definitions", "max_representation_bytes",
							   "max_candidates", "max_pairs"}, "Invalid saved deterministic limits")
		parse_limits = evidence.EvidenceLimits(
			max_blob_bytes=limits["max_blob_bytes"], max_definitions=limits["max_definitions"],
			max_representation_bytes=limits["max_representation_bytes"],
		)
		try:
			parse_fp = evidence.parse_fingerprint(parse_limits)
		except IndexContractError as error:
			raise QueryError(error.code, str(error)) from error
		if parse_fp != self.context.parse_fingerprint:
			raise QueryError("parse_mismatch", "Installed parse policy differs from this generation")
		_check(0 <= limits["max_candidates"] <= 64 and 0 <= limits["max_pairs"] <= 10000,
			   "Invalid saved matching limits")
		with Path(__file__).with_name("builder.py").open("rb") as stream:
			builder_digest = file_digest(stream, "sha256").digest()
		manifest = matching.match_manifest(builder_digest, limits["max_candidates"], limits["max_pairs"])
		if matching.match_fingerprint(manifest) != self.context.match_fingerprint:
			raise QueryError("match_mismatch", "Installed matching policy differs from this generation")

	def __enter__(self) -> IndexSession:
		self._ensure_open()
		return self

	def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
		self.close()

	def close(self) -> None:
		if not self._closed:
			self._closed = True
			self._coverage_cache.clear()
			with _query_errors():
				self._stack.close()

	def _ensure_open(self) -> None:
		if self._closed:
			raise QueryError("query_closed", "Index session is closed")

	@contextmanager
	def _reading(self) -> Iterator[None]:
		self._ensure_open()
		with _query_errors(), _storage_errors(self._store.path):
			yield

	def _one(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
		cursor = self._store.connection.execute(sql, parameters)
		try:
			return cursor.fetchone()
		finally:
			cursor.close()

	def _at(self, at: GitOid | str | None) -> tuple[GitOid, int]:
		if at is None:
			oid = self.tip
		elif type(at) is str:
			try:
				oid = self._repository.resolve(at)
			except GitObjectError as error:
				raise QueryError("outside_scope", "Revision is unavailable in this generation") from error
		elif type(at) is GitOid:
			oid = at
		else:
			raise QueryError("outside_scope", "Expected a committed revision or GitOid")
		row = self._one(
			"SELECT sequence FROM membership WHERE generation_id = ? AND commit_format = ? AND commit_digest = ?",
			(self.generation, *oid.fields()),
		)
		if row is None:
			raise QueryError("outside_scope", "Commit is not a member of this generation")
		return oid, row["sequence"]

	def _identity(self, eid: str) -> LineageSeed:
		try:
			_identifier(eid, ("eid1", "oeid1"))
		except IndexContractError as error:
			raise QueryError("invalid_identity", str(error)) from error
		row = self._one(
			"SELECT * FROM lineage WHERE id = ? AND context_id = ? AND (EXISTS ("
			"SELECT 1 FROM binding JOIN file_state ON file_state.binding_set_id = binding.binding_set_id "
			"WHERE binding.eid = lineage.id AND file_state.generation_id = ?) OR EXISTS ("
			"SELECT 1 FROM event JOIN generation_event ON generation_event.event_id = event.id "
			"WHERE event.eid = lineage.id AND generation_event.generation_id = ?) OR EXISTS ("
			"SELECT 1 FROM departure WHERE eid = lineage.id AND generation_id = ?))",
			(eid, self.context.id, self.generation, self.generation, self.generation),
		)
		if row is None:
			raise QueryError("identity_outside_scope", "Identity is not in this generation's population")
		fields = _load(row["fields_json"])
		seed = LineageSeed(fields[0], GitOid(*fields[1]), *fields[2:])
		_check(seed.id == eid and canonical_encode(eid.split("_")[0], seed.fields()) == row["canonical_payload"]
			   and (seed.context_id, seed.commit, seed.occurrence_id, seed.reason)
			   == (row["context_id"], self._store._row_oid(row, "commit"), row["occurrence_id"], row["reason"]),
			   "Lineage payload disagrees with its stored identity")
		return seed

	def _definition(self, identifier: str) -> tuple[EntityDefinition, EntityVersion]:
		row = self._store._definition(identifier)
		definition = EntityDefinition(
			row["parse_id"], row["version_id"],
			SourceSpan(row["start_byte"], row["end_byte"], row["start_line"], row["end_line"]),
			row["kind"], _load(row["names_json"]), row["parent_id"],
			_load(row["discriminator_json"]), bool(row["synthetic_root"]),
		)
		_check(definition.id == identifier and definition.names[-1] == row["terminal_name"]
			   and row["synthetic_root"] in (0, 1) and row["status"] == "complete"
			   and row["parse_fp"] == self.context.parse_fingerprint,
			   "Definition columns disagree with its identity or interpretation")
		blob = GitOid(row["blob_format"], row["blob_digest"])
		_check(definition.parse_id == content_id("par1", (row["parse_fp"], blob.fields(), row["path"])),
			   "Definition parse key disagrees with its blob and path")
		parsed = self._one(
			"SELECT byte_length, json_extract(fields_json, '$[1][3]') AS language, "
			"json_extract(fields_json, ?) AS definition_json FROM parse_result WHERE id = ?",
			(f"$[1][6][1][{row['ordinal']}]", definition.parse_id),
		)
		_check(parsed is not None, "Missing definition parse")
		assert parsed is not None
		saved = _load(parsed["definition_json"])
		_check(saved == (definition.parse_id, definition.version_id, definition.span.fields(),
						 definition.kind, definition.names, definition.parent_id,
						 definition.discriminator, int(definition.synthetic_root))
			   and definition.span.end_byte <= parsed["byte_length"],
			   "Definition bounds or fields disagree with saved parse evidence")
		version = self._store.get_version(definition.version_id)
		_check(version is not None, "Missing definition version")
		assert version is not None
		_check(version.kind == definition.kind and version.parse_fingerprint == row["parse_fp"]
			   and version.language == parsed["language"], "Definition version disagrees with parse evidence")
		return definition, version

	@staticmethod
	def _occurrence_id(
		commit_format: str, commit_digest: bytes, path: bytes, blob_format: str,
		blob_digest: bytes, object_type: str, mode: int, definition_id: str,
	) -> str:
		return EntityOccurrence(GitOid(commit_format, commit_digest), path,
								GitOid(blob_format, blob_digest), object_type, mode, definition_id).id

	def _occurrence(self, state: sqlite3.Row, definition_id: str, commit: GitOid) -> dict[str, Any]:
		definition, unused_version = self._definition(definition_id)
		blob = GitOid(state["blob_format"], state["blob_digest"])
		_check(state["generation_id"] == self.generation and state["status"] == "present"
			   and state["parse_id"] == definition.parse_id
			   and definition.parse_id == content_id("par1", (self.context.parse_fingerprint, blob.fields(), state["path"]))
			   and state["object_type"] == "blob" and state["mode"] in (0o100644, 0o100755),
			   "Occurrence does not identify its exact active regular file")
		occurrence = EntityOccurrence(commit, state["path"], blob, state["object_type"], state["mode"], definition.id)
		try:
			display = occurrence.path.decode("utf-8", errors="strict")
		except UnicodeDecodeError:
			display = None
		return {
			"id": occurrence.id, "generation": self.generation, "commit": _oid_json(commit),
			"path_bytes_b64": base64.b64encode(occurrence.path).decode("ascii"), "path": display,
			"blob": _oid_json(blob), "object_type": occurrence.object_type, "mode": occurrence.mode,
			"definition_id": definition.id, "names": list(definition.names),
			"span": {"start_byte": definition.span.start_byte, "end_byte": definition.span.end_byte,
					 "start_line": definition.span.start_line, "end_line": definition.span.end_line},
		}

	def _bound_state(self, commit: GitOid, path: bytes, definition_id: str) -> sqlite3.Row:
		unused_oid, sequence = self._at(commit)
		row = self._one(
			"SELECT file_state.*, binding.eid FROM file_state "
			"JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
			"JOIN lineage ON lineage.id = binding.eid WHERE generation_id = ? "
			"AND path = ? AND from_seq <= ? AND (to_seq IS NULL OR to_seq > ?) "
			"AND status = 'present' AND binding.definition_id = ? AND lineage.context_id = ?",
			(self.generation, path, sequence, sequence, definition_id, self.context.id),
		)
		_check(row is not None, "Occurrence is not bound at this generation member")
		assert row is not None
		return row

	def _stored_occurrence(self, identifier: str, eid: str | None = None) -> dict[str, Any]:
		row = self._store._required("occurrence", identifier)
		fields = _load(row["fields_json"])
		occurrence = EntityOccurrence(GitOid(*fields[0]), fields[1], GitOid(*fields[2]), *fields[3:])
		_check(occurrence.id == identifier and canonical_encode("occ1", occurrence.fields()) == row["canonical_payload"]
			   and (occurrence.commit, occurrence.path, occurrence.blob, occurrence.object_type,
					occurrence.mode, occurrence.definition_id)
			   == (self._store._row_oid(row, "commit"), row["path"], self._store._row_oid(row, "blob"),
				   row["object_type"], row["mode"], row["definition_id"]),
			   "Stored occurrence disagrees with its canonical payload")
		state = self._bound_state(occurrence.commit, occurrence.path, occurrence.definition_id)
		_check(eid is None or state["eid"] == eid, "Occurrence belongs to another identity")
		result = self._occurrence(state, occurrence.definition_id, occurrence.commit)
		_check(result["id"] == identifier, "Stored occurrence disagrees with the active file state")
		return result

	def _state(self, eid: str, commit: GitOid, sequence: int) -> dict[str, Any]:
		seed = self._identity(eid)
		unused_oid, seed_sequence = self._at(seed.commit)
		result: dict[str, Any] = {"eid": eid, "state": "unknown", "occurrence": None,
								  "reason": "no_complete_evidence", "last_known_occurrence": None}
		if sequence < seed_sequence:
			result.update(state="absent" if eid.startswith("eid1_") else "unknown",
						  reason="before_birth" if eid.startswith("eid1_") else "before_observation")
			return result
		cursor = self._store.connection.execute(
			"SELECT file_state.*, binding.definition_id FROM binding JOIN file_state "
			"ON file_state.binding_set_id = binding.binding_set_id WHERE binding.eid = ? "
			"AND generation_id = ? AND status = 'present' AND from_seq <= ? "
			"AND (to_seq IS NULL OR to_seq > ?) LIMIT 2", (eid, self.generation, sequence, sequence),
		)
		try:
			active = cursor.fetchall()
		finally:
			cursor.close()
		_check(len(active) <= 1, "Identity has multiple active bindings")
		if active:
			result.update(state="present", reason=None,
						  occurrence=self._occurrence(active[0], active[0]["definition_id"], commit))
			return result
		departure = self._one(
			"SELECT * FROM departure WHERE generation_id = ? AND eid = ? AND sequence <= ?",
			(self.generation, eid, sequence),
		)
		if departure is not None:
			last = self._stored_occurrence(departure["occurrence_id"], eid)
			unused_oid, last_sequence = self._at(_json_oid(last["commit"]))
			_check(last_sequence == departure["sequence"] - 1, "Departure has an incorrect source member")
			result.update(state=departure["status"], reason=departure["reason"], last_known_occurrence=last)
		return result

	def state_at(self, eid: str, at: GitOid | str | None = None) -> dict[str, Any]:
		with self._reading():
			commit, sequence = self._at(at)
			return self._state(eid, commit, sequence)

	def source(self, reference: dict[str, Any]) -> bytes:
		with self._reading():
			try:
				required = {"id", "generation", "commit", "path_bytes_b64", "path", "blob",
							"object_type", "mode", "definition_id", "names", "span"}
				if type(reference) is not dict or set(reference) != required:
					raise QueryError("invalid_source", "Expected a complete occurrence reference")
				if (reference["generation"] != self.generation or type(reference["generation"]) is not str
						or type(reference["mode"]) is not int or type(reference["object_type"]) is not str
						or type(reference["names"]) is not list
						or any(type(name) is not str for name in reference["names"])
						or type(reference["path_bytes_b64"]) is not str
						or (reference["path"] is not None and type(reference["path"]) is not str)):
					raise QueryError("invalid_source", "Invalid occurrence metadata")
				_identifier(reference["id"], ("occ1",))
				_identifier(reference["definition_id"], ("def1",))
				commit, blob = _json_oid(reference["commit"]), _json_oid(reference["blob"])
				path = base64.b64decode(reference["path_bytes_b64"], validate=True)
				_path(path)
				span_fields = reference["span"]
				if type(span_fields) is not dict or set(span_fields) != {"start_byte", "end_byte", "start_line", "end_line"}:
					raise QueryError("invalid_source", "Invalid occurrence bounds")
				span = SourceSpan(span_fields["start_byte"], span_fields["end_byte"],
								  span_fields["start_line"], span_fields["end_line"])
				state = self._bound_state(commit, path, reference["definition_id"])
				expected = self._occurrence(state, reference["definition_id"], commit)
				if reference != expected:
					raise QueryError("invalid_source", "Occurrence does not match the selected generation state")
				unused_definition, version = self._definition(reference["definition_id"])
			except QueryError as error:
				if error.code == "invalid_source":
					raise
				raise QueryError("invalid_source", str(error)) from error
			except (ValueError, TypeError, KeyError, OverflowError) as error:
				raise QueryError("invalid_source", "Malformed occurrence reference") from error
			try:
				source = self._repository.source(commit, path, blob, reference["mode"], span.start_byte, span.end_byte)
			except (GitObjectError, OSError) as error:
				raise QueryError("source_unavailable", "Committed source bytes are unavailable") from error
			if blake2b(source, digest_size=32).digest() != version.source_digest:
				raise QueryError("source_unavailable", "Committed bytes disagree with the stored source digest")
			return source

	@staticmethod
	def _limit(limit: int) -> None:
		if type(limit) is not int or not 1 <= limit <= 1000:
			raise QueryError("invalid_limit", "Page limit must be an integer from 1 to 1000")

	def _cursor_context(self, action: str, filters: Any, at: Any, order: str) -> dict[str, Any]:
		return {"version": 1, "scope": self.scope, "generation": self.generation,
				"context": self.context.id, "action": action, "filter": filters,
				"at": at, "order": order}

	@staticmethod
	def _encode_cursor(fields: dict[str, Any]) -> str:
		raw = json.dumps(fields, sort_keys=True, separators=(",", ":"),
						 ensure_ascii=False, allow_nan=False).encode("utf-8")
		token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
		if len(token) > 4096:
			raise QueryError("invalid_cursor", "Continuation exceeds the 4096-byte cursor limit")
		return token

	def _decode_cursor(self, cursor: str | None, expected: dict[str, Any]) -> tuple | None:
		if cursor is None:
			return None
		if (type(cursor) is not str or not 0 < len(cursor) <= 4096
				or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None):
			raise QueryError("invalid_cursor", "Cursor must be canonical base64url, at most 4096 bytes")
		try:
			raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
			if len(raw) > 4096:
				raise ValueError("Decoded cursor is oversized")
			fields = json.loads(raw.decode("utf-8", errors="strict"))
			if (type(fields) is not dict or set(fields) != set(expected) | {"last"}
					or type(fields["version"]) is not int or fields["version"] != 1
					or self._encode_cursor(fields) != cursor):
				raise ValueError("Cursor fields, version, or serialization are invalid")
			actual_context = {key: fields[key] for key in expected}
			if self._encode_cursor(actual_context) != self._encode_cursor(expected):
				raise ValueError("Cursor belongs to a different query or generation")
			last = fields["last"]
			if type(last) is not list:
				raise ValueError("Cursor boundary must be an array")
			if expected["action"] in ("history", "events_in"):
				if len(last) != 2 or type(last[0]) is not int or not 0 <= last[0] < 1 << 63:
					raise ValueError("Invalid event boundary sequence")
				_identifier(last[1], ("evt1",))
				return tuple(last)
			if (len(last) != 4 or type(last[0]) is not str
					or re.fullmatch(r"(?:[0-9a-f]{2})+", last[0]) is None
					or type(last[1]) is not int or not 0 <= last[1] < 1 << 63
					or type(last[2]) is not str or not last[2]):
				raise ValueError("Invalid occurrence boundary")
			path = bytes.fromhex(last[0])
			_path(path)
			last[2].encode("utf-8", errors="strict")
			prefixes = ("occ1",) if expected["action"] == "entities_in" else ("eid1", "oeid1")
			_identifier(last[3], prefixes)
			return (path, *last[1:])
		except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as error:
			raise QueryError("invalid_cursor", "Malformed or mismatched continuation cursor") from error

	def _take(
		self, cte: str, parameters: tuple, context: dict[str, Any], last: tuple | None,
		limit: int,
	) -> tuple[list[sqlite3.Row], int, str | None]:
		event_order = context["action"] in ("history", "events_in")
		keys = ("sequence", "event_id") if event_order else ("path", "start_byte", "kind", "sort_id")
		columns = ", ".join(keys)
		placeholders = ", ".join("?" for unused in keys)
		count = self._one(cte + " SELECT count(*) FROM selected", parameters)
		assert count is not None
		total = count[0]
		if last is not None:
			member = self._one(
				cte + f" SELECT 1 FROM selected WHERE ({columns}) = ({placeholders}) LIMIT 1",
				parameters + last,
			)
			if member is None:
				raise QueryError("invalid_cursor", "Cursor boundary is not a member of the filtered result")
		descending = context["order"] == "newest"
		comparison = "<" if descending else ">"
		direction = "DESC" if descending else "ASC"
		where = "" if last is None else f" WHERE ({columns}) {comparison} ({placeholders})"
		ordering = ", ".join(f"{column} {direction}" for column in keys)
		cursor = self._store.connection.execute(
			cte + f" SELECT * FROM selected{where} ORDER BY {ordering} LIMIT ?",
			parameters + (() if last is None else last) + (limit + 1,),
		)
		try:
			rows = cursor.fetchall()
		finally:
			cursor.close()
		next_cursor = None
		if len(rows) > limit:
			rows = rows[:limit]
			boundary = [rows[-1][key] for key in keys]
			if not event_order:
				boundary[0] = boundary[0].hex()
			next_cursor = self._encode_cursor({**context, "last": boundary})
		return rows, total, next_cursor

	def _coverage(self, sequence: int) -> dict[str, Any]:
		cached = self._coverage_cache.get(sequence)
		if cached is not None:
			self._coverage_cache.move_to_end(sequence)
			return deepcopy(cached)
		row = self._one(
			f"SELECT count(*) AS records, coalesce(sum({model.GAP_SQL}), 0) AS gaps, "
			f"coalesce(sum(CASE WHEN {model.UNCOVERED_SQL} THEN 1 ELSE 0 END), 0) AS uncovered, "
			f"coalesce(sum(CASE WHEN {model.EXCLUDED_SQL} THEN 1 ELSE 0 END), 0) AS excluded "
			"FROM coverage WHERE generation_id = ? AND sequence <= ?", (self.generation, sequence),
		)
		assert row is not None
		cached = {"working_tree": "out_of_scope", "source_availability": "unchecked",
				  "population": "indexed_evidence", "boundary": self.context.boundary,
				  # `status` answers "is every file indexed"; `gaps` answers "can
				  # this index mistake an edit for a delete plus an add". They are
				  # different questions, and collapsing them made a LICENSE file
				  # look like lost lineage evidence.
				  "status": "partial" if row["uncovered"] else "complete",
				  "records": row["records"], "gaps": row["gaps"],
				  "uncovered": row["uncovered"], "excluded": row["excluded"]}
		self._coverage_cache[sequence] = cached
		if len(self._coverage_cache) > 128:
			self._coverage_cache.popitem(last=False)
		return deepcopy(cached)

	def _page(
		self, results: list[dict[str, Any]], total: int | None, next_cursor: str | None,
		status: str, sequence: int,
	) -> dict[str, Any]:
		counts = PageCounts(total, len(results), None if total is None else total - len(results))
		return {"results": results, "status": status,
				"page": {"shown": counts.shown, "total": counts.total, "next_cursor": next_cursor},
				"omitted": {"count": counts.omitted, "count_known": counts.total is not None},
				"coverage": self._coverage(sequence)}

	@staticmethod
	def _name_filter(token: str, *, locator: bool = False) -> tuple[str, tuple]:
		def names(text: str) -> tuple[str, tuple]:
			predicate = "(definition.terminal_name = ? OR definition.names_json = ? OR definition.names_json = ?)"
			parameters = (text, _dump(tuple(text.split("."))), _dump(tuple(text.split("::"))))
			qualified = text.replace("::", ".")
			if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)+", qualified):
				prefix, suffix = _dump(("_gita_name_",)).split("_gita_name_")
				predicate = (
					f"({predicate} OR replace(replace(substr(definition.names_json, ?, "
					"length(definition.names_json) - ?), '\",\"', '.'), '::', '.') = ?)"
				)
				parameters += (len(prefix) + 1, len(prefix) + len(suffix), qualified)
			elif "::" in text or "." in text:
				prefix, suffix = _dump(("_gita_name_",)).split("_gita_name_")
				encoded = _dump((text,))[len(prefix):-len(suffix)]
				# Compare escaped payloads so literal quotes cannot become tuple boundaries.
				for separator in ("::", "."):
					if separator in text:
						predicate = (
							f"({predicate} OR replace(substr(definition.names_json, ?, "
							"length(definition.names_json) - ?), '\",\"', ?) = ?)"
						)
						parameters += (len(prefix) + 1, len(prefix) + len(suffix), separator, encoded)
			return predicate, parameters

		if locator:
			path, local = token.split("::", 1)
			directory = path.endswith("/")
			if directory:
				path = path[:-1]
			raw_path = path.encode("utf-8", errors="strict")
			try:
				_path(raw_path)
			except IndexContractError as error:
				raise QueryError("unsupported_path_input", str(error)) from error
			local_predicate, local_parameters = names(local)
			if directory:
				return (f"(file_state.path >= ? AND file_state.path < ? AND {local_predicate})",
						(raw_path + b"/", raw_path + b"0", *local_parameters))
			if not directory and "/" not in path:
				return f"(file_state.path = ? AND {local_predicate})", (raw_path, *local_parameters)
			path_predicate = "(file_state.path = ? OR (file_state.path >= ? AND file_state.path < ?))"
			return (f"({path_predicate} AND {local_predicate})",
					(raw_path, raw_path + b"/", raw_path + b"0", *local_parameters))
		return names(token)

	def resolve(
		self, token: str, *, at: GitOid | str | None = None, historical: bool = False,
		limit: int = 100, cursor: str | None = None,
	) -> dict[str, Any]:
		with self._reading():
			self._limit(limit)
			if type(token) is not str or not token or type(historical) is not bool:
				raise QueryError("invalid_query", "Expected a nonempty token and boolean historical selector")
			try:
				token.encode("utf-8", errors="strict")
			except UnicodeEncodeError as error:
				raise QueryError("unsupported_path_input", "Locator is not strict UTF-8") from error
			typed = token.startswith(("eid1_", "oeid1_"))
			if typed:
				if re.fullmatch(r"(?:eid1|oeid1)_[0-9a-f]{8,64}", token) is None:
					raise QueryError("invalid_identity", "Identity prefixes require 8 to 64 lowercase hex digits")
				_identifier(token.split("_")[0] + "_" + token.split("_")[1].ljust(64, "0"), ("eid1", "oeid1"))
			commit, sequence = self._at(at)
			tier = "identity"
			if typed:
				if len(token.split("_")[1]) == 64:
					identity_predicate = "lineage.id = ?"
					identity_parameters = (token,)
				else:
					identity_predicate = "lineage.id >= ? AND lineage.id < ?"
					identity_parameters = (token, token + "g")
				cte = f"""WITH population(eid) AS (
					SELECT lineage.id FROM lineage
					WHERE {identity_predicate} AND lineage.context_id = ? AND (
						EXISTS (
							SELECT 1 FROM binding INDEXED BY binding_eid
							JOIN file_state INDEXED BY file_state_binding
								ON file_state.binding_set_id = binding.binding_set_id
							WHERE binding.eid = lineage.id AND file_state.generation_id = ?
						) OR EXISTS (
							SELECT 1 FROM event INDEXED BY event_history
							WHERE event.eid = lineage.id AND EXISTS (
								SELECT 1 FROM generation_event
								WHERE generation_event.event_id = event.id AND generation_event.generation_id = ?
							)
						) OR EXISTS (
							SELECT 1 FROM departure WHERE eid = lineage.id AND generation_id = ?
						)
					)
				), selected AS (
					SELECT lineage.id AS eid, occurrence.path, definition.start_byte,
						definition.kind, lineage.id AS sort_id, 1 AS live
					FROM population JOIN lineage ON lineage.id = population.eid
						JOIN occurrence ON occurrence.id = lineage.occurrence_id
						JOIN definition ON definition.id = occurrence.definition_id
				)"""
				parameters = (*identity_parameters, self.context.id, self.generation, self.generation, self.generation)
			else:
				active_only = "" if historical else " AND (file_state.to_seq IS NULL OR file_state.to_seq > ?)"
				for tier in (("locator", "name") if "::" in token else ("name",)):
					predicate, name_parameters = self._name_filter(token, locator=tier == "locator")
					cte = """WITH matches AS (
						SELECT binding.eid, file_state.path, definition.start_byte, definition.kind,
							CASE WHEN file_state.to_seq IS NULL OR file_state.to_seq > ? THEN 1 ELSE 0 END AS live,
							row_number() OVER (PARTITION BY binding.eid ORDER BY file_state.from_seq DESC,
								file_state.path, definition.start_byte, definition.kind, definition.id) AS rank
						FROM definition JOIN binding ON binding.definition_id = definition.id
							JOIN file_state ON file_state.binding_set_id = binding.binding_set_id
							JOIN lineage ON lineage.id = binding.eid
						WHERE file_state.generation_id = ? AND lineage.context_id = ?
							AND file_state.status = 'present' AND file_state.from_seq <= ? AND """ + predicate + active_only + """
					), selected AS (
						SELECT eid, path, start_byte, kind, eid AS sort_id, live FROM matches WHERE rank = 1
					)"""
					parameters = (sequence, self.generation, self.context.id, sequence, *name_parameters)
					if not historical:
						parameters += (sequence,)
					if tier == "name" or self._one(cte + " SELECT 1 FROM selected LIMIT 1", parameters) is not None:
						break
			context = self._cursor_context(
				"resolve", {"token": token, "historical": historical, "tier": tier,
							**({"name_lookup_version": 2} if not typed else {})}, _oid_json(commit), "oldest",
			)
			last = self._decode_cursor(cursor, context)
			rows, total, next_cursor = self._take(cte, parameters, context, last, limit)
			results = []
			for row in rows:
				state = self._state(row["eid"], commit, sequence)
				results.append({"eid": row["eid"], "lifecycle": state["state"],
								"historical_only": not bool(row["live"]),
								"occurrence": state["occurrence"],
								"last_known_occurrence": state["last_known_occurrence"]})
			status = "not_found" if total == 0 else ("unique" if total == 1 else "ambiguous")
			return self._page(results, total, next_cursor, status, sequence)

	def entities_in(
		self, path: bytes, at: GitOid | str | None = None, *, limit: int = 100, cursor: str | None = None,
	) -> dict[str, Any]:
		with self._reading():
			self._limit(limit)
			try:
				_path(path)
			except IndexContractError as error:
				raise QueryError("unsupported_path_input", str(error)) from error
			commit, sequence = self._at(at)
			context = self._cursor_context("entities_in", {"path": path.hex()}, _oid_json(commit), "oldest")
			last = self._decode_cursor(cursor, context)
			cte = """WITH selected AS (
				SELECT file_state.*, binding.definition_id, definition.start_byte, definition.kind,
					gita_query_occurrence(?, ?, file_state.path, file_state.blob_format,
						file_state.blob_digest, file_state.object_type, file_state.mode, definition.id) AS sort_id
				FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id
					JOIN definition ON definition.id = binding.definition_id
					JOIN lineage ON lineage.id = binding.eid
				WHERE generation_id = ? AND file_state.path = ? AND from_seq <= ?
					AND (to_seq IS NULL OR to_seq > ?) AND file_state.status = 'present'
					AND lineage.context_id = ?
			)"""
			parameters = (*commit.fields(), self.generation, path, sequence, sequence, self.context.id)
			rows, total, next_cursor = self._take(cte, parameters, context, last, limit)
			file_state = self._one(
				"SELECT status FROM file_state WHERE generation_id = ? AND path = ? AND from_seq <= ? "
				"AND (to_seq IS NULL OR to_seq > ?)", (self.generation, path, sequence, sequence),
			)
			status = "absent" if file_state is None else file_state["status"]
			if file_state is None and self._one(
				"SELECT 1 FROM coverage WHERE generation_id = ? AND sequence <= ? AND path IS NULL "
				"AND category = 'extraction' AND reason NOT IN ('complete', 'removed') LIMIT 1",
				(self.generation, sequence),
			) is not None:
				status = "unknown"
			results = [self._occurrence(row, row["definition_id"], commit) for row in rows]
			return self._page(results, None if status == "unknown" else total, next_cursor, status, sequence)

	@staticmethod
	def _evidence_json(value: Any) -> Any:
		if type(value) is bytes:
			return {"bytes_b64": base64.b64encode(value).decode("ascii")}
		if type(value) is tuple:
			return [IndexSession._evidence_json(item) for item in value]
		return value

	def _event_json(self, identifier: str, sequence: int) -> dict[str, Any]:
		event = self._store._event(identifier)
		self._store._event_sequence(self.generation, sequence, event)
		source = None if event.source is None else self._stored_occurrence(event.source, event.eid)
		destination = None if event.destination is None else self._stored_occurrence(event.destination, event.eid)
		decision = None
		if event.decision_id is not None:
			link = self._store._link(event.decision_id)
			attached = self._one(
				"SELECT 1 FROM generation_link WHERE generation_id = ? AND link_id = ?",
				(self.generation, link.id),
			)
			_check(attached is not None and self._store._link_sequence(self.generation, link) == sequence,
				   "Event decision is not attached at its generation edge")
			decision = {"id": link.id, "status": link.status.value, "method": link.method,
						"reason": link.reason,
						"evidence": {key: self._evidence_json(value) for key, value in link.evidence}}
		return {"id": event.id, "eid": event.eid, "commit": _oid_json(event.commit),
				"parent": _oid_json(event.parent), "source": source, "destination": destination,
				"flags": list(sorted(event.flags)), "material": event.material, "decision": decision}

	def history(
		self, eid: str, *, at: GitOid | str | None = None, order: str = "newest",
		limit: int = 100, cursor: str | None = None,
	) -> dict[str, Any]:
		with self._reading():
			self._limit(limit)
			if type(order) is not str or order not in ("oldest", "newest"):
				raise QueryError("invalid_order", "History order must be oldest or newest")
			commit, sequence = self._at(at)
			context = self._cursor_context("history", {"eid": eid}, _oid_json(commit), order)
			last = self._decode_cursor(cursor, context)
			self._identity(eid)
			cte = """WITH selected AS (
				SELECT generation_event.sequence, generation_event.event_id
				FROM generation_event JOIN event ON event.id = generation_event.event_id
				WHERE generation_id = ? AND event.context_id = ? AND event.eid = ? AND sequence <= ?
			)"""
			rows, total, next_cursor = self._take(
				cte, (self.generation, self.context.id, eid, sequence), context, last, limit,
			)
			results = [self._event_json(row["event_id"], row["sequence"]) for row in rows]
			return self._page(results, total, next_cursor, "ok", sequence)

	def events_in(
		self, base: GitOid | str, head: GitOid | str, *, limit: int = 100, cursor: str | None = None,
	) -> dict[str, Any]:
		with self._reading():
			self._limit(limit)
			if base is None or head is None:
				raise QueryError("outside_scope", "Event range requires both explicit endpoints")
			base_oid, base_sequence = self._at(base)
			head_oid, head_sequence = self._at(head)
			if base_sequence > head_sequence:
				raise QueryError("outside_scope", "Event range must follow generation membership order")
			context = self._cursor_context("events_in", {}, [_oid_json(base_oid), _oid_json(head_oid)], "oldest")
			last = self._decode_cursor(cursor, context)
			cte = """WITH selected AS (
				SELECT generation_event.sequence, generation_event.event_id
				FROM generation_event JOIN event ON event.id = generation_event.event_id
				WHERE generation_id = ? AND event.context_id = ? AND sequence > ? AND sequence <= ?
			)"""
			rows, total, next_cursor = self._take(
				cte, (self.generation, self.context.id, base_sequence, head_sequence), context, last, limit,
			)
			results = [self._event_json(row["event_id"], row["sequence"]) for row in rows]
			return self._page(results, total, next_cursor, "ok", head_sequence)