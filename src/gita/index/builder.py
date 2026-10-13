"""Transactional first-parent builds and persisted-frontier continuation."""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from hashlib import blake2b, file_digest
from math import isfinite
import os
from pathlib import Path
import sqlite3
from typing import Iterator

from ..vcs.git import CommittedRepo, GitObject, GitObjectError, GitObjectInfo, GitTreeEntry
from . import evidence, matching, model
from .evidence import EvidenceBundle, EvidenceLimits
from .model import (
	ChangeEvent, EntityDefinition, EntityOccurrence, EntityVersion, GitOid, IdentityContext,
	LineageLink, LineageSeed, LinkStatus, ParseResult, ParseStatus, SourceSpan,
	canonical_encode, content_id,
)
from .store import IndexStore, _dump, _load
from .worker import EvidenceWorker


class BuildError(RuntimeError):
	"""A refused build, with no partial publication."""

	def __init__(self, code: str, message: str) -> None:
		self.code = code
		super().__init__(message)


@dataclass(frozen=True, slots=True)
class BuildLimits:
	parse_limits: EvidenceLimits = EvidenceLimits()
	parse_timeout_seconds: float = 5.0
	max_candidates: int = 64
	max_pairs: int = 10000
	page_size: int = 1000
	max_storage_bytes: int = 10 * 1024 ** 3
	workers: int = min(4, os.cpu_count() or 1)

	def __post_init__(self) -> None:
		if type(self.parse_limits) is not EvidenceLimits:
			raise BuildError("invalid_limits", "parse_limits must be EvidenceLimits")
		self.parse_limits.__post_init__()
		for name, lower, upper in (("max_candidates", 0, 64), ("max_pairs", 0, 10000),
								   ("page_size", 1, 1000), ("max_storage_bytes", 1, (1 << 63) - 1),
								   ("workers", 1, 32)):
			value = getattr(self, name)
			if type(value) is not int or not lower <= value <= upper:
				raise BuildError("invalid_limits", f"{name} must be an integer in [{lower}, {upper}]")
		try:
			valid = (type(self.parse_timeout_seconds) in (int, float)
					 and self.parse_timeout_seconds > 0 and isfinite(self.parse_timeout_seconds))
		except OverflowError:
			valid = False
		if not valid:
			raise BuildError("invalid_limits", "parse deadline must be finite and positive")

	def policy(self) -> tuple[tuple[str, int], ...]:
		return tuple(sorted({
			"max_blob_bytes": self.parse_limits.max_blob_bytes,
			"max_candidates": self.max_candidates,
			"max_definitions": self.parse_limits.max_definitions,
			"max_pairs": self.max_pairs,
			"max_representation_bytes": self.parse_limits.max_representation_bytes,
		}.items()))


@dataclass(frozen=True, slots=True)
class BuildResult:
	generation: str
	scope: str
	tip: GitOid
	context: IdentityContext
	status: str
	commits: int
	parsed_files: int
	cached_files: int
	ref_moved: bool
	current_tip: GitOid | None = None


_FILES = """
	path BLOB PRIMARY KEY, blob_format TEXT, blob_digest BLOB, object_type TEXT,
	mode INTEGER, parse_id TEXT, binding_set_id TEXT, state_id INTEGER,
	status TEXT NOT NULL, reason TEXT NOT NULL, changed INTEGER NOT NULL
"""
_POPULATION = """
	defkey TEXT PRIMARY KEY, path BLOB NOT NULL, parse_id TEXT NOT NULL,
	version_id TEXT NOT NULL, parent_id TEXT, name TEXT NOT NULL,
	kind TEXT NOT NULL, language TEXT NOT NULL, synthetic_root INTEGER NOT NULL,
	container INTEGER NOT NULL, depth INTEGER NOT NULL,
	locator BLOB NOT NULL, parent_locator BLOB,
	own_payload BLOB, own_digest BLOB, body_payload BLOB, body_digest BLOB,
	subtree_payload BLOB, subtree_digest BLOB, body_count INTEGER, subtree_count INTEGER,
	blob_format TEXT NOT NULL, blob_digest BLOB NOT NULL, object_type TEXT NOT NULL,
	mode INTEGER NOT NULL, eid TEXT, changed INTEGER NOT NULL, matched TEXT,
	uncertain TEXT, seed_reason TEXT
"""


class _Spool:
	def __init__(self, store: IndexStore, limits: BuildLimits) -> None:
		self.store = store
		self.db = store.connection
		self.limits = limits
		self.tables: list[str] = []
		self.cursors: set[sqlite3.Cursor] = set()

	def create(self, name: str, columns: str) -> None:
		self.db.execute(f"CREATE TEMP TABLE {name} ({columns})")
		self.tables.append(name)

	def setup(self) -> None:
		self.create("f2_timeline", "backward INTEGER PRIMARY KEY, object_format TEXT, digest BLOB, "
					"UNIQUE(object_format, digest)")
		self.create("f2_repr", "id TEXT PRIMARY KEY, language TEXT, own_payload BLOB, own_digest BLOB, "
					"body_payload BLOB, body_digest BLOB, subtree_payload BLOB, subtree_digest BLOB, "
					"body_count INTEGER, subtree_count INTEGER")
		self.create("f2_definfo", "id TEXT PRIMARY KEY, depth INTEGER, locator BLOB, parent_locator BLOB")
		for side in ("before", "after"):
			self.create("f2_files_" + side, _FILES)
			self.create("f2_" + side, _POPULATION)
			for suffix, columns in (
				("path", "path"), ("parent", "parent_id"), ("eid", "eid"),
				("locator", "locator"), ("names", "path, name, kind, language"),
				("role", "language, kind, synthetic_root, container"),
				("own", "language, kind, own_digest"), ("body", "language, kind, body_digest"),
				("subtree", "language, kind, subtree_digest"), ("depth", "depth, defkey"),
			):
				self.db.execute(f"CREATE INDEX f2_{side}_{suffix} ON f2_{side} ({columns})")
			self.db.execute(f"CREATE INDEX f2_files_{side}_blob ON f2_files_{side} "
							"(blob_format, blob_digest, mode, object_type)")
		self.create("f2_graph", "source TEXT, destination TEXT, method TEXT, evidence TEXT, "
					"PRIMARY KEY(source, destination)")
		self.db.execute("CREATE INDEX f2_graph_destination ON f2_graph(destination)")
		self.create("f2_decisions", "source TEXT, destination TEXT, method TEXT, status TEXT, "
					"reason TEXT, evidence TEXT, PRIMARY KEY(source, destination, method)")
		self.db.execute("CREATE INDEX f2_decisions_destination ON f2_decisions(destination, status)")

	def check(self) -> None:
		self.store.check_storage_limit(self.limits.max_storage_bytes)

	def one(self, query: str, parameters: tuple = ()) -> sqlite3.Row | None:
		with closing(self.db.execute(query, parameters)) as cursor:
			return cursor.fetchone()

	def scalar(self, query: str, parameters: tuple = ()):
		row = self.one(query, parameters)
		return None if row is None else row[0]

	def rows(self, query: str, parameters: tuple = ()) -> Iterator[sqlite3.Row]:
		cursor = self.db.execute(query, parameters)
		self.cursors.add(cursor)
		try:
			count = 0
			while (row := cursor.fetchone()) is not None:
				yield row
				count += 1
				if count % self.limits.page_size == 0:
					self.check()
		finally:
			cursor.close()
			self.cursors.discard(cursor)

	def close(self) -> None:
		failures = []
		for cursor in tuple(self.cursors):
			try:
				cursor.close()
			except sqlite3.Error as error:
				failures.append(error)
		self.cursors.clear()
		for table in reversed(self.tables):
			try:
				self.db.execute(f"DROP TABLE IF EXISTS temp.{table}")
			except sqlite3.Error as error:
				failures.append(error)
		if failures:
			raise failures[0]


