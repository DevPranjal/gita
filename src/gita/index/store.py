"""Persistent evidence and transactional, immutable generation checkpoints."""

from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import sqlite3
from sys import getsizeof
from typing import Any, Iterator, TYPE_CHECKING
from uuid import uuid4

from .model import (
    CanonicalValue, ChangeEvent, EntityOccurrence, IdentityContext, LineageLink,
    LineageSeed, LinkStatus, _digest, _named_pairs, _oids, _path, _require, _text, _tuple,
    EntityDefinition, EntityVersion, GitOid, IndexContractError, ParseResult,
    ParseStatus, SourceSpan, _identifier, canonical_encode, content_id, require_same_payload,
)
from .schema import COLUMNS as _GENERATION_COLUMNS, SCHEMA as _GENERATION_SCHEMA

if TYPE_CHECKING:
    from ..vcs.git import Repo


SCHEMA_VERSION = 2
_APPLICATION_ID = 0x47495441
_METADATA = {"schema_version": "2", "cache_format": "generation-cache-v2"}
_PERFORMANCE_INDEXES = {
    "parse_result_metadata": "CREATE INDEX parse_result_metadata ON parse_result "
    "(id, path, blob_format, blob_digest, parse_fp, status, byte_length)",
}
_COLUMNS = {
    "metadata": "key value",
    "version": "id canonical_payload fields_json parse_fp kind",
    "parse_result": (
        "id canonical_payload fields_json parse_fp blob_format blob_digest path status byte_length"
    ),
    "definition": (
        "id parse_id version_id parent_id start_byte end_byte start_line end_line "
        "kind names_json discriminator_json synthetic_root ordinal terminal_name"
    ),
    **_GENERATION_COLUMNS,
}
_SCHEMA = (
    """CREATE TABLE metadata (
        key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL
    )""",
    """CREATE TABLE version (
        id TEXT PRIMARY KEY NOT NULL,
        canonical_payload BLOB NOT NULL CHECK(typeof(canonical_payload) = 'blob'),
        fields_json TEXT NOT NULL CHECK(typeof(fields_json) = 'text'),
        parse_fp BLOB NOT NULL CHECK(typeof(parse_fp) = 'blob' AND length(parse_fp) = 32),
        kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) > 0)
    )""",
    """CREATE TABLE parse_result (
        id TEXT PRIMARY KEY NOT NULL,
        canonical_payload BLOB NOT NULL CHECK(typeof(canonical_payload) = 'blob'),
        fields_json TEXT NOT NULL CHECK(typeof(fields_json) = 'text'),
        parse_fp BLOB NOT NULL CHECK(typeof(parse_fp) = 'blob' AND length(parse_fp) = 32),
        blob_format TEXT NOT NULL CHECK(blob_format IN ('sha1', 'sha256')),
        blob_digest BLOB NOT NULL CHECK(typeof(blob_digest) = 'blob' AND
            length(blob_digest) = CASE blob_format WHEN 'sha1' THEN 20 ELSE 32 END),
        path BLOB NOT NULL CHECK(typeof(path) = 'blob' AND length(path) > 0),
        status TEXT NOT NULL CHECK(status IN ('complete', 'unsupported_language',
            'unsupported_encoding', 'parse_error', 'oversized', 'definition_limit',
            'representation_limit')),
        byte_length INTEGER NOT NULL CHECK(typeof(byte_length) = 'integer' AND byte_length >= 0)
    )""",
    """CREATE TABLE definition (
        id TEXT PRIMARY KEY NOT NULL,
        parse_id TEXT NOT NULL REFERENCES parse_result(id),
        version_id TEXT NOT NULL REFERENCES version(id),
        parent_id TEXT REFERENCES definition(id),
        start_byte INTEGER NOT NULL CHECK(typeof(start_byte) = 'integer' AND start_byte >= 0),
        end_byte INTEGER NOT NULL CHECK(typeof(end_byte) = 'integer' AND end_byte >= start_byte),
        start_line INTEGER NOT NULL CHECK(typeof(start_line) = 'integer' AND start_line >= 1),
        end_line INTEGER NOT NULL CHECK(typeof(end_line) = 'integer' AND end_line >= start_line),
        kind TEXT NOT NULL CHECK(typeof(kind) = 'text' AND length(kind) > 0),
        names_json TEXT NOT NULL CHECK(typeof(names_json) = 'text'),
        discriminator_json TEXT NOT NULL CHECK(typeof(discriminator_json) = 'text'),
        synthetic_root INTEGER NOT NULL CHECK(synthetic_root IN (0, 1)),
        ordinal INTEGER NOT NULL CHECK(typeof(ordinal) = 'integer' AND ordinal >= 0),
        terminal_name TEXT NOT NULL CHECK(typeof(terminal_name) = 'text' AND length(terminal_name) > 0),
        UNIQUE(parse_id, ordinal),
        CHECK(synthetic_root = 0 OR parent_id IS NULL),
        CHECK(start_byte != end_byte OR start_line = end_line)
    )""",
    "CREATE INDEX definition_version ON definition(version_id)",
    "CREATE INDEX definition_parent ON definition(parent_id)",
) + _GENERATION_SCHEMA + tuple(_PERFORMANCE_INDEXES.values())


@lru_cache(maxsize=1)
def _schema_manifest(version: int) -> dict[tuple[str, str], str | None]:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in _SCHEMA:
            connection.execute(statement)
        return {(row[0], row[1]): row[2] for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master",
        )}
    finally:
        connection.close()


