"""Executable generation storage schema, added to the legacy evidence tables."""

from __future__ import annotations


COLUMNS = {
    "interpretation": "id category fingerprint canonical_payload fields_json",
    "commit_object": "object_format digest tree_format tree_digest parents_json message canonical_payload",
    "context": "id canonical_payload fields_json anchor_format anchor_digest boundary parse_id match_id",
    "scope": "id name published_generation_id",
    "generation": "id scope_id context_id tip_format tip_digest predecessor state",
    "membership": "generation_id sequence commit_format commit_digest parent_format parent_digest",
    "lineage": "id canonical_payload fields_json context_id commit_format commit_digest occurrence_id reason",
    "binding_set": "id canonical_payload parse_id",
    "binding": "binding_set_id definition_id eid",
    "file_state": "id generation_id path from_seq to_seq status blob_format blob_digest object_type mode parse_id binding_set_id",
    "frontier": "generation_id path file_state_id",
    "occurrence": "id canonical_payload fields_json commit_format commit_digest path blob_format blob_digest object_type mode definition_id",
    "lineage_link": "id canonical_payload fields_json context_id parent_format parent_digest commit_format commit_digest source destination status method reason",
    "generation_link": "generation_id link_id",
    "event": "id canonical_payload fields_json context_id eid parent_format parent_digest commit_format commit_digest source destination flags_json material decision_id",
    "generation_event": "generation_id sequence event_id",
    "departure": "generation_id sequence eid occurrence_id status reason",
    "coverage": "id generation_id sequence path category reason omitted count_known",
    "gc_guard": "id",
}


def _oid(prefix: str, *, optional: bool = False) -> str:
    required = "" if optional else " NOT NULL"
    return f"""{prefix}_format TEXT{required}, {prefix}_digest BLOB{required},
        CHECK (({prefix}_format IS NULL AND {prefix}_digest IS NULL)
            OR ({prefix}_format IS NOT NULL AND {prefix}_digest IS NOT NULL
                AND {prefix}_format IN ('sha1', 'sha256')
                AND typeof({prefix}_digest) = 'blob'
                AND length({prefix}_digest) = CASE {prefix}_format WHEN 'sha1' THEN 20 ELSE 32 END))"""


_PAYLOAD = """canonical_payload BLOB NOT NULL CHECK(typeof(canonical_payload) = 'blob'),
    fields_json TEXT NOT NULL CHECK(typeof(fields_json) = 'text')"""
_PATH = "path BLOB NOT NULL CHECK(typeof(path) = 'blob' AND length(path) > 0 AND instr(path, x'00') = 0)"