@contextmanager
def _spool(store: IndexStore, limits: BuildLimits) -> Iterator[_Spool]:
	spool = _Spool(store, limits)
	try:
		spool.setup()
		yield spool
	except BaseException as error:
		try:
			spool.close()
		except Exception as cleanup_error:
			error.add_note(f"Spool cleanup also failed: {cleanup_error}")
		raise
	else:
		spool.close()


def _walk(repository: CommittedRepo, spool: _Spool, tip: GitOid,
		  since: GitOid | None) -> tuple[GitOid, str, int]:
	shallow = repository.shallow_boundaries()
	current = tip
	previous = None
	count = 0
	while True:
		if spool.one("SELECT 1 FROM f2_timeline WHERE object_format = ? AND digest = ?",
					 current.fields()) is not None:
			raise BuildError("invalid_history", "First-parent history contains a cycle")
		info = repository.object_info(current)
		if info is None:
			if previous is None:
				raise BuildError("object_missing", "Pinned tip is unavailable locally")
			anchor, boundary = previous, "missing_parent"
			break
		if info.size > 5 * 1024 * 1024:
			raise BuildError("resource_limit", "Commit object exceeds the 5 MiB read bound")
		try:
			committed = repository.commit(current)
		except GitObjectError as error:
			if error.code != "object_missing" or previous is None:
				raise
			anchor, boundary = previous, "missing_parent"
			break
		spool.store.put_commit(current, committed.tree, committed.parents, committed.message)
		spool.db.execute("INSERT INTO f2_timeline VALUES (?, ?, ?)", (count, *current.fields()))
		count += 1
		spool.check()
		if since == current:
			return current, "bounded", count
		if not committed.parents:
			anchor, boundary = current, "root"
			break
		if current in shallow:
			anchor, boundary = current, "shallow"
			break
		previous, current = current, committed.parents[0]
	if since is not None:
		raise BuildError("boundary_unavailable", "Requested boundary is not reachable on the first-parent chain")
	return anchor, boundary, count


def _walk_suffix(repository: CommittedRepo, spool: _Spool, tip: GitOid,
				 published: str, published_tip: GitOid) -> int:
	shallow = repository.shallow_boundaries()
	current = tip
	count = 0
	while current != published_tip:
		if spool.one(
			"SELECT 1 FROM membership WHERE generation_id = ? AND commit_format = ? AND commit_digest = ?",
			(published, *current.fields()),
		) is not None:
			raise BuildError("rebuild_required", "History diverges before the published first-parent tip")
		if spool.one("SELECT 1 FROM f2_timeline WHERE object_format = ? AND digest = ?",
					 current.fields()) is not None:
			raise BuildError("invalid_history", "First-parent history contains a cycle")
		info = repository.object_info(current)
		if info is None:
			raise BuildError("rebuild_required", "First-parent continuation is unavailable locally")
		if info.size > 5 * 1024 * 1024:
			raise BuildError("resource_limit", "Commit object exceeds the 5 MiB read bound")
		try:
			committed = repository.commit(current)
		except GitObjectError as error:
			if error.code != "object_missing":
				raise
			raise BuildError("rebuild_required", "First-parent continuation is unavailable locally") from error
		spool.store.put_commit(current, committed.tree, committed.parents, committed.message)
		spool.db.execute("INSERT INTO f2_timeline VALUES (?, ?, ?)", (count, *current.fields()))
		count += 1
		spool.check()
		if not committed.parents or current in shallow:
			raise BuildError("rebuild_required", "History does not reach the published first-parent tip")
		current = committed.parents[0]
	return count


def _ref_end(repository: CommittedRepo, ref: str, tip: GitOid) -> tuple[bool, GitOid | None]:
	try:
		current = repository.resolve(ref)
	except GitObjectError as error:
		if error.code not in ("git_command_failed", "invalid_revision", "object_missing"):
			raise
		return True, None
	return current != tip, current


def _status(spool: _Spool, generation: str) -> str:
	partial = spool.one(
		f"SELECT 1 FROM coverage WHERE generation_id = ? AND {model.UNCOVERED_SQL} LIMIT 1", (generation,),
	)
	return "partial" if partial is not None else "ready"