class IndexStoreError(RuntimeError):
    """A classified storage failure that never requests automatic replacement."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _storage_error(error: sqlite3.Error | OSError, path: Path | None) -> IndexStoreError:
    primary = getattr(error, "sqlite_errorcode", 0) & 0xFF
    if primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        code = "busy"
    elif primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        code = "corrupt"
    elif primary in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM, sqlite3.SQLITE_AUTH):
        code = "permission_denied"
    elif isinstance(error, PermissionError):
        code = "permission_denied"
    else:
        code = "io_error"
    if primary == sqlite3.SQLITE_CANTOPEN and path is not None:
        try:
            path.stat()
        except FileNotFoundError:
            code = "not_built"
        except PermissionError:
            code = "permission_denied"
        except OSError:
            pass
    location = f" ({path})" if path is not None else ""
    return IndexStoreError(code, f"{error}{location}")


@contextmanager
def _storage_errors(path: Path | None = None) -> Iterator[None]:
    try:
        yield
    except (sqlite3.Error, OSError) as error:
        raise _storage_error(error, path) from error


@contextmanager
def _record_errors() -> Iterator[None]:
    with _storage_errors():
        try:
            yield
        except (ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError) as error:
            raise IndexStoreError("corrupt", f"Invalid stored evidence: {error}") from error


def _valid(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _pack(value: Any) -> Any:
    if type(value) is bytes:
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if type(value) is tuple:
        if all(type(item) is str for item in value):
            return ["tuple", list(value)]
        return ["tuple", [_pack(item) for item in value]]
    if value is None or type(value) in (str, int):
        return value
    raise IndexContractError("invalid_record", "unsupported evidence JSON value")


def _unpack(value: Any, depth: int = 0) -> Any:
    _valid(depth <= 64, "evidence JSON exceeds maximum depth")
    if value is None or type(value) in (str, int):
        return value
    _valid(type(value) is list and len(value) == 2, "invalid evidence JSON tag")
    tag, payload = value
    if tag == "bytes":
        _valid(type(payload) is str, "invalid bytes JSON payload")
        return base64.b64decode(payload, validate=True)
    _valid(tag == "tuple" and type(payload) is list, "unknown evidence JSON tag")
    if depth < 64 and all(type(item) is str for item in payload):
        return tuple(payload)
    return tuple(_unpack(item, depth + 1) for item in payload)


def _dump(fields: tuple[Any, ...]) -> str:
    return json.dumps(_pack(fields), ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _load(document: str) -> tuple[Any, ...]:
    _valid(type(document) is str, "expected evidence JSON text")
    fields = _unpack(json.loads(document))
    _valid(type(fields) is tuple, "expected evidence fields tuple")
    return fields


def _timeout(value: int) -> None:
    if type(value) is not int or not 0 <= value <= 2000:
        raise IndexContractError("invalid_record", "busy_timeout_ms must be an integer from 0 to 2000")


def _sqlite_bounds(*values: int) -> None:
    if any(type(value) is not int or not 0 <= value < 1 << 63 for value in values):
        raise IndexContractError("invalid_record", "source bounds exceed SQLite signed integer capacity")


def _parent_first(result: ParseResult) -> list[tuple[int, EntityDefinition]]:
    children: dict[str, list[tuple[int, EntityDefinition]]] = {}
    ordered = []
    for ordinal, definition in enumerate(result.definitions):
        item = (ordinal, definition)
        if definition.parent_id is None:
            ordered.append(item)
        else:
            children.setdefault(definition.parent_id, []).append(item)
    for _, definition in ordered:
        ordered.extend(children.get(definition.id, ()))
    if len(ordered) != len(result.definitions):
        raise IndexContractError("invalid_record", "definition parents contain a cycle or missing parent")
    return ordered


class _DecodedRecords:
    """Bounded immutable decodes, reused only after rereading every SQL column."""

    def __init__(self, max_bytes: int = 32 * 1024 ** 2, max_entries: int = 4096) -> None:
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self.entries: OrderedDict[tuple[str, str], tuple[tuple, Any, int]] = OrderedDict()
        self.bytes = 0

    def get(self, table: str, row: sqlite3.Row):
        key = (table, row["id"])
        cached = self.entries.get(key)
        if cached is None:
            return None
        if cached[0] != tuple(row):
            self.bytes -= self.entries.pop(key)[2]
            return None
        self.entries.move_to_end(key)
        return cached[1]

    def put(self, table: str, row: sqlite3.Row, value) -> None:
        key, snapshot = (table, row["id"]), tuple(row)
        seen: set[int] = set()
        pending = [key, snapshot, value, value.fields()]
        size = 0
        while pending:
            item = pending.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            size += getsizeof(item)
            if size > self.max_bytes:
                return
            if type(item) is tuple:
                pending.extend(item)
        size += getsizeof((snapshot, value, size)) + 128  # OrderedDict entry overhead
        if size > self.max_bytes or not self.max_entries:
            return
        if key in self.entries:
            self.bytes -= self.entries.pop(key)[2]
        while self.entries and (self.bytes + size > self.max_bytes
                                or len(self.entries) >= self.max_entries):
            _, (_, _, previous_size) = self.entries.popitem(last=False)
            self.bytes -= previous_size
        self.entries[key] = (snapshot, value, size)
        self.bytes += size


class IndexStore:
    """Non-destructive SQLite lifecycle, evidence, and generation operations."""

    def __init__(self, path: Path, connection: sqlite3.Connection, *, read_only: bool) -> None:
        self.path = path
        self.connection = connection
        self.read_only = read_only
        self._writer_owned = False
        self._scope_active = False
        self._gc_authorized = False
        self._decoded = _DecodedRecords()
        self._parse_metadata_hint = (
            " INDEXED BY parse_result_metadata" if self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'parse_result_metadata'",
            ).fetchone() is not None else ""
        )
        self.connection.set_authorizer(self._authorize)

    def _authorize(
        self, action: int, first: str | None, second: str | None,
        database: str | None, trigger: str | None,
    ) -> int:
        if first == "gc_guard":
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                return sqlite3.SQLITE_OK if self._gc_authorized else sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_TABLE,
                          sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_VIEW):
                return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_ALTER_TABLE and second == "gc_guard":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @staticmethod
    def locate(repo: Repo) -> Path:
        """Resolve the common Git directory relative to this Repo's invocation."""
        from ..vcs.git import GitError

        try:
            output = repo._run("rev-parse", "--path-format=absolute", "--git-common-dir")
        except GitError:
            output = repo._run("rev-parse", "--git-common-dir")
        if not output or output.startswith(b"--path-format=absolute\n"):
            output = repo._run("rev-parse", "--git-common-dir")
        raw_path = output.removesuffix(b"\n")
        if not raw_path or b"\0" in raw_path:
            raise IndexStoreError("io_error", "Git returned an invalid common directory")
        common = Path(os.fsdecode(raw_path))
        if not common.is_absolute():
            common = Path(repo.root).resolve() / common
        return (common / "gita" / "index.sqlite").resolve()

    @classmethod
    def create(cls, path: str | os.PathLike[str], *, busy_timeout_ms: int = 2000) -> IndexStore:
        """Reserve a new file exclusively; leave failed creations intact for diagnosis."""
        _timeout(busy_timeout_ms)
        path = Path(path).absolute()
        with _storage_errors(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError as error:
                raise IndexStoreError("exists", f"Index already exists: {path}") from error
            os.close(descriptor)
            connection = sqlite3.connect(
                path.as_uri() + "?mode=rw", uri=True, isolation_level=None,
                timeout=busy_timeout_ms / 1000,
            )
            try:
                connection.row_factory = sqlite3.Row
                cls._configure(connection, busy_timeout_ms)
                if connection.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() != "wal":
                    raise IndexStoreError("io_error", "SQLite could not enable WAL")
                store = cls(path, connection, read_only=False)
                with store.transaction():
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.executemany("INSERT INTO metadata VALUES (?, ?)", _METADATA.items())
                    connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                store._parse_metadata_hint = " INDEXED BY parse_result_metadata"
                cls._validate_schema(connection)
                issues = store.check_integrity()
                if issues:
                    raise IndexStoreError("corrupt", "; ".join(issues))
                return store
            except BaseException:
                connection.close()
                raise

    @classmethod
    def open(
        cls, path: str | os.PathLike[str], *, read_only: bool = True, busy_timeout_ms: int = 2000,
    ) -> IndexStore:
        """Open an existing compatible file without DDL, migration, or journal changes."""
        _timeout(busy_timeout_ms)
        if type(read_only) is not bool:
            raise IndexContractError("invalid_record", "read_only must be bool")
        path = Path(path).absolute()
        with _storage_errors(path):
            try:
                path.stat()
            except FileNotFoundError as error:
                raise IndexStoreError("not_built", f"Index has not been built: {path}") from error
            mode = "ro" if read_only else "rw"
            connection = sqlite3.connect(
                path.as_uri() + f"?mode={mode}", uri=True, isolation_level=None,
                timeout=busy_timeout_ms / 1000,
            )
            try:
                connection.row_factory = sqlite3.Row
                cls._validate_schema(connection)
                if not read_only:
                    journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
                    if journal.lower() != "wal":
                        raise IndexStoreError("schema_mismatch", "Writable indexes require existing WAL mode")
                cls._configure(connection, busy_timeout_ms)
                return cls(path, connection, read_only=read_only)
            except BaseException:
                connection.close()
                raise

    @staticmethod
    def _configure(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA main.cache_size = -65536")
        connection.execute("PRAGMA temp.cache_size = -65536")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise IndexStoreError("io_error", "SQLite foreign-key enforcement is unavailable")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if application != _APPLICATION_ID or version != SCHEMA_VERSION:
            raise IndexStoreError("schema_mismatch", "Unrecognized index application or schema version")
        manifest = {(row[0], row[1]): row[2] for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master",
        )}
        expected = _schema_manifest(SCHEMA_VERSION)
        expected = {key: value for key, value in expected.items()
                if key[0] != "index" or key[1] not in _PERFORMANCE_INDEXES or key in manifest}
        if manifest != expected:
            raise IndexStoreError("schema_mismatch", "Index schema definitions are incompatible")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'",
        )}
        if not tables.issuperset(_COLUMNS):
            raise IndexStoreError("schema_mismatch", "Index evidence tables are missing")
        for table, expected in _COLUMNS.items():
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            if [row[1] for row in columns] != expected.split() or columns[0][5] != 1:
                raise IndexStoreError("schema_mismatch", f"Unexpected {table} columns or primary key")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if any(metadata.get(key) != value for key, value in _METADATA.items()):
            raise IndexStoreError("schema_mismatch", "Index cache metadata is incompatible")
        if connection.execute("SELECT 1 FROM gc_guard LIMIT 1").fetchone() is not None:
            raise IndexStoreError("corrupt", "Index contains an unfinished collection guard")
        foreign_keys = {(row[3], row[2], row[4]) for row in connection.execute(
            "PRAGMA foreign_key_list(definition)",
        )}
        required = {
            ("parse_id", "parse_result", "id"), ("version_id", "version", "id"),
            ("parent_id", "definition", "id"),
        }
        if not foreign_keys.issuperset(required):
            raise IndexStoreError("schema_mismatch", "Index definition foreign keys are missing")

    def __enter__(self) -> IndexStore:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        with _storage_errors(self.path):
            try:
                self.connection.close()
            finally:
                self._writer_owned = False
                self._scope_active = False

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[IndexStore]:
        if write and self.read_only:
            raise IndexStoreError("read_only", "Index was opened read-only")
        with _storage_errors(self.path):
            if self._scope_active or self.connection.in_transaction:
                raise IndexStoreError("transaction_active", "Nested or externally owned transactions are not supported")
            self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._scope_active = True
            self._writer_owned = write
            try:
                if not write:
                    self.connection.execute("SELECT key FROM metadata LIMIT 1").fetchone()
                yield self
                self.connection.commit()
            except BaseException as error:
                try:
                    self.connection.rollback()
                except sqlite3.Error as rollback_error:
                    error.add_note(f"Rollback also failed: {rollback_error}")
                raise
            finally:
                self._writer_owned = False
                self._scope_active = False

    def transaction(self) -> Any:
        """Own a BEGIN IMMEDIATE write transaction, rolling back even on cancellation."""
        return self._transaction(write=True)

    def snapshot(self) -> Any:
        """Own a stable read snapshot without acquiring a writer lock."""
        return self._transaction(write=False)

    def _require_writer(self) -> None:
        if self.read_only:
            raise IndexStoreError("read_only", "Index was opened read-only")
        if not self._writer_owned or not self.connection.in_transaction:
            raise IndexStoreError("transaction_required", "Writes require an IndexStore transaction")

    @contextmanager
    def _bundle(self) -> Iterator[None]:
        with _storage_errors(self.path):
            self.connection.execute("SAVEPOINT evidence_bundle")
            try:
                yield
                self.connection.execute("RELEASE SAVEPOINT evidence_bundle")
            except BaseException as error:
                try:
                    self.connection.execute("ROLLBACK TO SAVEPOINT evidence_bundle")
                    self.connection.execute("RELEASE SAVEPOINT evidence_bundle")
                except sqlite3.Error as rollback_error:
                    error.add_note(f"Bundle rollback also failed: {rollback_error}")
                raise

    def put_version(self, version: EntityVersion) -> str:
        self._require_writer()
        if type(version) is not EntityVersion:
            raise IndexContractError("invalid_record", "expected EntityVersion")
        version.__post_init__()
        identifier = version.id
        _identifier(identifier, ("ver1",))
        fields = version.fields()
        payload = canonical_encode("ver1", fields)
        with _storage_errors(self.path):
            row = self.connection.execute(
                "SELECT canonical_payload FROM version WHERE id = ?", (identifier,),
            ).fetchone()
            if row is not None:
                require_same_payload(row[0], payload)
                self.get_version(identifier)
            else:
                self.connection.execute(
                    "INSERT INTO version VALUES (?, ?, ?, ?, ?)",
                    (identifier, payload, _dump(fields), version.parse_fingerprint, version.kind),
                )
        return identifier

    def get_version(self, identifier: str) -> EntityVersion | None:
        _identifier(identifier, ("ver1",))
        with _record_errors():
            row = self.connection.execute("SELECT * FROM version WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                return None
            cached = self._decoded.get("version", row)
            if cached is not None:
                return cached
            version = EntityVersion(*_load(row["fields_json"]))
            _valid(version.id == identifier, "version identifier disagrees with payload")
            _valid(canonical_encode("ver1", version.fields()) == row["canonical_payload"],
                   "version canonical payload disagrees with JSON")
            _valid(version.parse_fingerprint == row["parse_fp"] and version.kind == row["kind"],
                   "version columns disagree with payload")
            self._decoded.put("version", row, version)
            return version

    def put_parse(self, result: ParseResult, versions: tuple[EntityVersion, ...]) -> str:
        self._require_writer()
        if type(result) is not ParseResult or type(versions) is not tuple:
            raise IndexContractError("invalid_record", "expected ParseResult and immutable versions tuple")
        result.__post_init__()
        _sqlite_bounds(result.byte_length)
        ordered = _parent_first(result)
        for definition in result.definitions:
            _sqlite_bounds(*definition.span.fields())
        payload = canonical_encode("par1", result.fields())
        with self._bundle():
            supplied: dict[str, EntityVersion] = {}
            for version in versions:
                if type(version) is not EntityVersion:
                    raise IndexContractError("invalid_record", "expected EntityVersion")
                version.__post_init__()
                identifier = version.id
                previous = supplied.get(identifier)
                if previous is not None:
                    require_same_payload(canonical_encode("ver1", previous.fields()),
                                         canonical_encode("ver1", version.fields()))
                supplied[identifier] = version
            for definition in result.definitions:
                version = supplied.get(definition.version_id) or self.get_version(definition.version_id)
                if version is None:
                    raise IndexContractError("invalid_record", "definition version is missing")
                if (version.kind != definition.kind
                        or version.parse_fingerprint != result.parse_fingerprint
                        or version.language != result.language):
                    raise IndexContractError(
                        "invalid_record", "definition version kind, fingerprint or language disagrees",
                    )
            row = self.connection.execute(
                "SELECT canonical_payload FROM parse_result WHERE id = ?", (result.id,),
            ).fetchone()
            if row is not None:
                require_same_payload(row[0], payload)
                for identifier, version in supplied.items():
                    previous = self.get_version(identifier)
                    if previous is not None:
                        require_same_payload(canonical_encode("ver1", previous.fields()),
                                             canonical_encode("ver1", version.fields()))
                self.get_parse(result.id)
                return result.id
            for version in supplied.values():
                self.put_version(version)
            self.connection.execute(
                "INSERT INTO parse_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.id, payload, _dump(result.fields()), result.parse_fingerprint,
                 result.blob.object_format, result.blob.digest, result.path,
                 result.status.value, result.byte_length),
            )
            for ordinal, definition in ordered:
                self.connection.execute(
                    "INSERT INTO definition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (definition.id, definition.parse_id, definition.version_id, definition.parent_id,
                     *definition.span.fields(), definition.kind, _dump(definition.names),
                     _dump(definition.discriminator), int(definition.synthetic_root), ordinal,
                     definition.names[-1]),
                )
        return result.id

    def get_parse(self, identifier: str) -> ParseResult | None:
        _identifier(identifier, ("par1",))
        with _record_errors():
            row = self.connection.execute("SELECT * FROM parse_result WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                return None
            fields = _load(row["fields_json"])
            _valid(len(fields) == 8, "invalid parse field count")
            definitions = []
            for ordinal, definition_row in enumerate(self.connection.execute(
                "SELECT * FROM definition WHERE parse_id = ? ORDER BY ordinal", (identifier,),
            )):
                _valid(definition_row["ordinal"] == ordinal, "definition ordinals are not contiguous")
                _valid(definition_row["synthetic_root"] in (0, 1), "invalid synthetic root flag")
                definition = EntityDefinition(
                    definition_row["parse_id"], definition_row["version_id"],
                    SourceSpan(*(definition_row[name] for name in (
                        "start_byte", "end_byte", "start_line", "end_line",
                    ))),
                    definition_row["kind"], _load(definition_row["names_json"]),
                    definition_row["parent_id"], _load(definition_row["discriminator_json"]),
                    bool(definition_row["synthetic_root"]),
                )
                _valid(
                    definition.id == definition_row["id"],
                    "definition identifier disagrees with columns",
                )
                _valid(
                    definition.names[-1] == definition_row["terminal_name"],
                    "definition terminal name disagrees with literal names",
                )
                version = self.get_version(definition.version_id)
                _valid(version is not None, "definition references a missing version")
                if version is not None:
                    _valid(version.kind == definition.kind
                           and version.parse_fingerprint == row["parse_fp"]
                           and version.language == fields[3],
                           "definition version kind, fingerprint or language disagrees")
                definitions.append(definition)
            result = ParseResult(
                fields[0], GitOid(*fields[1]), fields[2], fields[3], ParseStatus(fields[4]),
                fields[5], tuple(definitions), fields[7],
            )
            _valid(result.id == identifier, "parse identifier disagrees with payload")
            _valid(result.fields() == fields, "parse JSON disagrees with relational definitions")
            _valid(canonical_encode("par1", result.fields()) == row["canonical_payload"],
                   "parse canonical payload disagrees with reconstructed record")
            _valid((result.parse_fingerprint, result.blob.object_format, result.blob.digest,
                    result.path, result.status.value, result.byte_length) ==
                   (row["parse_fp"], row["blob_format"], row["blob_digest"],
                    row["path"], row["status"], row["byte_length"]), "parse columns disagree with payload")
            return result

    @contextmanager
    def _write_bundle(self) -> Iterator[None]:
        self._require_writer()
        with self._bundle():
            try:
                yield
            except sqlite3.IntegrityError as error:
                raise IndexContractError("invalid_record", str(error)) from error

    def _saved(self, table: str, identifier: str, payload: bytes) -> bool:
        row = self.connection.execute(
            f"SELECT canonical_payload FROM {table} WHERE id = ?", (identifier,),
        ).fetchone()
        if row is None:
            return False
        require_same_payload(row[0], payload)
        return True

    def _required(self, table: str, identifier: str) -> sqlite3.Row:
        _text(identifier)
        row = self.connection.execute(f"SELECT * FROM {table} WHERE id = ?", (identifier,)).fetchone()
        _require(row is not None, f"missing {table}: {identifier}")
        assert row is not None
        return row

    def _commit(self, oid: GitOid) -> sqlite3.Row:
        _oids(oid)
        oid.__post_init__()
        row = self.connection.execute(
            "SELECT * FROM commit_object WHERE object_format = ? AND digest = ?", oid.fields(),
        ).fetchone()
        _require(row is not None, "commit must be stored before its references")
        assert row is not None
        return row

    def _parents(self, oid: GitOid) -> tuple[GitOid, ...]:
        row = self._commit(oid)
        with _record_errors():
            parents = tuple(GitOid(*fields) for fields in _load(row["parents_json"]))
            _oids(oid, *parents)
            return parents

    def _context(self, identifier: str) -> IdentityContext:
        row = self._required("context", identifier)
        with _record_errors():
            cached = self._decoded.get("context", row)
            if cached is not None:
                return cached
            fields = _load(row["fields_json"])
            context = IdentityContext(GitOid(*fields[0]), fields[1],
                                      tuple(GitOid(*parent) for parent in fields[2]), *fields[3:])
            _valid(context.id == identifier, "context identifier disagrees with payload")
            _valid(canonical_encode("ctx1", context.fields()) == row["canonical_payload"],
                   "context canonical payload disagrees with JSON")
            self._decoded.put("context", row, context)
            return context

    @staticmethod
    def _page(offset: int, limit: int) -> None:
        _sqlite_bounds(offset, limit)
        _require(1 <= limit <= 1000, "page limit must be between 1 and 1000")

    @staticmethod
    def _row_oid(row: sqlite3.Row | dict[str, Any], prefix: str) -> GitOid | None:
        return (None if row[prefix + "_format"] is None
                else GitOid(row[prefix + "_format"], row[prefix + "_digest"]))

    def put_interpretation(
        self, category: str, fingerprint: bytes, manifest: tuple[tuple[str, CanonicalValue], ...],
    ) -> str:
        with self._write_bundle():
            _text(category)
            _require(category in ("parse", "match"), "unknown interpretation category")
            _digest(fingerprint)
            _named_pairs(manifest)
            identifier = category + "_" + fingerprint.hex()
            fields = (category, fingerprint, manifest)
            payload = canonical_encode("evd1", fields)
            if not self._saved("interpretation", identifier, payload):
                self.connection.execute("INSERT INTO interpretation VALUES (?, ?, ?, ?, ?)",
                                        (identifier, category, fingerprint, payload, _dump(fields)))
            return identifier

    def put_commit(
        self, commit: GitOid, tree: GitOid, parents: tuple[GitOid, ...], message: bytes = b"",
    ) -> None:
        """Store caller-verified committed object metadata, not proof of Git object bytes."""
        with self._write_bundle():
            _tuple(parents)
            _oids(commit, tree, *parents)
            for oid in (commit, tree, *parents):
                oid.__post_init__()
            _require(type(message) is bytes, "commit message must be bytes")
            _require(commit not in parents, "commit cannot parent itself")
            parent_fields = tuple(parent.fields() for parent in parents)
            payload = canonical_encode("evd1", (commit.fields(), tree.fields(), parent_fields, message))
            row = self.connection.execute(
                "SELECT canonical_payload FROM commit_object WHERE object_format = ? AND digest = ?",
                commit.fields(),
            ).fetchone()
            if row is not None:
                require_same_payload(row[0], payload)
                return
            self.connection.execute("INSERT INTO commit_object VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (*commit.fields(), *tree.fields(), _dump(parent_fields), message, payload))

    def put_context(self, context: IdentityContext) -> str:
        with self._write_bundle():
            _require(type(context) is IdentityContext, "expected IdentityContext")
            context.__post_init__()
            payload = canonical_encode("ctx1", context.fields())
            if self._saved("context", context.id, payload):
                return context.id
            _require(self._parents(context.anchor) == context.parents, "context anchor parents disagree")
            _require(context.boundary != "root" or not context.parents, "root context has parents")
            _require(context.boundary not in ("shallow", "missing_parent") or bool(context.parents),
                     "missing-parent context requires actual parent provenance")
            parse_id = "parse_" + context.parse_fingerprint.hex()
            match_id = "match_" + context.match_fingerprint.hex()
            self._required("interpretation", parse_id)
            self._required("interpretation", match_id)
            self.connection.execute("INSERT INTO context VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (context.id, payload, _dump(context.fields()), *context.anchor.fields(),
                                     context.boundary, parse_id, match_id))
            return context.id

    def generation(self, identifier: str) -> dict[str, Any]:
        _text(identifier)
        with _storage_errors(self.path):
            row = self.connection.execute(
                "SELECT generation.*, scope.name AS scope FROM generation JOIN scope ON scope.id = scope_id "
                "WHERE generation.id = ?", (identifier,),
            ).fetchone()
            if row is None:
                raise IndexStoreError("generation_gone", f"Generation is unavailable: {identifier}")
            result = dict(row)
            result["tip"] = self._row_oid(row, "tip")
            return result

    def published_generation(self, scope: str) -> str | None:
        _text(scope)
        with _storage_errors(self.path):
            row = self.connection.execute(
                "SELECT published_generation_id FROM scope WHERE name = ?", (scope,),
            ).fetchone()
            return None if row is None else row[0]

    def _staging(self, identifier: str) -> dict[str, Any]:
        generation = self.generation(identifier)
        _require(generation["state"] == "staging", "published generation is immutable")
        return generation

    def _copy_rows(self, table: str, columns: str, predecessor: str, generation: str) -> None:
        last_rowid = 0
        while True:
            boundary = self.connection.execute(
                f"SELECT max(copy_rowid) FROM (SELECT rowid AS copy_rowid FROM {table} WHERE generation_id = ? "
                "AND rowid > ? ORDER BY rowid LIMIT 1000)", (predecessor, last_rowid),
            ).fetchone()[0]
            if boundary is None:
                return
            self.check_storage_limit()
            self.connection.execute(
                f"INSERT INTO {table} (generation_id, {columns}) SELECT ?, {columns} FROM {table} "
                "WHERE generation_id = ? AND rowid > ? AND rowid <= ? ORDER BY rowid LIMIT 1000",
                (generation, predecessor, last_rowid, boundary),
            )
            last_rowid = boundary

    def begin_generation(
        self, scope: str, context: IdentityContext, tip: GitOid, *, predecessor: str | None = None,
    ) -> str:
        with self._write_bundle():
            _text(scope)
            _require(type(context) is IdentityContext, "expected IdentityContext")
            context.__post_init__()
            stored_context = self._context(context.id)
            require_same_payload(canonical_encode("ctx1", stored_context.fields()),
                                 canonical_encode("ctx1", context.fields()))
            _oids(tip, context.anchor)
            self._commit(tip)
            if predecessor is not None:
                previous = self.generation(predecessor)
                _require(previous["scope"] == scope and previous["context_id"] == context.id
                         and previous["state"] == "published"
                         and self.published_generation(scope) == predecessor,
                         "predecessor must be the current publication of the same scope and context")
            row = self.connection.execute("SELECT id FROM scope WHERE name = ?", (scope,)).fetchone()
            scope_id = row[0] if row is not None else "scp1_" + uuid4().hex
            if row is None:
                self.connection.execute("INSERT INTO scope VALUES (?, ?, NULL)", (scope_id, scope))
            identifier = "gen1_" + uuid4().hex
            self.connection.execute("INSERT INTO generation VALUES (?, ?, ?, ?, ?, ?, 'staging')",
                                    (identifier, scope_id, context.id, *tip.fields(), predecessor))
            if predecessor is not None:
                for table, columns in (
                    ("membership", "sequence, commit_format, commit_digest, parent_format, parent_digest"),
                    ("file_state", "path, from_seq, to_seq, status, blob_format, blob_digest, object_type, mode, parse_id, binding_set_id"),
                    ("coverage", "sequence, path, category, reason, omitted, count_known"),
                    ("generation_event", "sequence, event_id"), ("generation_link", "link_id"),
                    ("departure", "sequence, eid, occurrence_id, status, reason"),
                ):
                    self._copy_rows(table, columns, predecessor, identifier)
                last_path = b""
                while True:
                    boundary = self.connection.execute(
                        "SELECT path FROM frontier WHERE generation_id = ? AND path > ? "
                        "ORDER BY path LIMIT 1 OFFSET 999", (predecessor, last_path),
                    ).fetchone()
                    self.connection.execute(
                        "INSERT INTO frontier (generation_id, path, file_state_id) "
                        "SELECT ?, old.path, copied.id FROM frontier old "
                        "JOIN file_state original ON original.id = old.file_state_id "
                        "JOIN file_state copied ON copied.generation_id = ? AND copied.path = old.path "
                        "AND copied.from_seq = original.from_seq "
                        "WHERE old.generation_id = ? AND old.path > ? ORDER BY old.path LIMIT 1000",
                        (identifier, identifier, predecessor, last_path),
                    )
                    self.check_storage_limit()
                    if boundary is None:
                        break
                    last_path = boundary[0]
            return identifier

    def _member(self, generation: str, sequence: int) -> sqlite3.Row:
        _sqlite_bounds(sequence)
        row = self.connection.execute(
            "SELECT * FROM membership WHERE generation_id = ? AND sequence = ?", (generation, sequence),
        ).fetchone()
        _require(row is not None, "sequence is not a generation member")
        assert row is not None
        return row

    def add_member(self, generation: str, sequence: int, commit: GitOid) -> None:
        with self._write_bundle():
            record = self._staging(generation)
            _sqlite_bounds(sequence)
            parents = self._parents(commit)
            last = self.connection.execute(
                "SELECT sequence, commit_format, commit_digest FROM membership WHERE generation_id = ? "
                "ORDER BY sequence DESC LIMIT 1", (generation,),
            ).fetchone()
            if last is None:
                context = self._context(record["context_id"])
                _require(sequence == 0 and commit == context.anchor and parents == context.parents,
                         "first member must be the context anchor with actual parents")
            else:
                _require(sequence == last["sequence"] + 1 and bool(parents)
                         and parents[0] == self._row_oid(last, "commit"),
                         "members must be consecutive actual first-parent neighbors")
            parent = (None, None) if not parents else parents[0].fields()
            self.connection.execute("INSERT INTO membership VALUES (?, ?, ?, ?, ?, ?)",
                                    (generation, sequence, *commit.fields(), *parent))

    def members(self, generation: str, *, offset: int = 0, limit: int = 1000) -> tuple[GitOid, ...]:
        self._page(offset, limit)
        with _storage_errors(self.path):
            self.generation(generation)
            return tuple(GitOid(*row) for row in self.connection.execute(
                "SELECT commit_format, commit_digest FROM membership WHERE generation_id = ? "
                "ORDER BY sequence LIMIT ? OFFSET ?", (generation, limit, offset),
            ))

    def _parse_metadata(self, identifier: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT id, path, blob_format, blob_digest, parse_fp, status, byte_length "
            f"FROM parse_result{self._parse_metadata_hint} WHERE id = ?", (identifier,),
        ).fetchone()
        _require(row is not None, "parse_result evidence is missing")
        assert row is not None
        return row

    def _definition(self, identifier: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT definition.*, parse_result.path, parse_result.blob_format, parse_result.blob_digest, "
            "parse_result.parse_fp, parse_result.status FROM definition "
            f"JOIN parse_result{self._parse_metadata_hint} "
            "ON parse_result.id = definition.parse_id WHERE definition.id = ?", (identifier,),
        ).fetchone()
        _require(row is not None, "definition must belong to a stored parse")
        assert row is not None
        return row

    def put_occurrence(self, occurrence: EntityOccurrence) -> str:
        with self._write_bundle():
            _require(type(occurrence) is EntityOccurrence, "expected EntityOccurrence")
            occurrence.__post_init__()
            payload = canonical_encode("occ1", occurrence.fields())
            if self._saved("occurrence", occurrence.id, payload):
                return occurrence.id
            self._commit(occurrence.commit)
            definition = self._definition(occurrence.definition_id)
            _require(definition["status"] == "complete" and definition["path"] == occurrence.path
                     and self._row_oid(definition, "blob") == occurrence.blob
                     and occurrence.object_type == "blob" and occurrence.mode in (0o100644, 0o100755),
                     "occurrence must identify its exact regular parsed blob entry")
            self.connection.execute("INSERT INTO occurrence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (occurrence.id, payload, _dump(occurrence.fields()), *occurrence.commit.fields(),
                                     occurrence.path, *occurrence.blob.fields(), occurrence.object_type,
                                     occurrence.mode, occurrence.definition_id))
            return occurrence.id

    def put_lineage(self, seed: LineageSeed) -> str:
        with self._write_bundle():
            _require(type(seed) is LineageSeed, "expected LineageSeed")
            seed.__post_init__()
            payload = canonical_encode(seed.id.split("_")[0], seed.fields())
            if self._saved("lineage", seed.id, payload):
                return seed.id
            context = self._context(seed.context_id)
            occurrence = self._required("occurrence", seed.occurrence_id)
            definition = self._definition(occurrence["definition_id"])
            _require(self._row_oid(occurrence, "commit") == seed.commit
                     and definition["parse_fp"] == context.parse_fingerprint,
                     "seed commit or parse interpretation disagrees with occurrence")
            if seed.reason == "root":
                _require(context.boundary == "root" and seed.commit == context.anchor,
                         "root seed requires a root context anchor")
            if seed.id.startswith("eid1_"):
                _require(context.boundary == "root", "genesis seeds require a root context")
            if seed.reason in ("bounded", "shallow", "missing_parent"):
                _require(seed.reason == context.boundary and seed.commit == context.anchor,
                         "boundary seed must agree with context")
            self.connection.execute("INSERT INTO lineage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (seed.id, payload, _dump(seed.fields()), seed.context_id,
                                     *seed.commit.fields(), seed.occurrence_id, seed.reason))
            return seed.id

    def put_binding_set(self, parse_id: str, members: tuple[tuple[str, str], ...]) -> str:
        with self._write_bundle():
            _identifier(parse_id, ("par1",))
            _tuple(members)
            for pair in members:
                _tuple(pair)
                _require(len(pair) == 2, "binding members must be definition/EID pairs")
                _identifier(pair[0], ("def1",))
                _identifier(pair[1], ("eid1", "oeid1"))
            pairs = tuple(sorted(members))
            fields = (parse_id, pairs)
            identifier = content_id("bnd1", fields)
            payload = canonical_encode("bnd1", fields)
            if self._saved("binding_set", identifier, payload):
                return identifier
            result = self._parse_metadata(parse_id)
            _require(result["status"] == "complete", "bindings require a complete parse")
            _require(len({pair[0] for pair in pairs}) == len(pairs)
                     and len({pair[1] for pair in pairs}) == len(pairs), "bindings must be one-to-one")
            count = self.connection.execute("SELECT count(*) FROM definition WHERE parse_id = ?", (parse_id,)).fetchone()[0]
            _require(count == len(pairs), "binding set must cover every definition")
            contexts = set()
            for definition_id, eid in pairs:
                _require(self._definition(definition_id)["parse_id"] == parse_id,
                         "binding definition belongs to another parse")
                seed = self._required("lineage", eid)
                context = self._context(seed["context_id"])
                _require(context.parse_fingerprint == result["parse_fp"], "binding parse interpretation disagrees")
                contexts.add(context.id)
            _require(len(contexts) <= 1, "binding set mixes identity contexts")
            self.connection.execute("INSERT INTO binding_set VALUES (?, ?, ?)", (identifier, payload, parse_id))
            self.connection.executemany("INSERT INTO binding VALUES (?, ?, ?)",
                                        ((identifier, definition_id, eid) for definition_id, eid in pairs))
            return identifier

    def _validate_file_payload(
        self, context_id: str, path: bytes, status: str, blob: GitOid | None,
        object_type: str | None, mode: int | None, parse_id: str | None, binding_set: str | None,
    ) -> None:
        _path(path)
        _text(status)
        _require(status in ("present", "unknown", "removed"), "unknown file status")
        context = self._context(context_id)
        if blob is None:
            _require(object_type is None and mode is None and parse_id is None,
                     "missing blob cannot carry entry or parse metadata")
        else:
            _oids(blob, context.anchor)
            blob.__post_init__()
            _sqlite_bounds(mode)
            _require((object_type, mode) in (("blob", 0o100644), ("blob", 0o100755),
                     ("blob", 0o120000), ("commit", 0o160000), ("tree", 0o040000)), "invalid tree entry")
        if status == "removed":
            _require(blob is None and parse_id is None and binding_set is None, "removed entry must be empty")
        if status == "unknown":
            _require(binding_set is None, "unknown population cannot carry bindings")
        if parse_id is not None:
            result = self._parse_metadata(parse_id)
            _require(result["path"] == path and self._row_oid(result, "blob") == blob
                     and result["parse_fp"] == context.parse_fingerprint
                     and object_type == "blob" and mode in (0o100644, 0o100755),
                     "file parse path, blob, interpretation or entry type disagrees")
        if status == "present":
            _require(blob is not None and parse_id is not None and binding_set is not None,
                     "present file requires blob, complete parse and bindings")
            result = self._parse_metadata(parse_id)
            bindings = self._required("binding_set", binding_set)
            _require(result["status"] == "complete" and bindings["parse_id"] == parse_id,
                     "present binding set must belong to its complete parse")
            mismatch = self.connection.execute(
                "SELECT 1 FROM binding JOIN lineage ON lineage.id = binding.eid "
                "JOIN definition ON definition.id = binding.definition_id "
                "WHERE binding_set_id = ? AND (lineage.context_id != ? OR definition.parse_id != ?) LIMIT 1",
                (binding_set, context_id, parse_id),
            ).fetchone()
            count = self.connection.execute(
                "SELECT (SELECT count(*) FROM binding WHERE binding_set_id = ?) = "
                "(SELECT count(*) FROM definition WHERE parse_id = ?)", (binding_set, parse_id),
            ).fetchone()[0]
            _require(mismatch is None and bool(count), "file bindings have wrong context or incomplete definitions")

    def put_file_state(
        self, generation: str, path: bytes, from_seq: int, to_seq: int | None, status: str,
        blob: GitOid | None, object_type: str | None, mode: int | None,
        parse_id: str | None, binding_set: str | None,
    ) -> int:
        with self._write_bundle():
            record = self._staging(generation)
            self._member(generation, from_seq)
            if to_seq is not None:
                self._member(generation, to_seq)
                _require(to_seq > from_seq, "file intervals must be nonempty and forward")
            self._validate_file_payload(record["context_id"], path, status, blob,
                                        object_type, mode, parse_id, binding_set)
            cursor = self.connection.execute(
                "INSERT INTO file_state (generation_id, path, from_seq, to_seq, status, blob_format, blob_digest, "
                "object_type, mode, parse_id, binding_set_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (generation, path, from_seq, to_seq, status, *((None, None) if blob is None else blob.fields()),
                 object_type, mode, parse_id, binding_set),
            )
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def close_file_state(self, generation: str, state: int, to_seq: int) -> None:
        with self._write_bundle():
            self._staging(generation)
            _sqlite_bounds(state, to_seq)
            self._member(generation, to_seq)
            row = self.connection.execute("SELECT * FROM file_state WHERE id = ?", (state,)).fetchone()
            _require(row is not None and row["generation_id"] == generation and row["to_seq"] is None
                     and row["from_seq"] < to_seq, "only an owned open interval can be closed")
            self.connection.execute("DELETE FROM frontier WHERE generation_id = ? AND file_state_id = ?", (generation, state))
            self.connection.execute("UPDATE file_state SET to_seq = ? WHERE id = ?", (to_seq, state))

    def set_frontier(self, generation: str, path: bytes, state: int) -> None:
        with self._write_bundle():
            self._staging(generation)
            _path(path)
            _sqlite_bounds(state)
            self.connection.execute(
                "INSERT INTO frontier VALUES (?, ?, ?) ON CONFLICT(generation_id, path) "
                "DO UPDATE SET file_state_id = excluded.file_state_id", (generation, path, state),
            )

    def file_states(
        self, generation: str, sequence: int, *, offset: int = 0, limit: int = 1000,
    ) -> tuple[dict[str, Any], ...]:
        self._page(offset, limit)
        with _storage_errors(self.path):
            self.generation(generation)
            self._member(generation, sequence)
            states = []
            for row in self.connection.execute(
                "SELECT * FROM file_state WHERE generation_id = ? AND from_seq <= ? "
                "AND (to_seq IS NULL OR to_seq > ?) ORDER BY path, from_seq LIMIT ? OFFSET ?",
                (generation, sequence, sequence, limit, offset),
            ):
                state = dict(row)
                state["blob"] = self._row_oid(row, "blob")
                state["binding_set"] = row["binding_set_id"]
                states.append(state)
            return tuple(states)

    def _edge(self, parent: GitOid, commit: GitOid) -> None:
        _oids(parent, commit)
        parents = self._parents(commit)
        self._commit(parent)
        _require(bool(parents) and parents[0] == parent, "edge is not the actual first-parent edge")

    def _endpoint(self, identifier: str, commit: GitOid, context: IdentityContext) -> sqlite3.Row:
        occurrence = self._required("occurrence", identifier)
        definition = self._definition(occurrence["definition_id"])
        _require(self._row_oid(occurrence, "commit") == commit
                 and definition["parse_fp"] == context.parse_fingerprint,
                 "endpoint commit or interpretation disagrees")
        return definition

    def _validate_link(self, link: LineageLink) -> None:
        context = self._context(link.context_id)
        self._edge(link.parent, link.commit)
        source = self._endpoint(link.source, link.parent, context)
        destination = self._endpoint(link.destination, link.commit, context)
        if link.status is LinkStatus.ACCEPTED:
            _require(source["kind"] == destination["kind"]
                     and source["synthetic_root"] == destination["synthetic_root"],
                     "accepted link cannot change definition kind or root role")
            source_version = self.get_version(source["version_id"])
            destination_version = self.get_version(destination["version_id"])
            _require(source_version is not None and destination_version is not None
                     and source_version.language == destination_version.language,
                     "accepted link cannot change language")

    def put_link(self, link: LineageLink) -> str:
        with self._write_bundle():
            _require(type(link) is LineageLink, "expected LineageLink")
            link.__post_init__()
            payload = canonical_encode("lnk1", link.fields())
            if self._saved("lineage_link", link.id, payload):
                return link.id
            self._validate_link(link)
            self.connection.execute(
                "INSERT INTO lineage_link VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (link.id, payload, _dump(link.fields()), link.context_id, *link.parent.fields(),
                 *link.commit.fields(), link.source, link.destination, link.status.value, link.method, link.reason),
            )
            return link.id

    def _link(self, identifier: str) -> LineageLink:
        row = self._required("lineage_link", identifier)
        with _record_errors():
            fields = _load(row["fields_json"])
            link = LineageLink(fields[0], GitOid(*fields[1]), GitOid(*fields[2]),
                               fields[3], fields[4], LinkStatus(fields[5]), *fields[6:])
            _valid(link.id == identifier and canonical_encode("lnk1", link.fields()) == row["canonical_payload"],
                   "link canonical payload disagrees with identifier or JSON")
            _valid((link.context_id, link.parent, link.commit, link.source, link.destination,
                    link.status.value, link.method, link.reason) ==
                   (row["context_id"], self._row_oid(row, "parent"), self._row_oid(row, "commit"),
                    row["source"], row["destination"], row["status"], row["method"], row["reason"]),
                   "link columns disagree with canonical payload")
            return link

    def _bound_occurrence(self, generation: str, sequence: int, occurrence_id: str, eid: str | None = None) -> str:
        member = self._member(generation, sequence)
        occurrence = self._required("occurrence", occurrence_id)
        _require(self._row_oid(member, "commit") == self._row_oid(occurrence, "commit"),
                 "occurrence is not at the selected member")
        row = self.connection.execute(
            "SELECT binding.eid FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
            "WHERE generation_id = ? AND path = ? AND from_seq <= ? AND (to_seq IS NULL OR to_seq > ?) "
            "AND status = 'present' AND blob_format = ? AND blob_digest = ? AND object_type = ? AND mode = ? "
            "AND binding.definition_id = ?", (generation, occurrence["path"], sequence, sequence,
             occurrence["blob_format"], occurrence["blob_digest"], occurrence["object_type"],
             occurrence["mode"], occurrence["definition_id"]),
        ).fetchone()
        _require(row is not None and (eid is None or row[0] == eid),
                 "occurrence is not bound at the selected generation state")
        assert row is not None
        return row[0]

    def _link_sequence(self, generation: str, link: LineageLink) -> int:
        record = self.generation(generation)
        _require(record["context_id"] == link.context_id, "link belongs to another context")
        self._validate_link(link)
        member = self.connection.execute(
            "SELECT sequence FROM membership WHERE generation_id = ? AND commit_format = ? AND commit_digest = ?",
            (generation, *link.commit.fields()),
        ).fetchone()
        _require(member is not None and member[0] > 0, "link edge is outside generation membership")
        assert member is not None
        sequence = member[0]
        previous = self._member(generation, sequence - 1)
        _require(self._row_oid(previous, "commit") == link.parent, "link parent is not the previous member")
        source_eid = self._bound_occurrence(generation, sequence - 1, link.source)
        destination_eid = self._bound_occurrence(generation, sequence, link.destination)
        _require(link.status is not LinkStatus.ACCEPTED or source_eid == destination_eid,
                 "accepted link endpoints must carry the same EID")
        return sequence

    def attach_link(self, generation: str, link_id: str) -> None:
        with self._write_bundle():
            self._staging(generation)
            link = self._link(link_id)
            self._link_sequence(generation, link)
            self.connection.execute("INSERT OR IGNORE INTO generation_link VALUES (?, ?)", (generation, link_id))

    def _validate_event(self, event: ChangeEvent) -> None:
        context = self._context(event.context_id)
        seed = self._required("lineage", event.eid)
        _require(seed["context_id"] == event.context_id, "event and lineage contexts disagree")
        parents = self._parents(event.commit)
        if event.parent is None:
            _require(event.source is None and event.commit == context.anchor
                     and (not parents or context.boundary != "root"),
                     "parentless event must observe the context anchor without a source")
        else:
            self._edge(event.parent, event.commit)
        matched = False
        for identifier, commit in ((event.source, event.parent), (event.destination, event.commit)):
            if identifier is None:
                continue
            _require(commit is not None, "source requires an actual parent")
            assert commit is not None
            definition = self._endpoint(identifier, commit, context)
            matched = matched or identifier == seed["occurrence_id"] or self.connection.execute(
                "SELECT 1 FROM binding WHERE definition_id = ? AND eid = ? LIMIT 1", (definition["id"], event.eid),
            ).fetchone() is not None
        _require(matched, "event endpoints do not belong to its lineage seed or bindings")
        if event.decision_id is not None:
            decision = self._link(event.decision_id)
            _require(decision.status is LinkStatus.ACCEPTED
                     and (decision.context_id, decision.parent, decision.commit, decision.source, decision.destination)
                     == (event.context_id, event.parent, event.commit, event.source, event.destination),
                     "event decision must be the matching accepted edge and endpoints")

    def put_event(self, event: ChangeEvent) -> str:
        with self._write_bundle():
            _require(type(event) is ChangeEvent, "expected ChangeEvent")
            event.__post_init__()
            payload = canonical_encode("evt1", event.fields())
            if self._saved("event", event.id, payload):
                return event.id
            self._validate_event(event)
            self.connection.execute(
                "INSERT INTO event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.id, payload, _dump(event.fields()), event.context_id, event.eid,
                 *((None, None) if event.parent is None else event.parent.fields()), *event.commit.fields(),
                 event.source, event.destination, _dump(tuple(sorted(event.flags))), int(event.material), event.decision_id),
            )
            return event.id

    def _event(self, identifier: str) -> ChangeEvent:
        row = self._required("event", identifier)
        with _record_errors():
            fields = _load(row["fields_json"])
            event = ChangeEvent(fields[0], fields[1], None if fields[2] is None else GitOid(*fields[2]),
                                GitOid(*fields[3]), *fields[4:])
            _valid(event.id == identifier and canonical_encode("evt1", event.fields()) == row["canonical_payload"],
                   "event canonical payload disagrees with identifier or JSON")
            _valid((event.context_id, event.eid, event.parent, event.commit, event.source, event.destination,
                    tuple(sorted(event.flags)), int(event.material), event.decision_id) ==
                   (row["context_id"], row["eid"], self._row_oid(row, "parent"), self._row_oid(row, "commit"),
                    row["source"], row["destination"], _load(row["flags_json"]), row["material"], row["decision_id"]),
                   "event columns disagree with canonical payload")
            return event

    def _event_sequence(self, generation: str, sequence: int, event: ChangeEvent) -> None:
        record = self.generation(generation)
        member = self._member(generation, sequence)
        _require(record["context_id"] == event.context_id and self._row_oid(member, "commit") == event.commit,
                 "event context or commit disagrees with generation member")
        self._validate_event(event)
        if sequence > 0:
            _require(event.parent == self._row_oid(self._member(generation, sequence - 1), "commit"),
                     "event parent is not the previous member")
        if event.source is not None:
            _require(sequence > 0, "event source is outside generation membership")
            self._bound_occurrence(generation, sequence - 1, event.source, event.eid)
        if event.destination is not None:
            self._bound_occurrence(generation, sequence, event.destination, event.eid)

    def attach_event(self, generation: str, sequence: int, event_id: str) -> None:
        with self._write_bundle():
            self._staging(generation)
            event = self._event(event_id)
            self._event_sequence(generation, sequence, event)
            self.connection.execute("INSERT OR IGNORE INTO generation_event VALUES (?, ?, ?)",
                                    (generation, sequence, event_id))

    def _validate_departure(
        self, generation: str, sequence: int, eid: str, occurrence_id: str, status: str, reason: str,
    ) -> None:
        _sqlite_bounds(sequence)
        _require(sequence > 0, "departure requires an in-scope parent")
        _text(status)
        _text(reason)
        _require(status in ("removed", "unknown"), "departure must be removed or unknown")
        record = self.generation(generation)
        seed = self._required("lineage", eid)
        _require(seed["context_id"] == record["context_id"], "departure belongs to another context")
        self._member(generation, sequence)
        self._bound_occurrence(generation, sequence - 1, occurrence_id, eid)
        present = self.connection.execute(
            "SELECT 1 FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
            "WHERE generation_id = ? AND eid = ? AND (to_seq IS NULL OR to_seq > ?) LIMIT 1",
            (generation, eid, sequence),
        ).fetchone()
        _require(present is None, "departure is terminal; EID is still present or returns later")

    def put_departure(
        self, generation: str, sequence: int, eid: str, occurrence_id: str, status: str, reason: str,
    ) -> None:
        with self._write_bundle():
            self._staging(generation)
            self._validate_departure(generation, sequence, eid, occurrence_id, status, reason)
            row = self.connection.execute(
                "SELECT sequence, occurrence_id, status, reason FROM departure WHERE generation_id = ? AND eid = ?",
                (generation, eid),
            ).fetchone()
            if row is not None:
                _require(tuple(row) == (sequence, occurrence_id, status, reason), "lineage already departed")
                return
            self.connection.execute("INSERT INTO departure VALUES (?, ?, ?, ?, ?, ?)",
                                    (generation, sequence, eid, occurrence_id, status, reason))

    def put_coverage(
        self, generation: str, sequence: int, path: bytes | None, category: str, reason: str, omitted: int | None,
    ) -> None:
        with self._write_bundle():
            self._staging(generation)
            self._member(generation, sequence)
            if path is not None:
                _path(path)
            _text(category)
            _text(reason)
            if omitted is not None:
                _sqlite_bounds(omitted)
            row = self.connection.execute(
                "SELECT omitted FROM coverage WHERE generation_id = ? AND sequence = ? AND path IS ? "
                "AND category = ? AND reason = ?", (generation, sequence, path, category, reason),
            ).fetchone()
            if row is not None:
                _require(row[0] == omitted, "coverage count disagrees with existing outcome")
                return
            self.connection.execute(
                "INSERT INTO coverage (generation_id, sequence, path, category, reason, omitted, count_known) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (generation, sequence, path, category, reason, omitted, int(omitted is not None)),
            )

    def _generation_rows(self, table: str, generation: str) -> Iterator[sqlite3.Row]:
        last_rowid = 0
        while True:
            rows = tuple(self.connection.execute(
                f"SELECT rowid AS page_rowid, * FROM {table} WHERE generation_id = ? AND rowid > ? "
                "ORDER BY rowid LIMIT 1000", (generation, last_rowid),
            ))
            if not rows:
                return
            yield from rows
            last_rowid = rows[-1]["page_rowid"]

    def publish_generation(self, generation: str, *, max_storage_bytes: int = 10 * 1024 ** 3) -> None:
        """Validate stored checkpoints, not Git inventory completeness or F4 source/policy proofs."""
        with self._write_bundle():
            record = self._staging(generation)
            self.check_storage_limit(max_storage_bytes)
            context = self._context(record["context_id"])
            _require(self._parents(context.anchor) == context.parents
                     and (context.boundary != "root" or not context.parents), "invalid anchor provenance")
            expected_sequence = 0
            previous_commit = None
            for member in self._generation_rows("membership", generation):
                commit = GitOid(member["commit_format"], member["commit_digest"])
                parents = self._parents(commit)
                _require(member["sequence"] == expected_sequence, "membership is not contiguous")
                _require(self._row_oid(member, "parent") == (parents[0] if parents else None),
                         "membership parent metadata disagrees with committed parents")
                if expected_sequence == 0:
                    _require(commit == context.anchor, "first member is not the context anchor")
                else:
                    _require(bool(parents) and parents[0] == previous_commit, "membership is not first-parent history")
                previous_commit = commit
                expected_sequence += 1
            _require(expected_sequence > 0 and previous_commit == record["tip"],
                     "generation membership must reach its pinned tip")
            invalid_interval = self.connection.execute(
                "SELECT 1 FROM (SELECT to_seq, lead(from_seq) OVER (PARTITION BY path ORDER BY from_seq) AS next_seq "
                "FROM file_state WHERE generation_id = ?) WHERE to_seq IS NOT next_seq LIMIT 1", (generation,),
            ).fetchone()
            _require(invalid_interval is None, "file intervals have gaps, overlap, or a closed final state")
            missing_frontier = self.connection.execute(
                "SELECT 1 FROM file_state LEFT JOIN frontier ON frontier.generation_id = file_state.generation_id "
                "AND frontier.path = file_state.path AND frontier.file_state_id = file_state.id "
                "WHERE file_state.generation_id = ? AND file_state.to_seq IS NULL "
                "AND frontier.file_state_id IS NULL LIMIT 1", (generation,),
            ).fetchone()
            invalid_frontier = self.connection.execute(
                "SELECT 1 FROM frontier LEFT JOIN file_state ON file_state.id = frontier.file_state_id "
                "WHERE frontier.generation_id = ? AND (file_state.id IS NULL OR file_state.generation_id != ? "
                "OR file_state.path != frontier.path OR file_state.to_seq IS NOT NULL OR file_state.from_seq >= ?) LIMIT 1",
                (generation, generation, expected_sequence),
            ).fetchone()
            _require(missing_frontier is None and invalid_frontier is None,
                     "frontier must equal every open interval at the final member")
            missing_coverage = self.connection.execute(
                "SELECT 1 FROM file_state WHERE generation_id = ? AND NOT EXISTS (SELECT 1 FROM coverage "
                "WHERE coverage.generation_id = file_state.generation_id AND coverage.sequence = file_state.from_seq "
                "AND coverage.path = file_state.path AND category = 'extraction') LIMIT 1", (generation,),
            ).fetchone()
            _require(missing_coverage is None, "every file interval needs an extraction coverage outcome")
            _require(not self._overlapping_eid(generation, expected_sequence),
                     "one EID occupies multiple active locations")
            invalid_seed = self.connection.execute(
                "WITH presence AS (SELECT binding.eid, min(file_state.from_seq) AS first_seq "
                "FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
                "WHERE file_state.generation_id = ? AND file_state.status = 'present' GROUP BY binding.eid) "
                "SELECT 1 FROM presence JOIN lineage seed ON seed.id = presence.eid "
                "LEFT JOIN membership member ON member.generation_id = ? "
                "AND member.commit_format = seed.commit_format AND member.commit_digest = seed.commit_digest "
                "WHERE member.sequence IS NULL OR presence.first_seq != member.sequence OR NOT EXISTS ("
                "SELECT 1 FROM occurrence JOIN file_state ON file_state.generation_id = member.generation_id "
                "AND file_state.path = occurrence.path AND file_state.blob_format = occurrence.blob_format "
                "AND file_state.blob_digest = occurrence.blob_digest AND file_state.object_type = occurrence.object_type "
                "AND file_state.mode = occurrence.mode "
                "JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
                "AND binding.definition_id = occurrence.definition_id AND binding.eid = seed.id "
                "WHERE occurrence.id = seed.occurrence_id AND occurrence.commit_format = member.commit_format "
                "AND occurrence.commit_digest = member.commit_digest AND file_state.status = 'present' "
                "AND file_state.from_seq <= member.sequence "
                "AND (file_state.to_seq IS NULL OR file_state.to_seq > member.sequence)) LIMIT 1",
                (generation, generation),
            ).fetchone()
            _require(invalid_seed is None, "lineage must first appear at its exact seed occurrence in membership")
            lineage_gap = self.connection.execute(
                "SELECT 1 FROM (SELECT file_state.from_seq, "
                "lag(file_state.to_seq, 1, file_state.from_seq) OVER (PARTITION BY binding.eid "
                "ORDER BY file_state.from_seq, file_state.id) AS previous_end "
                "FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
                "WHERE file_state.generation_id = ? AND file_state.status = 'present') "
                "WHERE previous_end IS NOT from_seq LIMIT 1", (generation,),
            ).fetchone()
            _require(lineage_gap is None, "lineage presence cannot resume after a gap")
            missing_departure = self.connection.execute(
                "WITH closed_presence AS (SELECT binding.eid, max(file_state.to_seq) AS end_seq "
                "FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
                "WHERE file_state.generation_id = ? AND file_state.status = 'present' "
                "GROUP BY binding.eid HAVING count(file_state.to_seq) = count(*)) "
                "SELECT 1 FROM closed_presence WHERE NOT EXISTS (SELECT 1 FROM departure "
                "WHERE departure.generation_id = ? AND departure.eid = closed_presence.eid "
                "AND departure.sequence = closed_presence.end_seq) LIMIT 1", (generation, generation),
            ).fetchone()
            _require(missing_departure is None, "closed lineage presence requires an exact terminal departure")
            for state in self._generation_rows("file_state", generation):
                self._member(generation, state["from_seq"])
                if state["to_seq"] is not None:
                    self._member(generation, state["to_seq"])
                self._validate_file_payload(record["context_id"], state["path"], state["status"],
                                            self._row_oid(state, "blob"), state["object_type"], state["mode"],
                                            state["parse_id"], state["binding_set_id"])
            for attached in self._generation_rows("generation_link", generation):
                self._link_sequence(generation, self._link(attached["link_id"]))
            for attached in self._generation_rows("generation_event", generation):
                event = self._event(attached["event_id"])
                self._event_sequence(generation, attached["sequence"], event)
                if event.decision_id is not None:
                    _require(self.connection.execute(
                        "SELECT 1 FROM generation_link WHERE generation_id = ? AND link_id = ?",
                        (generation, event.decision_id),
                    ).fetchone() is not None, "event accepted decision is not attached to generation")
            for departure in self._generation_rows("departure", generation):
                self._validate_departure(generation, departure["sequence"], departure["eid"],
                                         departure["occurrence_id"], departure["status"], departure["reason"])
            issues = self.check_integrity()
            if issues:
                raise IndexStoreError("corrupt", "; ".join(issues))
            self.connection.execute("UPDATE generation SET state = 'published' WHERE id = ?", (generation,))
            self.connection.execute("UPDATE scope SET published_generation_id = ? WHERE id = ?",
                                    (generation, record["scope_id"]))
            self.check_storage_limit(max_storage_bytes, reserve_commit=True)

    def _overlapping_eid(self, generation: str, end_sequence: int) -> bool:
        # For nonempty half-open intervals ordered by start, an overlap exists
        # iff a prior maximum end exceeds this start. MAX (not LAG) also detects
        # nested intervals; the validated member count bounds every open end.
        return self.connection.execute(
            "/* active-EID overlap proof */ "
            "WITH presence AS (SELECT file_state.from_seq, "
            "max(coalesce(file_state.to_seq, ?)) OVER (PARTITION BY binding.eid "
            "ORDER BY file_state.from_seq, file_state.id "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_end "
            "FROM file_state JOIN binding ON binding.binding_set_id = file_state.binding_set_id "
            "WHERE file_state.generation_id = ?) "
            "SELECT 1 FROM presence WHERE previous_end > from_seq LIMIT 1",
            (end_sequence, generation),
        ).fetchone() is not None

    def collect_generation(self, generation: str) -> None:
        """Collect one unused checkpoint and unreachable evidence without unlinking storage."""
        with self._write_bundle():
            self.generation(generation)
            in_use = self.connection.execute(
                "SELECT 1 FROM scope WHERE published_generation_id = ? "
                "UNION ALL SELECT 1 FROM generation WHERE predecessor = ? LIMIT 1", (generation, generation),
            ).fetchone()
            if in_use is not None:
                raise IndexStoreError("generation_in_use", "Generation is published or retained as a predecessor")
            self._gc_authorized = True
            try:
                self.connection.execute("INSERT INTO gc_guard VALUES (1)")
                for table in ("frontier", "generation_event", "generation_link", "departure", "coverage", "file_state", "membership"):
                    self.connection.execute(f"DELETE FROM {table} WHERE generation_id = ?", (generation,))
                self.connection.execute("DELETE FROM generation WHERE id = ?", (generation,))
                self.connection.execute(
                    "DELETE FROM binding WHERE binding_set_id NOT IN "
                    "(SELECT id FROM generation_evidence WHERE kind = 'binding_set')",
                )
                for table in ("event", "lineage_link", "binding_set", "lineage", "occurrence",
                              "definition", "parse_result", "version", "context", "interpretation"):
                    self.connection.execute(
                        f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM generation_evidence WHERE kind = ?)", (table,),
                    )
                self.connection.execute(
                    "DELETE FROM commit_object WHERE NOT EXISTS ("
                    "SELECT 1 FROM generation WHERE tip_format = object_format AND tip_digest = digest "
                    "UNION ALL SELECT 1 FROM context WHERE anchor_format = object_format AND anchor_digest = digest "
                    "UNION ALL SELECT 1 FROM membership WHERE commit_format = object_format AND commit_digest = digest "
                    "UNION ALL SELECT 1 FROM occurrence WHERE commit_format = object_format AND commit_digest = digest "
                    "UNION ALL SELECT 1 FROM lineage WHERE commit_format = object_format AND commit_digest = digest "
                    "UNION ALL SELECT 1 FROM lineage_link WHERE (commit_format = object_format AND commit_digest = digest) "
                    "OR (parent_format = object_format AND parent_digest = digest) "
                    "UNION ALL SELECT 1 FROM event WHERE (commit_format = object_format AND commit_digest = digest) "
                    "OR (parent_format = object_format AND parent_digest = digest))",
                )
                self.connection.execute("DELETE FROM gc_guard")
            finally:
                self._gc_authorized = False
                self.connection.set_authorizer(self._authorize)

    def check_storage_limit(self, max_bytes: int = 10 * 1024 ** 3, *, reserve_commit: bool = False) -> None:
        """Measure physical storage, optionally reserving a conservative whole-page commit bound."""
        _sqlite_bounds(max_bytes)
        _require(max_bytes > 0, "storage limit must be positive")
        _require(type(reserve_commit) is bool, "reserve_commit must be bool")
        with _storage_errors(self.path):
            database_bytes = self.path.stat().st_size
            wal_bytes = 0
            try:
                wal_bytes = self.path.with_name(self.path.name + "-wal").stat().st_size
            except FileNotFoundError:
                pass
            measured = database_bytes + wal_bytes
            if reserve_commit:
                self._require_writer()
                page_count = self.connection.execute("PRAGMA page_count").fetchone()[0]
                page_size = self.connection.execute("PRAGMA page_size").fetchone()[0]
                measured = (max(database_bytes, page_count * page_size) + wal_bytes
                            + 32 + page_count * (page_size + 24))
            if measured > max_bytes:
                measure = "commit storage reservation" if reserve_commit else "database plus WAL"
                raise IndexStoreError("resource_limit", f"Index {measure} requires {measured} bytes "
                                      f"(limit {max_bytes}); run explicit generation gc to reduce retention")

    def check_integrity(self) -> tuple[str, ...]:
        """Report SQLite structural and foreign-key issues without repairing anything."""
        with _storage_errors(self.path):
            issues = [f"quick_check: {row[0]}" for row in self.connection.execute("PRAGMA quick_check")
                      if row[0] != "ok"]
            issues.extend(
                f"foreign_key_check: table={row[0]} rowid={row[1]} parent={row[2]} key={row[3]}"
                for row in self.connection.execute("PRAGMA foreign_key_check")
            )
            return tuple(issues)