SCHEMA = (
    """CREATE TABLE interpretation (
        id TEXT PRIMARY KEY NOT NULL,
        category TEXT NOT NULL CHECK(category IN ('parse', 'match')),
        fingerprint BLOB NOT NULL CHECK(typeof(fingerprint) = 'blob' AND length(fingerprint) = 32),
        canonical_payload BLOB NOT NULL CHECK(typeof(canonical_payload) = 'blob'),
        fields_json TEXT NOT NULL,
        UNIQUE(category, fingerprint)
    )""",
    """CREATE TABLE commit_object (
        object_format TEXT NOT NULL CHECK(object_format IN ('sha1', 'sha256')),
        digest BLOB NOT NULL CHECK(typeof(digest) = 'blob' AND
            length(digest) = CASE object_format WHEN 'sha1' THEN 20 ELSE 32 END),
        tree_format TEXT NOT NULL CHECK(tree_format = object_format),
        tree_digest BLOB NOT NULL CHECK(typeof(tree_digest) = 'blob' AND length(tree_digest) = length(digest)),
        parents_json TEXT NOT NULL,
        message BLOB NOT NULL CHECK(typeof(message) = 'blob'),
        canonical_payload BLOB NOT NULL CHECK(typeof(canonical_payload) = 'blob'),
        PRIMARY KEY(object_format, digest)
    )""",
    f"""CREATE TABLE context (
        id TEXT PRIMARY KEY NOT NULL, {_PAYLOAD},
        anchor_format TEXT NOT NULL, anchor_digest BLOB NOT NULL,
        boundary TEXT NOT NULL CHECK(boundary IN ('root', 'bounded', 'shallow', 'missing_parent')),
        parse_id TEXT NOT NULL REFERENCES interpretation(id),
        match_id TEXT NOT NULL REFERENCES interpretation(id),
        FOREIGN KEY(anchor_format, anchor_digest) REFERENCES commit_object(object_format, digest)
    )""",
    """CREATE TABLE scope (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL UNIQUE CHECK(typeof(name) = 'text' AND length(name) > 0),
        published_generation_id TEXT REFERENCES generation(id)
    )""",
    """CREATE TABLE generation (
        id TEXT PRIMARY KEY NOT NULL,
        scope_id TEXT NOT NULL REFERENCES scope(id),
        context_id TEXT NOT NULL REFERENCES context(id),
        tip_format TEXT NOT NULL, tip_digest BLOB NOT NULL,
        predecessor TEXT REFERENCES generation(id),
        state TEXT NOT NULL CHECK(state IN ('staging', 'published')),
        FOREIGN KEY(tip_format, tip_digest) REFERENCES commit_object(object_format, digest)
    )""",
    f"""CREATE TABLE membership (
        generation_id TEXT NOT NULL REFERENCES generation(id),
        sequence INTEGER NOT NULL CHECK(typeof(sequence) = 'integer' AND sequence >= 0),
        commit_format TEXT NOT NULL, commit_digest BLOB NOT NULL,
        {_oid('parent', optional=True)},
        PRIMARY KEY(generation_id, sequence),
        UNIQUE(generation_id, commit_format, commit_digest),
        FOREIGN KEY(commit_format, commit_digest) REFERENCES commit_object(object_format, digest),
        CHECK(parent_format IS NULL OR parent_format = commit_format)
    )""",
    f"""CREATE TABLE occurrence (
        id TEXT PRIMARY KEY NOT NULL, {_PAYLOAD},
        commit_format TEXT NOT NULL, commit_digest BLOB NOT NULL,
        {_PATH},
        blob_format TEXT NOT NULL, blob_digest BLOB NOT NULL,
        object_type TEXT NOT NULL CHECK(object_type = 'blob'),
        mode INTEGER NOT NULL CHECK(typeof(mode) = 'integer' AND mode IN (33188, 33261)),
        definition_id TEXT NOT NULL REFERENCES definition(id),
        FOREIGN KEY(commit_format, commit_digest) REFERENCES commit_object(object_format, digest),
        CHECK(blob_format = commit_format AND typeof(blob_digest) = 'blob' AND length(blob_digest) = length(commit_digest))
    )""",
    f"""CREATE TABLE lineage (
        id TEXT PRIMARY KEY NOT NULL, {_PAYLOAD},
        context_id TEXT NOT NULL REFERENCES context(id),
        commit_format TEXT NOT NULL, commit_digest BLOB NOT NULL,
        occurrence_id TEXT NOT NULL REFERENCES occurrence(id), reason TEXT NOT NULL,
        FOREIGN KEY(commit_format, commit_digest) REFERENCES commit_object(object_format, digest)
    )""",
    """CREATE TABLE binding_set (
        id TEXT PRIMARY KEY NOT NULL,
        canonical_payload BLOB NOT NULL CHECK(typeof(canonical_payload) = 'blob'),
        parse_id TEXT NOT NULL REFERENCES parse_result(id)
    )""",
    """CREATE TABLE binding (
        binding_set_id TEXT NOT NULL REFERENCES binding_set(id),
        definition_id TEXT NOT NULL REFERENCES definition(id),
        eid TEXT NOT NULL REFERENCES lineage(id),
        PRIMARY KEY(binding_set_id, definition_id), UNIQUE(binding_set_id, eid)
    )""",
    f"""CREATE TABLE file_state (
        id INTEGER PRIMARY KEY,
        generation_id TEXT NOT NULL REFERENCES generation(id), {_PATH},
        from_seq INTEGER NOT NULL CHECK(typeof(from_seq) = 'integer' AND from_seq >= 0),
        to_seq INTEGER CHECK(to_seq IS NULL OR (typeof(to_seq) = 'integer' AND to_seq > from_seq)),
        status TEXT NOT NULL CHECK(status IN ('present', 'removed', 'unknown')),
        blob_format TEXT, blob_digest BLOB, object_type TEXT, mode INTEGER,
        parse_id TEXT REFERENCES parse_result(id), binding_set_id TEXT REFERENCES binding_set(id),
        FOREIGN KEY(generation_id, from_seq) REFERENCES membership(generation_id, sequence),
        FOREIGN KEY(generation_id, to_seq) REFERENCES membership(generation_id, sequence),
        UNIQUE(generation_id, path, from_seq), UNIQUE(generation_id, path, id),
        CHECK((blob_format IS NULL AND blob_digest IS NULL AND object_type IS NULL AND mode IS NULL)
            OR (blob_format IS NOT NULL AND blob_digest IS NOT NULL AND object_type IS NOT NULL AND mode IS NOT NULL
                AND blob_format IN ('sha1', 'sha256') AND typeof(blob_digest) = 'blob'
                AND length(blob_digest) = CASE blob_format WHEN 'sha1' THEN 20 ELSE 32 END
                AND ((object_type = 'blob' AND mode IN (33188, 33261, 40960))
                    OR (object_type = 'commit' AND mode = 57344) OR (object_type = 'tree' AND mode = 16384)))),
        CHECK(status != 'present' OR (parse_id IS NOT NULL AND binding_set_id IS NOT NULL
            AND blob_format IS NOT NULL AND object_type = 'blob' AND mode IN (33188, 33261))),
        CHECK(status != 'removed' OR (blob_format IS NULL AND parse_id IS NULL AND binding_set_id IS NULL)),
        CHECK(status != 'unknown' OR binding_set_id IS NULL),
        CHECK(parse_id IS NULL OR blob_format IS NOT NULL)
    )""",
    f"""CREATE TABLE frontier (
        generation_id TEXT NOT NULL REFERENCES generation(id), {_PATH},
        file_state_id INTEGER NOT NULL REFERENCES file_state(id),
        PRIMARY KEY(generation_id, path),
        FOREIGN KEY(generation_id, path, file_state_id) REFERENCES file_state(generation_id, path, id)
    )""",
    f"""CREATE TABLE lineage_link (
        id TEXT PRIMARY KEY NOT NULL, {_PAYLOAD},
        context_id TEXT NOT NULL REFERENCES context(id),
        parent_format TEXT NOT NULL, parent_digest BLOB NOT NULL,
        commit_format TEXT NOT NULL, commit_digest BLOB NOT NULL,
        source TEXT NOT NULL REFERENCES occurrence(id), destination TEXT NOT NULL REFERENCES occurrence(id),
        status TEXT NOT NULL CHECK(status IN ('accepted', 'candidate', 'rejected', 'ambiguous')),
        method TEXT NOT NULL, reason TEXT NOT NULL,
        FOREIGN KEY(parent_format, parent_digest) REFERENCES commit_object(object_format, digest),
        FOREIGN KEY(commit_format, commit_digest) REFERENCES commit_object(object_format, digest),
        CHECK(parent_format = commit_format), CHECK(status != 'accepted' OR method != 'similarity')
    )""",
    """CREATE TABLE generation_link (
        generation_id TEXT NOT NULL REFERENCES generation(id), link_id TEXT NOT NULL REFERENCES lineage_link(id),
        PRIMARY KEY(generation_id, link_id)
    )""",
    f"""CREATE TABLE event (
        id TEXT PRIMARY KEY NOT NULL, {_PAYLOAD},
        context_id TEXT NOT NULL REFERENCES context(id), eid TEXT NOT NULL REFERENCES lineage(id),
        parent_format TEXT, parent_digest BLOB,
        commit_format TEXT NOT NULL, commit_digest BLOB NOT NULL,
        source TEXT REFERENCES occurrence(id), destination TEXT REFERENCES occurrence(id),
        flags_json TEXT NOT NULL, material INTEGER NOT NULL CHECK(material IN (0, 1)),
        decision_id TEXT REFERENCES lineage_link(id),
        FOREIGN KEY(parent_format, parent_digest) REFERENCES commit_object(object_format, digest),
        FOREIGN KEY(commit_format, commit_digest) REFERENCES commit_object(object_format, digest),
        CHECK((parent_format IS NULL AND parent_digest IS NULL AND source IS NULL)
            OR (parent_format IS NOT NULL AND parent_digest IS NOT NULL AND parent_format = commit_format)),
        CHECK(source IS NOT NULL OR destination IS NOT NULL)
    )""",
    """CREATE TABLE generation_event (
        generation_id TEXT NOT NULL REFERENCES generation(id), sequence INTEGER NOT NULL,
        event_id TEXT NOT NULL REFERENCES event(id), PRIMARY KEY(generation_id, sequence, event_id),
        FOREIGN KEY(generation_id, sequence) REFERENCES membership(generation_id, sequence)
    )""",
    """CREATE TABLE departure (
        generation_id TEXT NOT NULL REFERENCES generation(id), sequence INTEGER NOT NULL CHECK(sequence > 0),
        eid TEXT NOT NULL REFERENCES lineage(id), occurrence_id TEXT NOT NULL REFERENCES occurrence(id),
        status TEXT NOT NULL CHECK(status IN ('removed', 'unknown')), reason TEXT NOT NULL CHECK(length(reason) > 0),
        PRIMARY KEY(generation_id, eid),
        FOREIGN KEY(generation_id, sequence) REFERENCES membership(generation_id, sequence)
    )""",
    """CREATE TABLE coverage (
        id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES generation(id), sequence INTEGER NOT NULL,
        path BLOB CHECK(path IS NULL OR (typeof(path) = 'blob' AND length(path) > 0 AND instr(path, x'00') = 0)),
        category TEXT NOT NULL CHECK(length(category) > 0), reason TEXT NOT NULL CHECK(length(reason) > 0),
        omitted INTEGER CHECK(omitted IS NULL OR (typeof(omitted) = 'integer' AND omitted >= 0)),
        count_known INTEGER NOT NULL CHECK(count_known IN (0, 1)),
        CHECK((omitted IS NULL AND count_known = 0) OR (omitted IS NOT NULL AND count_known = 1)),
        FOREIGN KEY(generation_id, sequence) REFERENCES membership(generation_id, sequence)
    )""",
    "CREATE TABLE gc_guard (id INTEGER PRIMARY KEY CHECK(id = 1))",
    "CREATE INDEX definition_names ON definition(parse_id, names_json)",
    "CREATE INDEX definition_terminal ON definition(terminal_name)",
    "CREATE INDEX binding_eid ON binding(eid, binding_set_id)",
    "CREATE INDEX file_state_binding ON file_state(binding_set_id, generation_id)",
    "CREATE INDEX file_state_parse ON file_state(parse_id, generation_id)",
    "CREATE INDEX event_history ON event(eid, commit_format, commit_digest)",
    "CREATE INDEX departure_history ON departure(generation_id, eid, sequence)",
    "CREATE INDEX coverage_selector ON coverage(generation_id, sequence, path)",
    "CREATE INDEX generation_predecessor ON generation(predecessor)",
    "CREATE INDEX occurrence_definition ON occurrence(definition_id)",
    "CREATE INDEX lineage_context ON lineage(context_id)",
    "CREATE UNIQUE INDEX accepted_source ON lineage_link(context_id, parent_format, parent_digest, commit_format, commit_digest, source) WHERE status = 'accepted'",
    "CREATE UNIQUE INDEX accepted_destination ON lineage_link(context_id, parent_format, parent_digest, commit_format, commit_digest, destination) WHERE status = 'accepted'",
    """CREATE VIEW evidence_edge(kind, id, child_kind, child_id) AS
        SELECT 'binding_set', id, 'parse_result', parse_id FROM binding_set
        UNION ALL SELECT 'binding_set', binding_set_id, 'lineage', eid FROM binding
        UNION ALL SELECT 'lineage', id, 'occurrence', occurrence_id FROM lineage
        UNION ALL SELECT 'lineage', id, 'context', context_id FROM lineage
        UNION ALL SELECT 'event', id, 'lineage', eid FROM event
        UNION ALL SELECT 'event', id, 'occurrence', source FROM event WHERE source IS NOT NULL
        UNION ALL SELECT 'event', id, 'occurrence', destination FROM event WHERE destination IS NOT NULL
        UNION ALL SELECT 'event', id, 'lineage_link', decision_id FROM event WHERE decision_id IS NOT NULL
        UNION ALL SELECT 'lineage_link', id, 'context', context_id FROM lineage_link
        UNION ALL SELECT 'lineage_link', id, 'occurrence', source FROM lineage_link
        UNION ALL SELECT 'lineage_link', id, 'occurrence', destination FROM lineage_link
        UNION ALL SELECT 'occurrence', id, 'definition', definition_id FROM occurrence
        UNION ALL SELECT 'definition', id, 'parse_result', parse_id FROM definition
        UNION ALL SELECT 'definition', id, 'version', version_id FROM definition
        UNION ALL SELECT 'parse_result', parse_id, 'definition', id FROM definition
        UNION ALL SELECT 'context', id, 'interpretation', parse_id FROM context
        UNION ALL SELECT 'context', id, 'interpretation', match_id FROM context""",
    """CREATE VIEW generation_evidence(generation_id, kind, id) AS
        WITH RECURSIVE reachable(generation_id, kind, id) AS (
            SELECT id, 'context', context_id FROM generation
            UNION SELECT generation_id, 'parse_result', parse_id FROM file_state WHERE parse_id IS NOT NULL
            UNION SELECT generation_id, 'binding_set', binding_set_id FROM file_state WHERE binding_set_id IS NOT NULL
            UNION SELECT generation_id, 'event', event_id FROM generation_event
            UNION SELECT generation_id, 'lineage_link', link_id FROM generation_link
            UNION SELECT generation_id, 'lineage', eid FROM departure
            UNION SELECT generation_id, 'occurrence', occurrence_id FROM departure
            UNION SELECT reachable.generation_id, edge.child_kind, edge.child_id
                FROM reachable JOIN evidence_edge edge ON edge.kind = reachable.kind AND edge.id = reachable.id
        ) SELECT generation_id, kind, id FROM reachable""",
    """CREATE TRIGGER scope_pointer_update BEFORE UPDATE ON scope
        WHEN NEW.published_generation_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM generation WHERE id = NEW.published_generation_id AND scope_id = NEW.id AND state = 'published')
        BEGIN SELECT RAISE(ABORT, 'scope pointer must select its published generation'); END""",
    """CREATE TRIGGER scope_pointer_insert BEFORE INSERT ON scope
        WHEN NEW.published_generation_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'new scopes cannot select a generation'); END""",
    """CREATE TRIGGER generation_insert BEFORE INSERT ON generation WHEN NEW.state != 'staging'
        BEGIN SELECT RAISE(ABORT, 'generations must start staging'); END""",
    """CREATE TRIGGER generation_update BEFORE UPDATE ON generation WHEN OLD.state = 'published'
        BEGIN SELECT RAISE(ABORT, 'published generation is immutable'); END""",
    """CREATE TRIGGER generation_delete BEFORE DELETE ON generation
        WHEN OLD.state = 'published' AND NOT EXISTS (SELECT 1 FROM gc_guard)
        BEGIN SELECT RAISE(ABORT, 'published generation requires explicit collection'); END""",
    """CREATE TRIGGER context_interpretations BEFORE INSERT ON context WHEN
        NOT EXISTS (SELECT 1 FROM interpretation WHERE id = NEW.parse_id AND category = 'parse') OR
        NOT EXISTS (SELECT 1 FROM interpretation WHERE id = NEW.match_id AND category = 'match')
        BEGIN SELECT RAISE(ABORT, 'context interpretation category mismatch'); END""",
    """CREATE TRIGGER definition_containment BEFORE INSERT ON definition WHEN
        NOT EXISTS (SELECT 1 FROM parse_result WHERE id = NEW.parse_id AND status = 'complete' AND byte_length >= NEW.end_byte)
        OR (NEW.parent_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM definition WHERE id = NEW.parent_id
            AND parse_id = NEW.parse_id AND start_byte <= NEW.start_byte AND end_byte >= NEW.end_byte
            AND start_line <= NEW.start_line AND end_line >= NEW.end_line))
        BEGIN SELECT RAISE(ABORT, 'definition containment mismatch'); END""",
    """CREATE TRIGGER binding_parse BEFORE INSERT ON binding WHEN NOT EXISTS (
        SELECT 1 FROM binding_set JOIN definition ON definition.parse_id = binding_set.parse_id
        WHERE binding_set.id = NEW.binding_set_id AND definition.id = NEW.definition_id)
        BEGIN SELECT RAISE(ABORT, 'binding definition belongs to another parse'); END""",
)