def build(repository: CommittedRepo, store: IndexStore, *, scope: str = "default", ref: str = "HEAD",
		  since: str | None = None, rebuild: bool = False, limits: BuildLimits = BuildLimits()) -> BuildResult:
	"""Build, continue, or explicitly rebuild a pinned first-parent timeline."""
	if type(limits) is not BuildLimits or type(rebuild) is not bool:
		raise BuildError("invalid_limits", "Expected BuildLimits and a boolean rebuild flag")
	limits.__post_init__()
	try:
		with store.transaction(), _spool(store, limits) as spool:
			previous = store.published_generation(scope)
			tip = repository.resolve(ref)
			try:
				boundary_oid = None if since is None else repository.resolve(since)
			except GitObjectError as error:
				code = "rebuild_required" if previous is not None and not rebuild else "boundary_unavailable"
				raise BuildError(code, "Cannot resolve the explicit committed boundary") from error
			parse_fp = evidence.parse_fingerprint(limits.parse_limits)
			with Path(__file__).open("rb") as stream:
				builder_digest = file_digest(stream, "sha256").digest()
			manifest = matching.match_manifest(builder_digest, limits.max_candidates, limits.max_pairs)
			match_fp = matching.match_fingerprint(manifest)
			prefix_count = 0
			predecessor = None
			if previous is not None and not rebuild:
				published = store.generation(previous)
				context = store._context(published["context_id"])
				if (context.parse_fingerprint != parse_fp or context.match_fingerprint != match_fp
						or context.limits != limits.policy()
						or (boundary_oid is not None and
							(boundary_oid != context.anchor or context.boundary != "bounded"))):
					raise BuildError("rebuild_required", "A different interpretation or boundary requires an explicit rebuild")
				membership = spool.one(
					"SELECT count(*), min(sequence), max(sequence) FROM membership WHERE generation_id = ?", (previous,),
				)
				if membership is None or membership[0] == 0 or membership[1] != 0 or membership[2] != membership[0] - 1:
					raise BuildError("corrupt", "Published membership is not contiguous")
				prefix_count = membership[0]
				first = store._member(previous, 0)
				last = store._member(previous, prefix_count - 1)
				if (published["state"] != "published"
						or store._row_oid(first, "commit") != context.anchor
						or store._row_oid(last, "commit") != published["tip"]
						or store._parents(context.anchor) != context.parents):
					raise BuildError("corrupt", "Published membership or anchor provenance is invalid")
				if published["tip"] == tip:
					moved, current = _ref_end(repository, ref, tip)
					return BuildResult(previous, scope, tip, context, _status(spool, previous), prefix_count, 0, 0,
									   moved, current)
				if spool.one(
					"SELECT 1 FROM membership WHERE generation_id = ? AND commit_format = ? AND commit_digest = ?",
					(previous, *tip.fields()),
				) is not None:
					raise BuildError("rebuild_required", "A shorter published timeline requires an explicit rebuild")
				count = prefix_count + _walk_suffix(repository, spool, tip, previous, published["tip"])
				predecessor = previous
			else:
				anchor, boundary, count = _walk(repository, spool, tip, boundary_oid)
				context = IdentityContext(anchor, boundary, store._parents(anchor), parse_fp, match_fp, limits.policy())
			store.put_interpretation("parse", parse_fp, (("fingerprint", parse_fp), ("policy", "byte-evidence-v1")))
			store.put_interpretation("match", match_fp, manifest)
			store.put_context(context)
			generation = store.begin_generation(scope, context, tip, predecessor=predecessor)
			with ExitStack() as resources:
				workers = tuple(resources.enter_context(EvidenceWorker(limits.parse_timeout_seconds))
								for _ in range(limits.workers))
				executor = (resources.enter_context(ThreadPoolExecutor(max_workers=limits.workers))
							if limits.workers > 1 else None)
				builder = _Builder(repository, spool, workers[0], generation, context, prefix_count=prefix_count,
								   workers=workers, executor=executor)
				for row in spool.rows("SELECT * FROM f2_timeline ORDER BY backward DESC"):
					builder.advance(count - row["backward"] - 1, GitOid(row["object_format"], row["digest"]))
			spool.check()
			store.publish_generation(generation, max_storage_bytes=limits.max_storage_bytes)
			moved, current = _ref_end(repository, ref, tip)
			spool.check()
			return BuildResult(generation, scope, tip, context, _status(spool, generation), count,
							   builder.parsed, builder.cached, moved, current)
	except GitObjectError as error:
		raise BuildError(error.code, str(error)) from error


