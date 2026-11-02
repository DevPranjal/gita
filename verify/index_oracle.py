"""Independent AST/Git/operation-ledger gates and explicit canonical exports."""

from __future__ import annotations

import ast
import base64
from hashlib import blake2b, sha1, sha256
import json
import sqlite3

from gita.index.model import EntityOccurrence, GitOid, LineageSeed, canonical_encode, content_id
from gita.index.store import IndexStoreError, _load
from verify.index_corpus import git


def encoded(value):
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (list, tuple)):
        return [encoded(item) for item in value]
    if isinstance(value, dict):
        return {key: encoded(item) for key, item in value.items()}
    return value


def update(digest, value):
    digest.update(json.dumps(encoded(value), sort_keys=True, separators=(",", ":")).encode() + b"\n")


def canonical_digest(db: sqlite3.Connection, generation: str) -> str:
    """Same-policy equality: drop ONLY allocation IDs and generation attachment IDs.

    Immutable records (including their EIDs/contexts) are compared intact. Prefix
    generations themselves are operational; their evidence remains included.
    """
    digest = sha256()
    for table in ("version", "parse_result", "definition", "commit_object", "context",
                  "lineage", "binding_set", "binding", "occurrence", "lineage_link", "event"):
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        update(digest, (table, columns))
        names = ", ".join(columns)
        for row in db.execute(f"SELECT {names} FROM {table} ORDER BY {names}"):
            update(digest, tuple(row))
    for table in ("membership", "file_state", "generation_link", "generation_event", "departure", "coverage"):
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")
                   if row[1] not in ("id", "generation_id")]
        names = ", ".join(columns)
        update(digest, (table, columns))
        for row in db.execute(f"SELECT {names} FROM {table} WHERE generation_id = ? ORDER BY {names}",
                              (generation,)):
            update(digest, tuple(row))
    for row in db.execute(
        "SELECT frontier.path, state.from_seq FROM frontier JOIN file_state state "
        "ON state.id = frontier.file_state_id WHERE frontier.generation_id = ? ORDER BY frontier.path",
        (generation,),
    ):
        update(digest, ("frontier", tuple(row)))
    return digest.hexdigest()


def spans(text: str) -> dict:
    lines = text.encode().splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    return {
        node.name: (offsets[node.lineno - 1] + node.col_offset,
                    offsets[node.end_lineno - 1] + node.end_col_offset,
                    node.lineno, node.end_lineno)
        for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)
    }