for _operation in ("INSERT", "UPDATE"):
    SCHEMA += (
        f"""CREATE TRIGGER file_state_overlap_{_operation.lower()} BEFORE {_operation} ON file_state WHEN EXISTS (
            SELECT 1 FROM file_state WHERE generation_id = NEW.generation_id AND path = NEW.path
                AND id != NEW.id AND (NEW.to_seq IS NULL OR from_seq < NEW.to_seq)
                AND (to_seq IS NULL OR to_seq > NEW.from_seq))
            BEGIN SELECT RAISE(ABORT, 'overlapping file intervals'); END""",
        f"""CREATE TRIGGER frontier_open_{_operation.lower()} BEFORE {_operation} ON frontier WHEN NOT EXISTS (
            SELECT 1 FROM file_state WHERE id = NEW.file_state_id AND generation_id = NEW.generation_id
                AND path = NEW.path AND to_seq IS NULL)
            BEGIN SELECT RAISE(ABORT, 'frontier requires its own open file state'); END""",
    )

for _table in ("membership", "file_state", "frontier", "generation_link", "generation_event", "departure", "coverage"):
    for _operation in ("INSERT", "UPDATE", "DELETE"):
        _references = ("OLD", "NEW") if _operation == "UPDATE" else (("OLD",) if _operation == "DELETE" else ("NEW",))
        _condition = " OR ".join(
            f"EXISTS (SELECT 1 FROM generation WHERE id = {reference}.generation_id AND state = 'published')"
            for reference in _references
        )
        _guard = " AND NOT EXISTS (SELECT 1 FROM gc_guard)" if _operation == "DELETE" else ""
        SCHEMA += (f"""CREATE TRIGGER {_table}_published_{_operation.lower()} BEFORE {_operation} ON {_table}
            WHEN ({_condition}){_guard}
            BEGIN SELECT RAISE(ABORT, 'published generation rows are immutable'); END""",)