class _Builder:
	def __init__(self, repository: CommittedRepo, spool: _Spool, worker: EvidenceWorker,
				 generation: str, context: IdentityContext, *, prefix_count: int = 0,
				 workers: tuple[EvidenceWorker, ...] = (), executor: ThreadPoolExecutor | None = None) -> None:
		self.repository = repository
		self.spool = spool
		self.db = spool.db
		self.store = spool.store
		self.limits = spool.limits
		self.worker = worker
		self.workers = workers or (worker,)
		self.executor = executor
		self._next_worker = 0
		self._pending: dict[bytes, tuple[GitObjectInfo, GitObject, Future[EvidenceBundle]]] = {}
		self.generation = generation
		self.context = context
		self.parsed = 0
		self.cached = 0
		self.sequence = 0
		self.commit = context.anchor
		self.parent: GitOid | None = None
		self.parents: tuple[GitOid, ...] = ()
		self.considered = 0
		self.limited = False
		self.gaps: dict[str, bool] = {}
		if prefix_count:
			self.sequence = prefix_count - 1
			member = self.store._member(generation, self.sequence)
			self.commit = GitOid(member["commit_format"], member["commit_digest"])
			self.parents = self.store._parents(self.commit)
			self.parent = self.parents[0] if self.sequence else None
			for row in self.spool.rows(
				"SELECT DISTINCT state.parse_id FROM frontier JOIN file_state state ON state.id = frontier.file_state_id "
				"WHERE frontier.generation_id = ? AND state.parse_id IS NOT NULL ORDER BY state.parse_id", (generation,),
			):
				result = self.store.get_parse(row["parse_id"])
				if result is None:
					raise BuildError("corrupt", "Stored frontier has no parse evidence")
				self._metadata(result)
				del result

	def _metadata(self, result: ParseResult, versions: tuple[EntityVersion, ...] = ()) -> None:
		supplied = {version.id: version for version in versions}
		for definition in sorted(result.definitions, key=lambda item: len(item.discriminator)):
			if self.spool.one("SELECT 1 FROM f2_repr WHERE id = ?", (definition.version_id,)) is None:
				version = supplied.get(definition.version_id) or self.store.get_version(definition.version_id)
				if version is None:
					raise BuildError("corrupt", "Stored definition has no version")
				values = []
				for part in (version.own, version.body, version.subtree):
					payload = matching.representation_payload(part)
					values.extend((payload, matching.representation_digest(payload)))
				self.db.execute("INSERT INTO f2_repr VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
								(definition.version_id, version.language, *values,
								 None if version.body is None else len(version.body),
								 None if version.subtree is None else len(version.subtree)))
			if self.spool.one("SELECT 1 FROM f2_definfo WHERE id = ?", (definition.id,)) is None:
				parent = None if definition.parent_id is None else self.spool.scalar(
					"SELECT locator FROM f2_definfo WHERE id = ?", (definition.parent_id,),
				)
				if definition.parent_id is not None and parent is None:
					raise BuildError("invalid_record", "Definition parent was not prepared first")
				locator = canonical_encode("structural-locator", (
					result.path, result.language, definition.kind, definition.names[-1], parent,
				))
				self.db.execute("INSERT INTO f2_definfo VALUES (?, ?, ?, ?)",
								(definition.id, len(definition.discriminator), locator, parent))

	def _parse(self, entry: GitTreeEntry) -> tuple[ParseResult | None, str]:
		pending = self._pending.pop(entry.path, None)
		if entry.object_type != "blob" or entry.mode not in (0o100644, 0o100755):
			return None, "non_source_object"
		parse_id = content_id("par1", (self.context.parse_fingerprint, entry.oid.fields(), entry.path))
		result = self.store.get_parse(parse_id)
		if result is not None:
			self.cached += 1
			self.store.put_coverage(self.generation, self.sequence, entry.path,
								"source_availability", "unchecked_cached_source", None)
			self._metadata(result)
			self.spool.check()
			return result, result.status.value
		info = pending[0] if pending is not None else self.repository.object_info(entry.oid)
		if info is None:
			return None, "missing_object"
		if info.object_type != "blob":
			raise BuildError("invalid_object", "Regular tree entry does not name a blob")
		if info.size > self.limits.parse_limits.max_blob_bytes:
			spec = evidence._spec_for_path(entry.path)
			result = ParseResult(self.context.parse_fingerprint, entry.oid, entry.path,
								 None if spec is None else spec.name, ParseStatus.OVERSIZED,
								 info.size, (), ("source exceeds max_blob_bytes",))
			self.store.put_parse(result, ())
			self._metadata(result)
		else:
			source = pending[1] if pending is not None else self.repository.read_object(entry.oid)
			if source is None:
				return None, "missing_object"
			if source.object_type != "blob" or len(source.data) != info.size:
				raise BuildError("invalid_object", "Blob metadata and payload disagree")
			bundle = (pending[2].result() if pending is not None else
					  self.worker.parse(source.data, entry.path, entry.oid.object_format, self.limits.parse_limits))
			self.parsed += 1
			result = bundle.result
			if (result.id != parse_id or result.parse_fingerprint != self.context.parse_fingerprint
					or result.blob != entry.oid or result.path != entry.path or result.byte_length != info.size):
				raise BuildError("parse_mismatch", "Worker returned a different source or parse interpretation")
			self.store.put_parse(result, bundle.versions)
			self._metadata(result, bundle.versions)
			del bundle, source
		if result.byte_length != info.size:
			raise BuildError("parse_mismatch", "Cached parse byte count disagrees with the committed object")
		self.spool.check()
		return result, result.status.value

	def _prefetch(self, page: tuple[GitTreeEntry, ...]) -> Iterator[GitTreeEntry]:
		if self.executor is None:
			yield from page
			return
		queued: deque[GitTreeEntry] = deque()
		try:
			for entry in page:
				before = self.spool.one("SELECT * FROM f2_files_before WHERE path = ?", (entry.path,))
				unchanged = (before is not None and before["status"] == "present"
							 and (before["blob_format"], before["blob_digest"], before["object_type"], before["mode"])
							 == (*entry.oid.fields(), entry.object_type, entry.mode))
				parse_id = content_id("par1", (self.context.parse_fingerprint, entry.oid.fields(), entry.path))
				if (not unchanged and entry.object_type == "blob" and entry.mode in (0o100644, 0o100755)
						and self.spool.one("SELECT 1 FROM parse_result WHERE id = ?", (parse_id,)) is None):
					info = self.repository.object_info(entry.oid)
					if info is not None and info.object_type == "blob" and info.size <= self.limits.parse_limits.max_blob_bytes:
						source = self.repository.read_object(entry.oid)
						if source is not None:
							worker = self.workers[self._next_worker % len(self.workers)]
							self._next_worker += 1
							future = self.executor.submit(worker.parse, source.data, entry.path,
														  entry.oid.object_format, self.limits.parse_limits)
							self._pending[entry.path] = (info, source, future)
				queued.append(entry)
				if len(queued) >= len(self.workers):
					yield queued.popleft()
			yield from queued
		finally:
			for _, _, future in self._pending.values():
				future.cancel()
			self._pending.clear()

	def _inventory(self) -> None:
		self.db.execute(
			"INSERT INTO f2_files_before SELECT state.path, state.blob_format, state.blob_digest, "
			"state.object_type, state.mode, state.parse_id, state.binding_set_id, state.id, state.status, "
			"(SELECT reason FROM coverage WHERE generation_id = state.generation_id "
			"AND sequence = state.from_seq AND path = state.path AND category = 'extraction'), 0 "
			"FROM frontier JOIN file_state state ON state.id = frontier.file_state_id "
			"WHERE frontier.generation_id = ?", (self.generation,),
		)
		with closing(self.repository.iter_tree(self.commit, page_size=self.limits.page_size)) as pages:
			for page in pages:
				for entry in self._prefetch(page):
					before = self.spool.one("SELECT * FROM f2_files_before WHERE path = ?", (entry.path,))
					same = (before is not None and before["status"] == "present"
							and (before["blob_format"], before["blob_digest"], before["object_type"], before["mode"])
							== (*entry.oid.fields(), entry.object_type, entry.mode))
					if same:
						self.db.execute("INSERT INTO f2_files_after SELECT * FROM f2_files_before WHERE path = ?",
										(entry.path,))
					else:
						result, reason = self._parse(entry)
						self.db.execute("INSERT INTO f2_files_after VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, 1)",
										(entry.path, *entry.oid.fields(), entry.object_type, entry.mode,
										 None if result is None else result.id,
										 "present" if result is not None and result.complete else "unknown", reason))
						del result
				self.spool.check()
		self.db.execute(
			"INSERT INTO f2_files_after SELECT path, NULL, NULL, NULL, NULL, NULL, NULL, "
			"CASE WHEN status = 'removed' THEN state_id END, 'removed', 'removed', "
			"CASE WHEN status = 'removed' THEN 0 ELSE 1 END FROM f2_files_before old "
			"WHERE NOT EXISTS (SELECT 1 FROM f2_files_after new WHERE new.path = old.path)",
		)
		self.db.execute("UPDATE f2_files_before SET changed = "
						"(SELECT changed FROM f2_files_after WHERE path = f2_files_before.path)")
		for side in ("before", "after"):
			self.db.execute(
				f"INSERT INTO f2_{side} SELECT definition.id, files.path, definition.parse_id, definition.version_id, "
				"definition.parent_id, definition.terminal_name, definition.kind, repr.language, definition.synthetic_root, "
				"EXISTS(SELECT 1 FROM definition child WHERE child.parent_id = definition.id), info.depth, info.locator, "
				"info.parent_locator, repr.own_payload, repr.own_digest, repr.body_payload, repr.body_digest, "
				"repr.subtree_payload, repr.subtree_digest, repr.body_count, repr.subtree_count, files.blob_format, "
				"files.blob_digest, files.object_type, files.mode, binding.eid, files.changed, NULL, NULL, NULL "
				f"FROM f2_files_{side} files JOIN definition ON definition.parse_id = files.parse_id "
				"JOIN version ON version.id = definition.version_id JOIN f2_repr repr ON repr.id = version.id "
				"JOIN f2_definfo info ON info.id = definition.id LEFT JOIN binding "
				"ON binding.binding_set_id = files.binding_set_id AND binding.definition_id = definition.id "
				"WHERE files.status = 'present'",
			)
		self.db.execute("UPDATE f2_before SET matched = defkey WHERE defkey IN "
						"(SELECT defkey FROM f2_after WHERE changed = 0)")
		self.db.execute("UPDATE f2_after SET matched = defkey WHERE changed = 0")

	def _gap(self, language: str) -> bool:
		if language in self.gaps:
			return self.gaps[language]
		excluded = ", ".join(f"'{value}'" for value in model.EXCLUDED_EXTRACTION)
		with closing(self.spool.rows(
			f"SELECT path FROM f2_files_before WHERE status = 'unknown' "
			f"AND reason NOT IN ({excluded}) UNION "
			f"SELECT path FROM f2_files_after WHERE status = 'unknown' "
			f"AND reason NOT IN ({excluded})",
		)) as rows:
			for row in rows:
				spec = evidence._spec_for_path(row[0])
				if spec is not None and spec.name == language:
					self.gaps[language] = True
					return True
		self.gaps[language] = False
		return False

	def _entity(self, side: str, identifier: str) -> sqlite3.Row:
		row = self.spool.one(f"SELECT * FROM f2_{side} WHERE defkey = ?", (identifier,))
		if row is None:
			raise BuildError("invalid_record", "Matching endpoint is outside the complete population")
		return row

	def _count(self, side: str, row: sqlite3.Row, part: str) -> int:
		return self.spool.scalar(
			f"SELECT count(*) FROM f2_{side} WHERE language = ? AND kind = ? AND synthetic_root = ? "
			f"AND container = ? AND {part}_digest = ? AND {part}_payload = ?",
			(row["language"], row["kind"], row["synthetic_root"], row["container"],
			 row[part + "_digest"], row[part + "_payload"]),
		)

	def _equivalents(self, side: str, row: sqlite3.Row, part: str) -> Iterator[sqlite3.Row]:
		return self.spool.rows(
			f"SELECT defkey FROM f2_{side} WHERE language = ? AND kind = ? AND synthetic_root = ? "
			f"AND container = ? AND {part}_digest = ? AND {part}_payload = ? ORDER BY defkey",
			(row["language"], row["kind"], row["synthetic_root"], row["container"],
			 row[part + "_digest"], row[part + "_payload"]),
		)

	def _uncertain(self, side: str, identifier: str, reason: str = "unmatched") -> None:
		self.db.execute(f"UPDATE f2_{side} SET uncertain = CASE "
						"WHEN uncertain IN ('ambiguous', 'kind_changed') THEN uncertain ELSE ? END WHERE defkey = ?",
						(reason, identifier))

	def _evidence(self, before: sqlite3.Row, after: sqlite3.Row, method: str,
				  old_count: int, new_count: int) -> tuple:
		part = {"exact_body": "body", "exact_subtree": "subtree"}.get(method, "own")
		if method == "exact_blob":
			old_digest = matching.representation_digest(canonical_encode("blob-entry", (
				before["blob_format"], before["blob_digest"], before["object_type"], before["mode"],
			)))
			new_digest = matching.representation_digest(canonical_encode("blob-entry", (
				after["blob_format"], after["blob_digest"], after["object_type"], after["mode"],
			)))
		else:
			old_digest, new_digest = before[part + "_digest"], after[part + "_digest"]
		complete = not self._gap(after["language"])
		count_known = complete or method == "structural_locator"
		population = "full-before-and-after-compatible-language-kind-role; unsupported-excluded"
		if method == "structural_locator":
			population = "full-before-and-after-structural-sibling-groups"
		elif method == "exact_blob":
			population = "full-before-and-after-file-entries; blob-type-mode"
		return tuple(sorted({
			"after_digest": new_digest,
			"before_digest": old_digest,
			"coverage": (("compatible_population_complete", int(complete)),
						 ("count_known", int(count_known)), ("omitted", 0 if count_known else None)),
			"destination_count": new_count,
			"policy": "strict-v1",
			"population": population,
			"source_count": old_count,
		}.items()))

	def _propose(self, before: sqlite3.Row, after: sqlite3.Row, method: str,
				 old_count: int = 1, new_count: int = 1,
				 *, sibling_counts: tuple[int, int] | None = None) -> None:
		evidence = self._evidence(before, after, method, old_count, new_count)
		if sibling_counts is not None:
			evidence = tuple(sorted({**dict(evidence),
				"matching_scope": "same-locator-established-parent-siblings",
				"sibling_source_count": sibling_counts[0],
				"sibling_destination_count": sibling_counts[1],
			}.items()))
		self.db.execute("INSERT OR IGNORE INTO f2_graph VALUES (?, ?, ?, ?)",
						(before["defkey"], after["defkey"], method,
						 _dump(evidence)))

	def _accept_graph(self) -> None:
		for pair in self.spool.rows(
			"SELECT graph.*, (SELECT count(*) FROM f2_graph other WHERE other.source = graph.source) old_degree, "
			"(SELECT count(*) FROM f2_graph other WHERE other.destination = graph.destination) new_degree "
			"FROM f2_graph graph ORDER BY source, destination",
		):
			if pair["old_degree"] != 1 or pair["new_degree"] != 1:
				self._uncertain("before", pair["source"], "ambiguous")
				self._uncertain("after", pair["destination"], "ambiguous")
				continue
			before = self._entity("before", pair["source"])
			after = self._entity("after", pair["destination"])
			if before["matched"] is not None or after["eid"] is not None:
				raise BuildError("invalid_record", "Frozen tier attempted to reuse an accepted endpoint")
			self.db.execute("UPDATE f2_before SET matched = ? WHERE defkey = ?",
							(pair["destination"], pair["source"]))
			self.db.execute("UPDATE f2_after SET matched = ?, eid = ? WHERE defkey = ?",
							(pair["source"], before["eid"], pair["destination"]))
			self.db.execute("INSERT INTO f2_decisions VALUES (?, ?, ?, 'accepted', 'strict_policy', ?)",
							(pair["source"], pair["destination"], pair["method"], pair["evidence"]))
		self.db.execute("DELETE FROM f2_graph")

	def _copy_guard(self, before: sqlite3.Row, after: sqlite3.Row) -> bool:
		if before["synthetic_root"]:
			return False
		parts = ("subtree",) if before["container"] else ("own", "body")
		count_field = "subtree_count" if before["container"] else "body_count"
		for part in parts:
			if (before[count_field] or 0) >= matching.MIN_BODY_TOKENS and self.spool.one(
				f"SELECT 1 FROM f2_after WHERE language = ? AND kind = ? AND {part}_digest = ? "
				f"AND {part}_payload = ? AND locator != ? AND synthetic_root = ? AND container = ? LIMIT 1",
				(before["language"], before["kind"], before[part + "_digest"],
				 before[part + "_payload"], before["locator"], before["synthetic_root"], before["container"]),
			) is not None:
				return True
		parts = ("subtree",) if after["container"] else ("own", "body")
		count_field = "subtree_count" if after["container"] else "body_count"
		for part in parts:
			if (after[count_field] or 0) >= matching.MIN_BODY_TOKENS and self.spool.one(
				f"SELECT 1 FROM f2_before other WHERE language = ? AND kind = ? AND {part}_digest = ? "
				f"AND {part}_payload = ? AND locator != ? AND synthetic_root = ? AND container = ? AND EXISTS "
				"(SELECT 1 FROM f2_after retained WHERE retained.locator = other.locator) LIMIT 1",
				(after["language"], after["kind"], after[part + "_digest"],
				 after[part + "_payload"], after["locator"], after["synthetic_root"], after["container"]),
			) is not None:
				return True
		return False

	def _exact_sibling(self, after: sqlite3.Row, parent_eid: str,
					   counts: tuple[int, int]) -> bool:
		part = "subtree" if after["container"] else "own"
		if after[part + "_payload"] is None or self._gap(after["language"]):
			return False
		if any(self._count(side, after, part) != 1 for side in ("before", "after")):
			return False
		with closing(self._equivalents("before", after, part)) as sources:
			before = self._entity("before", next(sources)[0])
		if (before["matched"] is not None or before["locator"] != after["locator"]
				or before["parent_id"] is None
				or self._entity("before", before["parent_id"])["eid"] != parent_eid):
			return False
		if self._copy_guard(before, after):
			self._refusal(before, after, "exact_" + part, "ambiguous", "copy_or_swap", 1, 1)
			return False
		self._propose(before, after, "exact_" + part, sibling_counts=counts)
		return True

	def _structural(self) -> None:
		for depth in self.spool.rows("SELECT DISTINCT depth FROM f2_after WHERE changed = 1 ORDER BY depth"):
			for target in self.spool.rows(
				"SELECT defkey FROM f2_after WHERE depth = ? AND changed = 1 AND eid IS NULL ORDER BY defkey",
				(depth[0],),
			):
				after = self._entity("after", target[0])
				parent_eid = None if after["parent_id"] is None else self.spool.scalar(
					"SELECT eid FROM f2_after WHERE defkey = ?", (after["parent_id"],),
				)
				if not after["synthetic_root"] and parent_eid is None:
					continue
				parameters = (after["path"], after["name"], after["kind"], after["language"], parent_eid)
				group = ("child.path = ? AND child.name = ? AND child.kind = ? AND child.language = ? "
						 "AND parent.eid IS ?")
				counts = [self.spool.scalar(
					f"SELECT count(*) FROM f2_{side} child LEFT JOIN f2_{side} parent ON parent.defkey = child.parent_id "
					f"WHERE {group}", parameters,
				) for side in ("before", "after")]
				if counts != [1, 1]:
					if (counts[0] and parent_eid is not None
							and self._exact_sibling(after, parent_eid, (counts[0], counts[1]))):
						continue
					if counts[0]:
						self._uncertain("after", after["defkey"], "ambiguous")
						self.db.execute("UPDATE f2_before SET uncertain = 'ambiguous' WHERE defkey IN ("
										"SELECT child.defkey FROM f2_before child LEFT JOIN f2_before parent "
										f"ON parent.defkey = child.parent_id WHERE {group})", parameters)
					continue
				source = self.spool.scalar(
					"SELECT child.defkey FROM f2_before child LEFT JOIN f2_before parent "
					f"ON parent.defkey = child.parent_id WHERE {group}", parameters,
				)
				before = self._entity("before", source)
				if before["matched"] is not None:
					continue
				if self._copy_guard(before, after):
					self._uncertain("before", before["defkey"], "ambiguous")
					self._uncertain("after", after["defkey"], "ambiguous")
					self._refusal(before, after, "structural_locator", "ambiguous", "copy_or_swap", 1, 1)
				else:
					self._propose(before, after, "structural_locator")
			self._accept_graph()

	def _blob_count(self, side: str, row: sqlite3.Row) -> int:
		return self.spool.scalar(
			f"SELECT count(*) FROM f2_files_{side} WHERE blob_format = ? AND blob_digest = ? "
			"AND object_type = ? AND mode = ?", (row["blob_format"], row["blob_digest"], row["object_type"], row["mode"]),
		)

	def _modules(self) -> None:
		for target in self.spool.rows("SELECT defkey FROM f2_after WHERE synthetic_root = 1 AND eid IS NULL ORDER BY defkey"):
			after = self._entity("after", target[0])
			old_count, new_count = [self._blob_count(side, after) for side in ("before", "after")]
			if not old_count:
				continue
			self._uncertain("after", after["defkey"], "ambiguous" if max(old_count, new_count) > 1 else "unmatched")
			self.db.execute("UPDATE f2_before SET uncertain = 'ambiguous' WHERE synthetic_root = 1 "
							"AND blob_format = ? AND blob_digest = ? AND mode = ? AND object_type = ?",
							(after["blob_format"], after["blob_digest"], after["mode"], after["object_type"]))
			if old_count != 1 or new_count != 1 or self._gap(after["language"]):
				continue
			source = self.spool.scalar(
				"SELECT defkey FROM f2_before WHERE synthetic_root = 1 AND blob_format = ? AND blob_digest = ? "
				"AND mode = ? AND object_type = ? AND language = ?",
				(after["blob_format"], after["blob_digest"], after["mode"], after["object_type"], after["language"]),
			)
			if source is None:
				continue
			before = self._entity("before", source)
			if before["matched"] is None and self.spool.one(
				"SELECT 1 FROM f2_files_after WHERE path = ? AND status != 'removed'", (before["path"],),
			) is None:
				self._propose(before, after, "exact_blob")
		self._accept_graph()

	def _exact(self, part: str, container: int) -> None:
		for target in self.spool.rows(
			"SELECT defkey FROM f2_after WHERE synthetic_root = 0 AND container = ? AND eid IS NULL ORDER BY defkey",
			(container,),
		):
			after = self._entity("after", target[0])
			if after[part + "_payload"] is None:
				continue
			old_count, new_count = [self._count(side, after, part) for side in ("before", "after")]
			if not old_count:
				continue
			reason = "ambiguous" if max(old_count, new_count) > 1 else "unmatched"
			self._uncertain("after", after["defkey"], reason)
			self.db.execute(f"UPDATE f2_before SET uncertain = coalesce(uncertain, ?) WHERE language = ? AND kind = ? "
							f"AND synthetic_root = 0 AND container = ? AND {part}_digest = ? AND {part}_payload = ?",
							(reason, after["language"], after["kind"], container,
							 after[part + "_digest"], after[part + "_payload"]))
			if old_count != 1 or new_count != 1 or self._gap(after["language"]):
				continue
			with closing(self._equivalents("before", after, part)) as sources:
				source = next(sources)
			before = self._entity("before", source[0])
			count_field = "subtree_count" if container else "body_count"
			if (before["matched"] is not None
					or min(before[count_field] or 0, after[count_field] or 0) < matching.MIN_BODY_TOKENS):
				continue
			if self.spool.one("SELECT 1 FROM f2_after WHERE locator = ? LIMIT 1", (before["locator"],)) is not None:
				continue
			if before[part + "_payload"] != after[part + "_payload"]:
				raise BuildError("invalid_record", "Exact representation lookup did not preserve full equality")
			self._propose(before, after, "exact_" + part, old_count, new_count)
		self._accept_graph()

	def _limit(self) -> None:
		self.limited = True
		self.store.put_coverage(self.generation, self.sequence, None, "matching", "candidate_limit", None)

	def _room(self, destination: str) -> bool:
		retained = self.spool.scalar("SELECT count(*) FROM f2_decisions WHERE destination = ? AND status != 'accepted'",
									 (destination,))
		if self.considered >= self.limits.max_pairs or retained >= self.limits.max_candidates:
			self._limit()
			return False
		return True

	def _refusal(self, before: sqlite3.Row, after: sqlite3.Row, method: str, status: str,
				 reason: str, old_count: int, new_count: int, ratio: tuple[int, int] | None = None,
				 *, counted: bool = False) -> None:
		if not counted:
			if not self._room(after["defkey"]):
				return
			self.considered += 1
		details = dict(self._evidence(before, after, method, old_count, new_count))
		if reason == "module_blob_changed":
			details.update(
				representation="subtree", before_digest=before["subtree_digest"], after_digest=after["subtree_digest"],
				before_payload=before["subtree_payload"], after_payload=after["subtree_payload"], module_blob_equal=0,
			)
		if ratio is not None:
			details.update(numerator=ratio[0], denominator=ratio[1], threshold=matching.SIMILARITY_THRESHOLD)
		self.db.execute("INSERT OR IGNORE INTO f2_decisions VALUES (?, ?, ?, ?, ?, ?)",
						(before["defkey"], after["defkey"], method, status, reason, _dump(tuple(sorted(details.items())))))
		unresolved = "kind_changed" if reason == "kind_changed" else "ambiguous" if status == "ambiguous" else "unmatched"
		self._uncertain("before", before["defkey"], unresolved)
		self._uncertain("after", after["defkey"], unresolved)

	def _suggest(self, before: sqlite3.Row, after: sqlite3.Row) -> None:
		if before["kind"] != after["kind"]:
			if (before["path"], before["name"], before["parent_locator"]) == (
				after["path"], after["name"], after["parent_locator"],
			):
				self._refusal(before, after, "similarity", "ambiguous", "kind_changed",
							  self._count("before", before, "own"), self._count("after", after, "own"), counted=True)
			return
		method = None
		old_count = new_count = 0
		if after["synthetic_root"]:
			if (before["blob_format"], before["blob_digest"], before["object_type"], before["mode"]) == (
				after["blob_format"], after["blob_digest"], after["object_type"], after["mode"],
			):
				method = "exact_blob"
				old_count, new_count = [self._blob_count(side, after) for side in ("before", "after")]
		else:
			parts = ("subtree", "own") if after["container"] or before["container"] else ("own", "body")
			for part in parts:
				if before[part + "_payload"] is not None and before[part + "_payload"] == after[part + "_payload"]:
					method = "exact_" + part
					old_count, new_count = [self._count(side, after, part) for side in ("before", "after")]
					break
		if method is not None:
			status, reason = "rejected", "locator_occupied"
			if max(old_count, new_count) > 1 or before["matched"] is not None or before["uncertain"] == "ambiguous":
				status, reason = "ambiguous", "duplicate_or_retained_source"
			elif self._gap(after["language"]):
				reason = "incomplete_population"
			elif before["container"] != after["container"]:
				reason = "container_role_changed"
			elif after["container"] and method == "exact_own":
				reason = "subtree_mismatch"
			elif not after["synthetic_root"] and min(
				before["subtree_count" if after["container"] else "body_count"] or 0,
				after["subtree_count" if after["container"] else "body_count"] or 0,
			) < matching.MIN_BODY_TOKENS:
				reason = "stub"
			elif self.spool.one("SELECT 1 FROM f2_after WHERE locator = ? LIMIT 1", (before["locator"],)) is not None:
				status, reason = "ambiguous", "copy_or_swap"
			self._refusal(before, after, method, status, reason, old_count, new_count, counted=True)
			return
		if after["synthetic_root"]:
			if (before["matched"] is None and before["path"] != after["path"]
					and before["subtree_payload"] is not None
					and before["subtree_payload"] == after["subtree_payload"]
					and min(before["subtree_count"] or 0, after["subtree_count"] or 0) >= matching.MIN_BODY_TOKENS
					and self.spool.one("SELECT 1 FROM f2_files_after WHERE path = ? AND status != 'removed'",
									   (before["path"],)) is None
					and self.spool.one("SELECT 1 FROM f2_files_before WHERE path = ? AND status != 'removed'",
									   (after["path"],)) is None):
				self._refusal(before, after, "similarity", "candidate", "module_blob_changed",
							  self._count("before", before, "subtree"), self._count("after", after, "subtree"),
							  (1, 1), counted=True)
			return
		old_version = self.store.get_version(before["version_id"])
		new_version = self.store.get_version(after["version_id"])
		if old_version is None or new_version is None:
			raise BuildError("corrupt", "Candidate representation is unavailable")
		numerator, denominator = matching.similarity(old_version.own, new_version.own)
		if denominator and 5 * numerator >= 3 * denominator:
			self._refusal(before, after, "similarity", "candidate", "normalized_token_similarity",
						  self._count("before", before, "own"), self._count("after", after, "own"),
						  (numerator, denominator), counted=True)

	def _suggestions(self) -> None:
		for target in self.spool.rows("SELECT defkey FROM f2_after WHERE eid IS NULL ORDER BY locator, defkey"):
			after = self._entity("after", target[0])
			with closing(self.spool.rows(
				"SELECT defkey FROM f2_before WHERE language = ? AND synthetic_root = ? "
				"AND (kind = ? OR (path = ? AND name = ?)) ORDER BY locator, defkey",
				(after["language"], after["synthetic_root"], after["kind"], after["path"], after["name"]),
			)) as sources:
				for source in sources:
					if self.spool.one("SELECT 1 FROM f2_decisions WHERE source = ? AND destination = ?",
									  (source[0], after["defkey"])) is not None:
						continue
					if not self._room(after["defkey"]):
						break
					self.considered += 1
					self._suggest(self._entity("before", source[0]), after)
		self.db.execute(
			"UPDATE f2_decisions AS decision SET status = 'ambiguous', reason = 'competing_candidates' "
			"WHERE status = 'candidate' AND ((SELECT count(DISTINCT destination) FROM f2_decisions other "
			"WHERE other.source = decision.source AND other.status != 'accepted') > 1 "
			"OR (SELECT count(DISTINCT source) FROM f2_decisions other WHERE other.destination = decision.destination "
			"AND other.status != 'accepted') > 1)",
		)
		for side, endpoint in (("before", "source"), ("after", "destination")):
			self.db.execute(f"UPDATE f2_{side} SET uncertain = 'ambiguous' WHERE defkey IN "
							f"(SELECT {endpoint} FROM f2_decisions WHERE status = 'ambiguous') "
							"AND uncertain IS NOT 'kind_changed'")

	def _seed_reason(self, row: sqlite3.Row) -> str:
		if self.sequence == 0:
			if self.context.boundary != "root":
				return self.context.boundary
			return "parse_gap" if self._gap(row["language"]) else "root"
		if len(self.parents) > 1:
			return "side_parent_origin_unresolved"
		last_status = self.spool.scalar(
			"SELECT status FROM file_state WHERE generation_id = ? AND path = ? AND status != 'removed' "
			"ORDER BY from_seq DESC LIMIT 1", (self.generation, row["path"]),
		)
		if last_status == "unknown" or self._gap(row["language"]):
			return "parse_gap"
		if row["uncertain"] is not None:
			return row["uncertain"]
		if self.limited or self.context.boundary != "root":
			return "unmatched"
		return "observed_addition_with_complete_predecessor"

	def _occurrence(self, row: sqlite3.Row, commit: GitOid) -> EntityOccurrence:
		occurrence = EntityOccurrence(commit, row["path"], GitOid(row["blob_format"], row["blob_digest"]),
									  row["object_type"], row["mode"], row["defkey"])
		self.store.put_occurrence(occurrence)
		return occurrence

	def _seeds(self) -> None:
		for target in self.spool.rows("SELECT defkey FROM f2_after WHERE eid IS NULL ORDER BY depth, defkey"):
			row = self._entity("after", target[0])
			reason = self._seed_reason(row)
			occurrence = self._occurrence(row, self.commit)
			digest = None if reason in ("root", "observed_addition_with_complete_predecessor") else blake2b(
				canonical_encode("seed-evidence", (reason, int(self.limited), int(self._gap(row["language"])),
													row["uncertain"])), digest_size=32,
			).digest()
			seed = LineageSeed(self.context.id, self.commit, occurrence.id, reason, digest)
			self.store.put_lineage(seed)
			self.db.execute("UPDATE f2_after SET eid = ?, seed_reason = ? WHERE defkey = ?", (seed.id, reason, target[0]))

	def _states(self) -> None:
		for state in self.spool.rows(
			"SELECT old.state_id FROM f2_files_before old JOIN f2_files_after new ON new.path = old.path WHERE new.changed = 1",
		):
			self.store.close_file_state(self.generation, state[0], self.sequence)
		for row in self.spool.rows("SELECT * FROM f2_files_after WHERE changed = 1 ORDER BY path"):
			binding_set = None
			if row["status"] == "present":
				members = tuple(tuple(member) for member in self.spool.rows(
					"SELECT defkey, eid FROM f2_after WHERE path = ? ORDER BY defkey", (row["path"],),
				))
				binding_set = self.store.put_binding_set(row["parse_id"], members)
				del members
			blob = None if row["blob_format"] is None else GitOid(row["blob_format"], row["blob_digest"])
			state = self.store.put_file_state(self.generation, row["path"], self.sequence, None,
											  row["status"], blob, row["object_type"], row["mode"], row["parse_id"], binding_set)
			self.store.set_frontier(self.generation, row["path"], state)
			self.store.put_coverage(self.generation, self.sequence, row["path"], "extraction", row["reason"],
									0 if row["status"] != "unknown" else None)

	def _definition(self, identifier: str) -> EntityDefinition:
		row = self.store._definition(identifier)
		return EntityDefinition(row["parse_id"], row["version_id"], SourceSpan(
			row["start_byte"], row["end_byte"], row["start_line"], row["end_line"],
		), row["kind"], _load(row["names_json"]), row["parent_id"],
			_load(row["discriminator_json"]), bool(row["synthetic_root"]))

	def _parent_identity(self, side: str, row: sqlite3.Row) -> str | None:
		if row["parent_id"] is None:
			return None
		identifier = self.spool.scalar(f"SELECT eid FROM f2_{side} WHERE defkey = ?", (row["parent_id"],))
		if identifier is None:
			raise BuildError("invalid_record", "Classification requires an established enclosing binding")
		return identifier

	def _event(self, eid: str, source: str | None, destination: str | None,
			   flags: tuple[str, ...], decision: str | None = None) -> None:
		if flags:
			event = ChangeEvent(self.context.id, eid, self.parent, self.commit, source, destination, flags, decision)
			self.store.put_event(event)
			self.store.attach_event(self.generation, self.sequence, event.id)

	def _links(self) -> None:
		for pair in self.spool.rows("SELECT * FROM f2_decisions ORDER BY source, destination, method"):
			if self.parent is None:
				raise BuildError("invalid_record", "Anchor cannot carry predecessor links")
			before = self._entity("before", pair["source"])
			after = self._entity("after", pair["destination"])
			source, destination = self._occurrence(before, self.parent), self._occurrence(after, self.commit)
			source_parent = self._parent_identity("before", before)
			destination_parent = self._parent_identity("after", after)
			details = dict(_load(pair["evidence"]))
			details["enclosing_location_changed"] = int(source_parent != destination_parent)
			if self.limited and pair["status"] != "accepted":
				details["candidate_coverage"] = (("count_known", 0), ("omitted", None))
			link = LineageLink(self.context.id, self.parent, self.commit, source.id, destination.id,
							   LinkStatus(pair["status"]), pair["method"], pair["reason"], tuple(sorted(details.items())))
			self.store.put_link(link)
			self.store.attach_link(self.generation, link.id)
			if link.status is LinkStatus.ACCEPTED:
				before_version = self.store.get_version(before["version_id"])
				after_version = self.store.get_version(after["version_id"])
				if before_version is None or after_version is None:
					raise BuildError("corrupt", "Accepted endpoint has no stored version")
				flags = matching.classify_transition(
					source, destination, self._definition(before["defkey"]), self._definition(after["defkey"]),
					before_version, after_version, source_parent_identity=source_parent,
					destination_parent_identity=destination_parent,
				)
				self._event(before["eid"], source.id, destination.id, flags, link.id)

	def _births_and_departures(self) -> None:
		for target in self.spool.rows("SELECT defkey FROM f2_after WHERE seed_reason IS NOT NULL ORDER BY defkey"):
			row = self._entity("after", target[0])
			occurrence = self._occurrence(row, self.commit)
			self._event(row["eid"], None, occurrence.id, ("added" if row["eid"].startswith("eid1_") else "observed",))
		for source in self.spool.rows("SELECT defkey FROM f2_before WHERE matched IS NULL ORDER BY defkey"):
			row = self._entity("before", source[0])
			if self.parent is None:
				raise BuildError("invalid_record", "Departure requires a first-parent edge")
			if self.spool.one("SELECT 1 FROM f2_after WHERE eid = ?", (row["eid"],)) is not None:
				raise BuildError("invalid_record", "An unmatched source still occupies the destination population")
			unknown = (self.limited or row["uncertain"] is not None or self._gap(row["language"])
					   or len(self.parents) > 1)
			status = "unknown" if unknown else "removed"
			reason = "identity_unresolved_or_coverage_gap" if unknown else "complete_structural_removal"
			occurrence = self._occurrence(row, self.parent)
			self.store.put_departure(self.generation, self.sequence, row["eid"], occurrence.id, status, reason)
			self._event(row["eid"], occurrence.id, None, ("lineage_gap" if unknown else "removed",))

	def advance(self, sequence: int, commit: GitOid) -> None:
		self.sequence, self.commit = sequence, commit
		self.parents = self.store._parents(commit)
		self.parent = self.parents[0] if sequence else None
		self.considered, self.limited = 0, False
		self.gaps.clear()
		for table in ("f2_before", "f2_after", "f2_files_before", "f2_files_after", "f2_graph", "f2_decisions"):
			self.db.execute(f"DELETE FROM {table}")
		self.store.add_member(self.generation, sequence, commit)
		self._inventory()
		if sequence:
			self._structural()
			self._modules()
			self._exact("subtree", 1)
			self._exact("own", 0)
			self._exact("body", 0)
			self._suggestions()
		self._seeds()
		self._states()
		self._links()
		self._births_and_departures()
		self.spool.check()