def validate_records(store, generation):
    """Check stored contracts separately from the independent authored-byte proof."""
    db = store.connection
    try:
        digests = {}
        for row in db.execute("SELECT id FROM version"):
            version = store.get_version(row["id"])
            assert version is not None, "missing version"
            digests[row["id"]] = version.source_digest
        for row in db.execute("SELECT id FROM parse_result"):
            assert store.get_parse(row["id"]) is not None, "missing parse"
        for row in db.execute("SELECT * FROM interpretation"):
            fields = _load(row["fields_json"])
            assert fields[:2] == (row["category"], row["fingerprint"])
            assert row["id"] == row["category"] + "_" + row["fingerprint"].hex()
            assert canonical_encode("evd1", fields) == row["canonical_payload"]
        for row in db.execute("SELECT * FROM commit_object"):
            fields = ((row["object_format"], row["digest"]),
                      (row["tree_format"], row["tree_digest"]),
                      _load(row["parents_json"]), row["message"])
            assert canonical_encode("evd1", fields) == row["canonical_payload"]
        for row in db.execute("SELECT * FROM context"):
            context = store._context(row["id"])
            assert (context.anchor.fields(), context.boundary,
                    "parse_" + context.parse_fingerprint.hex(),
                    "match_" + context.match_fingerprint.hex()) == (
                        (row["anchor_format"], row["anchor_digest"]), row["boundary"],
                        row["parse_id"], row["match_id"])
            assert store._parents(context.anchor) == context.parents
        for row in db.execute("SELECT occurrence.*, parse_result.path AS parse_path, "
                              "parse_result.blob_format AS parse_blob_format, "
                              "parse_result.blob_digest AS parse_blob_digest "
                              "FROM occurrence JOIN definition ON definition.id = occurrence.definition_id "
                              "JOIN parse_result ON parse_result.id = definition.parse_id"):
            fields = _load(row["fields_json"])
            occurrence = EntityOccurrence(GitOid(*fields[0]), fields[1], GitOid(*fields[2]), *fields[3:])
            assert occurrence.id == row["id"]
            assert canonical_encode("occ1", occurrence.fields()) == row["canonical_payload"]
            assert occurrence.fields() == (
                (row["commit_format"], row["commit_digest"]), row["path"],
                (row["blob_format"], row["blob_digest"]), row["object_type"],
                row["mode"], row["definition_id"])
            assert (occurrence.path, occurrence.blob.fields()) == (
                row["parse_path"], (row["parse_blob_format"], row["parse_blob_digest"]))
        for row in db.execute("SELECT * FROM lineage"):
            fields = _load(row["fields_json"])
            seed = LineageSeed(fields[0], GitOid(*fields[1]), *fields[2:])
            assert seed.id == row["id"]
            assert canonical_encode(row["id"].split("_")[0], seed.fields()) == row["canonical_payload"]
            assert (seed.context_id, seed.commit.fields(), seed.occurrence_id, seed.reason) == (
                row["context_id"], (row["commit_format"], row["commit_digest"]),
                row["occurrence_id"], row["reason"])
        for row in db.execute("SELECT * FROM binding_set"):
            pairs = tuple(tuple(pair) for pair in db.execute(
                "SELECT definition_id, eid FROM binding WHERE binding_set_id = ? "
                "ORDER BY definition_id, eid", (row["id"],),
            ))
            fields = (row["parse_id"], pairs)
            assert content_id("bnd1", fields) == row["id"]
            assert canonical_encode("bnd1", fields) == row["canonical_payload"]
            definitions = {item[0] for item in db.execute(
                "SELECT id FROM definition WHERE parse_id = ?", (row["parse_id"],),
            )}
            assert {pair[0] for pair in pairs} == definitions
        record = store.generation(generation)
        for row in db.execute("SELECT * FROM file_state WHERE generation_id = ?", (generation,)):
            store._validate_file_payload(
                record["context_id"], row["path"], row["status"], store._row_oid(row, "blob"),
                row["object_type"], row["mode"], row["parse_id"], row["binding_set_id"],
            )
        for row in db.execute("SELECT link_id FROM generation_link WHERE generation_id = ?", (generation,)):
            link = store._link(row["link_id"])
            store._validate_link(link)
            store._link_sequence(generation, link)
        for row in db.execute("SELECT sequence, event_id FROM generation_event WHERE generation_id = ?",
                              (generation,)):
            event = store._event(row["event_id"])
            store._event_sequence(generation, row["sequence"], event)
            if event.decision_id is not None:
                assert db.execute(
                    "SELECT 1 FROM generation_link WHERE generation_id = ? AND link_id = ?",
                    (generation, event.decision_id),
                ).fetchone() is not None
        for row in db.execute("SELECT * FROM departure WHERE generation_id = ?", (generation,)):
            store._validate_departure(generation, row["sequence"], row["eid"],
                                      row["occurrence_id"], row["status"], row["reason"])
        return digests
    except (IndexStoreError, ValueError, TypeError, KeyError, AssertionError) as error:
        raise AssertionError(f"invalid stored evidence: {error}") from error


