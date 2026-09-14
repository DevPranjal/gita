"""Persistent version and parse evidence cache; publication is not implemented."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator, TYPE_CHECKING

from .model import (
    EntityDefinition, EntityVersion, GitOid, IndexContractError, ParseResult,
    ParseStatus, SourceSpan, _identifier, canonical_encode, require_same_payload,
)

if TYPE_CHECKING:
    from ..vcs.git import Repo


SCHEMA_VERSION = 1
_APPLICATION_ID = 0x47495441
_METADATA = {"schema_version": "1", "cache_format": "evidence-cache-v1"}
_COLUMNS = {
    "metadata": "key value",
    "version": "id canonical_payload fields_json parse_fp kind",
    "parse_result": (
        "id canonical_payload fields_json parse_fp blob_format blob_digest path status byte_length"
    ),
    "definition": (
        "id parse_id version_id parent_id start_byte end_byte start_line end_line "
        "kind names_json discriminator_json synthetic_root ordinal"
    ),
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
        UNIQUE(parse_id, ordinal),
        CHECK(synthetic_root = 0 OR parent_id IS NULL),
        CHECK(start_byte != end_byte OR start_line = end_line)
    )""",
    "CREATE INDEX definition_version ON definition(version_id)",
    "CREATE INDEX definition_parent ON definition(parent_id)",
)


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


class IndexStore:
    """Non-destructive SQLite lifecycle and immutable parse-cache operations."""

    def __init__(self, path: Path, connection: sqlite3.Connection, *, read_only: bool) -> None:
        self.path = path
        self.connection = connection
        self.read_only = read_only
        self._writer_owned = False
        self._scope_active = False

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
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise IndexStoreError("io_error", "SQLite foreign-key enforcement is unavailable")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if application != _APPLICATION_ID or version != SCHEMA_VERSION:
            raise IndexStoreError("schema_mismatch", "Unrecognized index application or schema version")
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
            version = EntityVersion(*_load(row["fields_json"]))
            _valid(version.id == identifier, "version identifier disagrees with payload")
            _valid(canonical_encode("ver1", version.fields()) == row["canonical_payload"],
                   "version canonical payload disagrees with JSON")
            _valid(version.parse_fingerprint == row["parse_fp"] and version.kind == row["kind"],
                   "version columns disagree with payload")
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
                previous = supplied.get(version.id) or self.get_version(version.id)
                if previous is not None:
                    require_same_payload(canonical_encode("ver1", previous.fields()),
                                         canonical_encode("ver1", version.fields()))
                supplied[version.id] = version
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
                self.get_parse(result.id)
                return result.id
            for version in versions:
                self.put_version(version)
            self.connection.execute(
                "INSERT INTO parse_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.id, payload, _dump(result.fields()), result.parse_fingerprint,
                 result.blob.object_format, result.blob.digest, result.path,
                 result.status.value, result.byte_length),
            )
            for ordinal, definition in ordered:
                self.connection.execute(
                    "INSERT INTO definition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (definition.id, definition.parse_id, definition.version_id, definition.parent_id,
                     *definition.span.fields(), definition.kind, _dump(definition.names),
                     _dump(definition.discriminator), int(definition.synthetic_root), ordinal),
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
                _valid(definition.id == definition_row["id"], "definition identifier disagrees with columns")
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