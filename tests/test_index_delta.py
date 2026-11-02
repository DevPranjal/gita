"""Incremental populations must preserve global identity guards and exact evidence."""

from __future__ import annotations

import subprocess
import sqlite3
import random

import pytest

from gita.index.builder import BuildLimits, build
from gita.index import evidence, store as store_module
from gita.index.store import IndexStore
from gita.vcs.git import CommittedRepo
from verify.index_oracle import canonical_digest, validate_records


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          check=True).stdout.decode().strip()


def repository(root):
    root.mkdir()
    git(root, "init", "--quiet")
    return root


def commit(root, message):
    git(root, "add", ".")
    git(root, "commit", "--quiet", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def function(name, value):
    return f"def {name}(value):\n    return value + {value}\n"


def test_history_inventories_once_and_prepares_unchanged_definitions_once(tmp_path, monkeypatch):
    root = repository(tmp_path / "repo")
    for index in range(24):
        (root / f"file{index}.py").write_text(function(f"compute{index}", index))
    anchor = commit(root, "anchor")
    for index in range(4):
        (root / "file0.py").write_text(function("compute0", 100 + index))
        commit(root, "one edit")
    calls, definitions = [], []
    original_tree = CommittedRepo.iter_tree
    original_metadata = __import__("gita.index.builder", fromlist=["_Builder"])._Builder._metadata

    def tree(self, *args, **kwargs):
        calls.append(args[0])
        yield from original_tree(self, *args, **kwargs)

    def metadata(self, result, *args, **kwargs):
        definitions.extend(definition.id for definition in result.definitions)
        return original_metadata(self, result, *args, **kwargs)

    monkeypatch.setattr(CommittedRepo, "iter_tree", tree)
    monkeypatch.setattr("gita.index.builder._Builder._metadata", metadata)
    with CommittedRepo(root) as repo, IndexStore.create(tmp_path / "index.sqlite") as store:
        result = build(repo, store, since=anchor, limits=BuildLimits(workers=1))
        validate_records(store, result.generation)
        assert len(calls) == 1, "only the anchor may enumerate the complete tree"
        assert len(definitions) == len(set(definitions)), "unchanged parse metadata was reconstructed"


@pytest.mark.parametrize("inline", [False, True])
def test_rust_named_modules_are_namespaces_not_synthetic_file_roots(tmp_path, inline):
    source = (b"mod helpers { pub fn compute(x: u32) -> u32 { x + 1 } }\n"
              if inline else b"mod helpers;\npub fn compute(x: u32) -> u32 { x + 1 }\n")
    bundle = evidence.parse_evidence(source, b"lib.rs")
    definition = next(item for item in bundle.result.definitions if item.names == ("helpers",))
    assert definition.kind == "namespace"
    assert definition.synthetic_root is False
    assert source[definition.span.start_byte:definition.span.end_byte].startswith(b"mod helpers")
    root = repository(tmp_path / "repo")
    (root / "lib.rs").write_bytes(source)
    commit(root, "module")
    (root / "lib.rs").write_bytes(source.replace(b"x + 1", b"x + 2"))
    commit(root, "body edit")
    with CommittedRepo(root) as repo, IndexStore.create(tmp_path / "index.sqlite") as store:
        result = build(repo, store, limits=BuildLimits(workers=1))
        validate_records(store, result.generation)
        assert store.connection.execute(
            "SELECT count(*) FROM definition WHERE kind='module' AND synthetic_root=0"
        ).fetchone()[0] == 0


def test_delta_populations_cold_and_restarted_append_are_equivalent(tmp_path):
    root = repository(tmp_path / "repo")
    (root / "fixed.py").write_text(function("fixed", 9))
    (root / "changed.py").write_text(function("changed", 1))
    commit(root, "anchor")
    (root / "changed.py").write_text(function("changed", 2))
    prefix = commit(root, "body edit")
    (root / "changed.py").rename(root / "moved.py")
    commit(root, "rename file")
    (root / "duplicate.py").write_text((root / "fixed.py").read_text())
    commit(root, "retained-source copy")
    (root / "moved.py").unlink()
    commit(root, "remove")
    with CommittedRepo(root) as repo, IndexStore.create(tmp_path / "cold.sqlite") as store:
        result = build(repo, store, limits=BuildLimits(workers=1))
        validate_records(store, result.generation)
        expected = canonical_digest(store.connection, result.generation)
    with CommittedRepo(root) as repo, IndexStore.create(tmp_path / "append.sqlite") as store:
        build(repo, store, ref=prefix, limits=BuildLimits(workers=1))
    with CommittedRepo(root) as repo, IndexStore.open(tmp_path / "append.sqlite", read_only=False) as store:
        result = build(repo, store, limits=BuildLimits(workers=1))
        validate_records(store, result.generation)
        assert canonical_digest(store.connection, result.generation) == expected
        fixed, duplicate = [store.connection.execute(
            "SELECT binding.eid FROM frontier JOIN file_state state ON state.id=frontier.file_state_id "
            "JOIN binding ON binding.binding_set_id=state.binding_set_id "
            "JOIN definition ON definition.id=binding.definition_id "
            "WHERE frontier.generation_id=? AND state.path=? AND definition.synthetic_root=0",
            (result.generation, path),
        ).fetchone()[0] for path in (b"fixed.py", b"duplicate.py")]
        assert fixed != duplicate, "global retained-copy guard must include unchanged files"


def test_version_validation_reuses_decoding_but_rereads_every_stored_column(tmp_path, monkeypatch):
    bundle = evidence.parse_evidence(b"def compute(value):\n    return value + 1\n", b"file.py")
    with IndexStore.create(tmp_path / "cache.sqlite") as store:
        with store.transaction():
            store.put_parse(bundle.result, bundle.versions)
        calls = []
        original = store_module._load

        def load(document):
            calls.append(document)
            return original(document)

        monkeypatch.setattr(store_module, "_load", load)
        identifier = bundle.versions[-1].id
        assert store.get_version(identifier) == store.get_version(identifier)
        assert len(calls) == 1, "immutable version payload was decoded and validated twice"
        # Raw mutations must still be detected even after a successful read.
        with store.transaction():
            store.connection.execute("UPDATE version SET fields_json='[]' WHERE id=?", (identifier,))
        with pytest.raises(store_module.IndexStoreError, match="Invalid stored evidence"):
            store.get_version(identifier)


def test_excluded_extensions_do_not_read_or_dispatch_blob_contents(tmp_path, monkeypatch):
    root = repository(tmp_path / "repo")
    (root / "opaque.txt").write_bytes(b"\0" + b"opaque" * 100)
    commit(root, "opaque")
    oid = bytes.fromhex(git(root, "rev-parse", "HEAD:opaque.txt"))
    original = CommittedRepo.read_object

    def read(self, object_id):
        assert object_id.digest != oid, "unsupported inventory classification read blob contents"
        return original(self, object_id)

    monkeypatch.setattr(CommittedRepo, "read_object", read)
    with CommittedRepo(root) as repo, IndexStore.create(tmp_path / "index.sqlite") as store:
        result = build(repo, store, limits=BuildLimits(workers=2))
        assert result.status == "partial"  # Excluded coverage is not complete source coverage.
        row = store.connection.execute("SELECT status,byte_length FROM parse_result").fetchone()
        assert tuple(row) == ("unsupported_language", 601)


def test_decoded_record_cache_obeys_byte_and_entry_bounds(tmp_path):
    with IndexStore.create(tmp_path / "cache.sqlite") as store:
        store._decoded = store_module._DecodedRecords(max_bytes=8192, max_entries=2)
        for index in range(8):
            bundle = evidence.parse_evidence(source=function(f"compute{index}", index).encode(),
                                             path=f"f{index}.py".encode())
            with store.transaction():
                store.put_parse(bundle.result, bundle.versions)
            for version in bundle.versions:
                assert store.get_version(version.id) == version
                assert store._decoded.bytes <= 8192
                assert len(store._decoded.entries) <= 2


def overlap_store(tmp_path, intervals):
    """Isolate the SQL relation; expected overlaps come from Python interval math."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE file_state(id INTEGER PRIMARY KEY, generation_id TEXT, path BLOB, "
        "from_seq INTEGER, to_seq INTEGER, binding_set_id TEXT);"
        "CREATE INDEX state_generation ON file_state(generation_id,path,from_seq);"
        "CREATE INDEX state_binding ON file_state(binding_set_id,generation_id);"
        "CREATE TABLE binding(binding_set_id TEXT, definition_id TEXT, eid TEXT, "
        "PRIMARY KEY(binding_set_id,definition_id), UNIQUE(binding_set_id,eid));"
        "CREATE INDEX binding_eid ON binding(eid,binding_set_id);"
    )
    for index, (generation, eid, start, end) in enumerate(intervals):
        db.execute("INSERT INTO file_state VALUES (?,?,?,?,?,?)",
                   (index + 1, generation, f"p{index}".encode(), start, end, f"b{index}"))
        db.execute("INSERT INTO binding VALUES (?,?,?)", (f"b{index}", f"d{index}", eid))
    return IndexStore(tmp_path / "not-written.sqlite", db, read_only=True)


@pytest.mark.parametrize("intervals", [
    [("g", "a", 0, 2), ("g", "a", 2, None)],  # Adjacent half-open presence.
    [("g", "a", 0, None), ("g", "a", 3, 5)],  # Open earlier interval.
    [("g", "a", 0, 10), ("g", "a", 1, 2), ("g", "a", 4, 6)],  # LAG alone is insufficient.
    [("g", "a", 4, 6), ("g", "a", 0, 10), ("g", "a", 1, 2)],  # Row IDs do not order starts.
    [("g", "a", 0, 2), ("g", "a", 0, 1)],  # Equal starts.
    [("g", "a", 0, None), ("g", "b", 0, None), ("other", "a", 0, None)],
])
def test_publication_overlap_guard_matches_independent_interval_math(tmp_path, intervals):
    expected = any(
        left[0] == right[0] == "g" and left[1] == right[1]
        and (left[3] is None or left[3] > right[2])
        and (right[3] is None or right[3] > left[2])
        for index, left in enumerate(intervals) for right in intervals[index + 1:]
    )
    with overlap_store(tmp_path, intervals) as store:
        assert store._overlapping_eid("g", 16) is expected


def test_publication_overlap_guard_randomized_math_parity(tmp_path):
    rng = random.Random(1973)
    for unused in range(40):
        intervals = []
        for index in range(rng.randrange(1, 20)):
            start = rng.randrange(12)
            end = None if rng.randrange(4) == 0 else rng.randrange(start + 1, 16)
            intervals.append(("g" if rng.randrange(4) else "other", f"e{rng.randrange(5)}", start, end))
        expected = any(
            left[0] == right[0] == "g" and left[1] == right[1]
            and (left[3] is None or left[3] > right[2])
            and (right[3] is None or right[3] > left[2])
            for index, left in enumerate(intervals) for right in intervals[index + 1:]
        )
        with overlap_store(tmp_path, intervals) as store:
            assert store._overlapping_eid("g", 16) is expected


def test_publication_overlap_guard_has_subquadratic_vm_work(tmp_path):
    size = 512
    with overlap_store(tmp_path, [("g", f"e{index}", 0, None) for index in range(size)]) as store:
        progress = []
        store.connection.set_progress_handler(lambda: progress.append(1) or 0, 100)
        assert store._overlapping_eid("g", 1) is False
        assert len(progress) * 100 < size * 300, (
            "duplicate-active-EID proof performed a generation-wide interval cross product"
        )