def verify(root, truth, store, generation, *, snapshot=False):
    db = store.connection
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    source_digests = validate_records(store, generation)
    membership = [row[0].hex() for row in db.execute(
        "SELECT commit_digest FROM membership WHERE generation_id = ? ORDER BY sequence", (generation,),
    )]
    assert membership == (truth["commits"][-1:] if snapshot else truth["commits"])
    contents, identities, logical_eids, eid_logicals = {}, {}, {}, {}
    semantic = sha256()
    source_checks = definition_checks = transition_checks = digest_checks = 0
    for step, (commit, entry) in enumerate(zip(truth["commits"], truth["ledger"], strict=True)):
        for path in entry["removes"]:
            contents.pop(path)
            identities.pop(path)
        contents.update(entry["writes"])
        identities.update(entry["identities"])
        for path, text in entry["writes"].items():
            assert git(root, "show", f"{commit}:{path}") == text.encode(), "Git bytes disagree with ledger"
            source_checks += 1
        if snapshot and step != len(truth["commits"]) - 1:
            continue
        sequence = 0 if snapshot else step
        expected = {(path.encode(), name): bounds for path, text in contents.items()
                    for name, bounds in spans(text).items()}
        seen, roots = set(), set()
        active_files = dict(db.execute(
            "SELECT path, status FROM file_state WHERE generation_id = ? AND from_seq <= ? "
            "AND (to_seq IS NULL OR to_seq > ?) AND status != 'removed'",
            (generation, sequence, sequence),
        ))
        assert active_files == {path.encode(): "present" for path in contents}, "coverage/inventory mismatch"
        for row in db.execute(
            "SELECT state.path, state.blob_digest, definition.*, binding.eid FROM file_state state "
            "JOIN binding ON binding.binding_set_id = state.binding_set_id "
            "JOIN definition ON definition.id = binding.definition_id "
            "WHERE state.generation_id = ? AND state.from_seq <= ? "
            "AND (state.to_seq IS NULL OR state.to_seq > ?) ORDER BY state.path, definition.start_byte, definition.id",
            (generation, sequence, sequence),
        ):
            path = row["path"].decode()
            original = contents[path].encode()
            assert row["blob_digest"] == sha1(
                b"blob " + str(len(original)).encode("ascii") + b"\0" + original,
            ).digest(), "stored Git object does not identify authored bytes"
            name = "<module>" if row["synthetic_root"] else row["terminal_name"]
            logical = identities[path][name]
            assert logical_eids.setdefault(logical, row["eid"]) == row["eid"], f"expected continuation missing at {step}:{path}:{name}"
            assert eid_logicals.setdefault(row["eid"], logical) == logical, "false accepted link"
            if row["synthetic_root"]:
                assert path not in roots, "duplicate module root"
                roots.add(path)
                assert (row["start_byte"], row["end_byte"], row["start_line"], row["end_line"]) == (
                    0, len(original), 1, max(1, len(original.splitlines())),
                ), "module source span mismatch"
            else:
                key = (row["path"], name)
                bounds = tuple(row[field] for field in ("start_byte", "end_byte", "start_line", "end_line"))
                assert key not in seen and expected[key] == bounds, "AST source span/name mismatch"
                seen.add(key)
                definition_checks += 1
                update(semantic, (step, logical, path, name, bounds,
                                  sha256(contents[path].encode()[bounds[0]:bounds[1]]).hexdigest()))
            authored_span = original[row["start_byte"]:row["end_byte"]]
            assert blake2b(authored_span, digest_size=32).digest() == source_digests[row["version_id"]], (
                f"stored source digest disagrees with authored span at {step}:{path}:{name}"
            )
            digest_checks += 1
        assert seen == set(expected), "missing definitions"
        assert roots == set(contents), "missing module roots"
        for logical, expected_flag in (() if snapshot else entry["transitions"].items()):
            # Each operation must surface a material transition, not merely leave
            # equal endpoints. The full normalized event export is compared too.
            assert db.execute(
                "SELECT 1 FROM generation_event member JOIN event ON event.id = member.event_id "
                "WHERE member.generation_id = ? AND member.sequence = ? AND event.eid = ? "
                "AND event.material = 1 AND event.flags_json LIKE ? LIMIT 1",
                (generation, sequence, logical_eids[logical], '%"' + expected_flag + '"%'),
            ).fetchone() is not None, "missing authored intermediate event"
            transition_checks += 1
    # Compare graph/event evidence through the independently checked ledger mapping,
    # not policy-dependent EIDs. This cross-policy digest assumes identical parse FP.
    for table in ("version", "parse_result", "definition", "occurrence", "commit_object"):
        columns = ", ".join(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
        for row in db.execute(f"SELECT {columns} FROM {table} ORDER BY {columns}"):
            update(semantic, (table, tuple(row)))
    for row in db.execute(
        "SELECT binding.eid, state.path, state.from_seq, state.to_seq, state.status, binding.definition_id "
        "FROM file_state state JOIN binding ON binding.binding_set_id = state.binding_set_id "
        "WHERE state.generation_id = ? ORDER BY state.path, state.from_seq, binding.definition_id",
        (generation,),
    ):
        update(semantic, ("binding_interval", eid_logicals[row[0]], *tuple(row)[1:]))
    for row in db.execute(
        "SELECT id, commit_digest, occurrence_id, reason FROM lineage ORDER BY occurrence_id",
    ):
        update(semantic, ("lineage_seed", eid_logicals[row[0]], *tuple(row)[1:]))
    for row in db.execute(
        "SELECT event.eid, member.sequence, event.source, event.destination, event.flags_json, event.material "
        "FROM generation_event member JOIN event ON event.id = member.event_id "
        "WHERE member.generation_id = ? ORDER BY member.sequence, event.source, event.destination, event.flags_json",
        (generation,),
    ):
        update(semantic, (eid_logicals[row[0]], *tuple(row)[1:]))
    for row in db.execute(
        "SELECT link.source, link.destination, link.status, link.method, link.reason, link.fields_json "
        "FROM generation_link member JOIN lineage_link link ON link.id = member.link_id "
        "WHERE member.generation_id = ? ORDER BY link.source, link.destination, link.method", (generation,),
    ):
        fields = json.loads(row[-1])
        # Link fields have a single context reference at field zero. Keep all
        # actual evidence (population/copy counts, digests, reasons) intact.
        fields[1][0] = "$verified-ledger-context"
        update(semantic, (*tuple(row)[:-1], fields))
    for row in db.execute(
        "SELECT sequence, path, category, reason, omitted, count_known FROM coverage "
        "WHERE generation_id = ? ORDER BY sequence, path, category, reason", (generation,),
    ):
        update(semantic, tuple(row))
    return {"passed": True, "git_source_checks": source_checks, "ast_definition_checks": definition_checks,
            "source_digest_checks": digest_checks,
            "authored_transition_checks": transition_checks,
            "logical_entities": len(logical_eids), "semantic_digest": semantic.hexdigest(),
            "canonical_digest": canonical_digest(db, generation)}


def query_expectations(truth, store, generation, path, eid):
    """Build bounded responses from ledger-verified records, never IndexSession."""
    db = store.connection
    tip = truth["commits"][-1]
    sequence = len(truth["commits"]) - 1
    contents = {}
    for entry in truth["ledger"]:
        for removed in entry["removes"]:
            contents.pop(removed)
        contents.update(entry["writes"])
    assert path.decode() in contents, "query path is not authored at tip"
    context = db.execute(
        "SELECT context.boundary FROM generation JOIN context ON context.id = generation.context_id "
        "WHERE generation.id = ?", (generation,),
    ).fetchone()
    coverage_rows = db.execute(
        "SELECT category, reason FROM coverage WHERE generation_id = ? AND sequence <= ?",
        (generation, sequence),
    ).fetchall()
    excluded = {"unsupported_language", "non_source_object"}
    uncovered = sum(row["category"] != "extraction" or row["reason"] not in {"complete", "removed"}
                    for row in coverage_rows)
    coverage = {
        "working_tree": "out_of_scope", "source_availability": "unchecked",
        "population": "indexed_evidence", "boundary": context["boundary"],
        "status": "partial" if uncovered else "complete", "records": len(coverage_rows),
        "gaps": sum((row["reason"] not in {"complete", "removed"} | excluded)
                    if row["category"] == "extraction" else row["category"] != "source_availability"
                    for row in coverage_rows),
        "uncovered": uncovered,
        "excluded": sum(row["category"] == "extraction" and row["reason"] in excluded
                        for row in coverage_rows),
    }

    def oid(object_format, digest):
        return None if object_format is None else {"object_format": object_format, "hex": digest.hex()}

    def occurrence(row, commit):
        fields = ((row["blob_format"], bytes.fromhex(commit)), row["path"],
                  (row["blob_format"], row["blob_digest"]), row["object_type"],
                  row["mode"], row["definition_id"])
        return {
            "id": content_id("occ1", fields), "generation": generation,
            "commit": oid(*fields[0]), "path_bytes_b64": base64.b64encode(row["path"]).decode("ascii"),
            "path": row["path"].decode(), "blob": oid(*fields[2]),
            "object_type": row["object_type"], "mode": row["mode"],
            "definition_id": row["definition_id"], "names": list(_load(row["names_json"])),
            "span": {key: row[key] for key in ("start_byte", "end_byte", "start_line", "end_line")},
        }

    def saved_occurrence(identifier):
        if identifier is None:
            return None
        row = db.execute(
            "SELECT occurrence.*, definition.names_json, definition.start_byte, definition.end_byte, "
            "definition.start_line, definition.end_line FROM occurrence "
            "JOIN definition ON definition.id = occurrence.definition_id WHERE occurrence.id = ?",
            (identifier,),
        ).fetchone()
        return occurrence(row, row["commit_digest"].hex())

    def page(results, status):
        assert len(results) <= 1000, "query fixture exceeds the measured first-page bound"
        return {"results": results, "status": status,
                "page": {"shown": len(results), "total": len(results), "next_cursor": None},
                "omitted": {"count": 0, "count_known": True}, "coverage": coverage}

    rows = db.execute(
        "SELECT state.*, binding.eid, definition.id AS definition_id, definition.names_json, "
        "definition.start_byte, definition.end_byte, definition.start_line, definition.end_line, definition.kind "
        "FROM file_state state JOIN binding ON binding.binding_set_id = state.binding_set_id "
        "JOIN definition ON definition.id = binding.definition_id "
        "WHERE state.generation_id = ? AND state.path = ? AND state.from_seq <= ? "
        "AND (state.to_seq IS NULL OR state.to_seq > ?) AND state.status = 'present'",
        (generation, path, sequence, sequence),
    ).fetchall()
    entities = [occurrence(row, tip) for row in rows]
    entities.sort(key=lambda item: (item["path_bytes_b64"], item["span"]["start_byte"],
                                   next(row["kind"] for row in rows if row["definition_id"] == item["definition_id"]),
                                   item["id"]))
    assert len(entities) == len(spans(contents[path.decode()])) + 1, "query population differs from authored AST"
    selected = [occurrence(row, tip) for row in rows if row["eid"] == eid]
    assert len(selected) == 1, "query EID is not uniquely present at the authored tip"

    def evidence_json(value):
        if isinstance(value, bytes):
            return {"bytes_b64": base64.b64encode(value).decode("ascii")}
        if isinstance(value, tuple):
            return [evidence_json(item) for item in value]
        return value

    events = []
    for row in db.execute(
        "SELECT event.* FROM generation_event member JOIN event ON event.id = member.event_id "
        "WHERE member.generation_id = ? AND event.eid = ? AND member.sequence <= ? "
        "ORDER BY member.sequence DESC, event.id DESC", (generation, eid, sequence),
    ):
        decision = None
        if row["decision_id"] is not None:
            link = db.execute("SELECT * FROM lineage_link WHERE id = ?", (row["decision_id"],)).fetchone()
            fields = _load(link["fields_json"])
            decision = {"id": link["id"], "status": link["status"], "method": link["method"],
                        "reason": link["reason"],
                        "evidence": {key: evidence_json(value) for key, value in fields[8]}}
        events.append({
            "id": row["id"], "eid": eid, "commit": oid(row["commit_format"], row["commit_digest"]),
            "parent": oid(row["parent_format"], row["parent_digest"]),
            "source": saved_occurrence(row["source"]), "destination": saved_occurrence(row["destination"]),
            "flags": list(_load(row["flags_json"])), "material": bool(row["material"]), "decision": decision,
        })
    assert events, "query history lacks authored birth/observation"
    return {
        "entities": page(entities, "present"),
        "state": {"eid": eid, "state": "present", "occurrence": selected[0],
                  "reason": None, "last_known_occurrence": None},
        "history": page(events, "ok"),
    }