for _table in ("interpretation", "commit_object", "context", "lineage", "binding_set", "binding", "occurrence", "lineage_link", "event"):
    for _operation in ("UPDATE", "DELETE"):
        _guard = " WHEN NOT EXISTS (SELECT 1 FROM gc_guard)" if _operation == "DELETE" else ""
        SCHEMA += (f"""CREATE TRIGGER {_table}_immutable_{_operation.lower()} BEFORE {_operation} ON {_table}{_guard}
            BEGIN SELECT RAISE(ABORT, 'immutable evidence'); END""",)

for _table in ("parse_result", "version", "definition"):
    for _operation in ("UPDATE", "DELETE"):
        _guard = " AND NOT EXISTS (SELECT 1 FROM gc_guard)" if _operation == "DELETE" else ""
        SCHEMA += (f"""CREATE TRIGGER {_table}_published_{_operation.lower()} BEFORE {_operation} ON {_table}
            WHEN EXISTS (SELECT 1 FROM generation_evidence evidence JOIN generation ON generation.id = evidence.generation_id
                WHERE generation.state = 'published' AND evidence.kind = '{_table}' AND evidence.id = OLD.id){_guard}
            BEGIN SELECT RAISE(ABORT, 'published evidence is immutable'); END""",)

for _table, _column in (("definition", "parse_id"), ("binding", "binding_set_id")):
    SCHEMA += (f"""CREATE TRIGGER {_table}_published_insert BEFORE INSERT ON {_table}
        WHEN EXISTS (SELECT 1 FROM file_state JOIN generation ON generation.id = file_state.generation_id
            WHERE file_state.{_column} = NEW.{_column} AND generation.state = 'published')
        BEGIN SELECT RAISE(ABORT, 'cannot extend published evidence'); END""",)