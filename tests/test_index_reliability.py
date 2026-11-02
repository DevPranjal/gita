"""Persistent, independent S2 equivalence and publication recovery campaigns."""

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

import pytest

from gita.index import builder
from gita.index.builder import BuildLimits, build
from gita.index.store import IndexStore, IndexStoreError
from gita.vcs.git import CommittedRepo
from verify.index_corpus import generate
from verify.index_oracle import canonical_digest, verify


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    truth = generate(root, files=3, functions=2, commits=10)
    return root, truth


def built(root, database, ref, workers=1):
    opener = IndexStore.open(database, read_only=False) if database.exists() else IndexStore.create(database)
    with CommittedRepo(root) as repository, opener as store:
        return build(repository, store, ref=ref, limits=BuildLimits(workers=workers))


def digest(database):
    with IndexStore.open(database) as store:
        return canonical_digest(store.connection, store.published_generation("default"))


def test_serial_parallel_resume_noop_and_cache_equivalence(corpus, tmp_path, monkeypatch):
    root, truth = corpus
    databases = [tmp_path / name for name in ("serial.sqlite", "parallel.sqlite", "resume.sqlite", "uncached.sqlite")]
    built(root, databases[0], truth["commits"][-1])
    built(root, databases[1], truth["commits"][-1], workers=4)
    built(root, databases[2], truth["commits"][4], workers=2)
    built(root, databases[2], truth["commits"][-1], workers=4)
    before = digest(databases[2])
    result = built(root, databases[2], truth["commits"][-1])
    assert result.parsed_files == result.cached_files == 0
    assert digest(databases[2]) == before
    original = builder._CandidateTokens.__init__

    def disabled(self, store, **kwargs):
        original(self, store, max_entries=0)

    monkeypatch.setattr(builder._CandidateTokens, "__init__", disabled)
    built(root, databases[3], truth["commits"][-1])
    assert len({digest(database) for database in databases}) == 1
    with IndexStore.open(databases[0]) as store:
        assert verify(root, truth, store, store.published_generation("default"))["passed"]


def test_wal_reader_snapshot_survives_writer_and_busy_is_typed(corpus, tmp_path):
    root, truth = corpus
    database = tmp_path / "readers.sqlite"
    old = built(root, database, truth["commits"][4])
    with IndexStore.open(database) as reader, reader.snapshot():
        assert reader.published_generation("default") == old.generation
        new = built(root, database, truth["commits"][-1], workers=2)
        assert new.generation != old.generation
        assert reader.published_generation("default") == old.generation
        assert reader.connection.execute(
            "SELECT count(*) FROM membership WHERE generation_id = ?", (old.generation,),
        ).fetchone()[0] == 5
    with ExitStack() as stack:
        first = stack.enter_context(IndexStore.open(database, read_only=False))
        second = stack.enter_context(IndexStore.open(database, read_only=False))
        second.connection.execute("PRAGMA busy_timeout=1")
        stack.enter_context(first.transaction())
        with pytest.raises(IndexStoreError) as error:
            with second.transaction():
                pass
        assert error.value.code == "busy"
    assert built(root, database, truth["commits"][-1]).generation == new.generation


@pytest.mark.parametrize("corruption", ("span", "false_link", "missing_event"))
def test_independent_oracle_rejects_corrupted_evidence(corpus, tmp_path, corruption):
    root, truth = corpus
    database = tmp_path / "oracle.sqlite"
    result = built(root, database, truth["commits"][-1])
    with IndexStore.open(database, read_only=False) as store:
        # Corrupt ONLY this generated disposable fixture after schema validation.
        # Production immutability triggers otherwise correctly block the mutation.
        triggers = store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name IN ('definition', 'binding', 'event')",
        ).fetchall()
        for trigger in triggers:
            store.connection.execute('DROP TRIGGER "' + trigger[0].replace('"', '""') + '"')
        if corruption == "span":
            store.connection.execute(
                "UPDATE definition SET start_byte = start_byte + 1 WHERE synthetic_root = 0 AND start_byte = 0",
            )
        elif corruption == "false_link":
            first = store.connection.execute(
                "SELECT binding_set_id, definition_id FROM binding ORDER BY binding_set_id, definition_id LIMIT 1",
            ).fetchone()
            different = store.connection.execute(
                "SELECT eid FROM binding WHERE binding_set_id != ? AND eid NOT IN "
                "(SELECT eid FROM binding WHERE binding_set_id = ?) LIMIT 1", (first[0], first[0]),
            ).fetchone()
            store.connection.execute(
                "UPDATE binding SET eid = ? WHERE binding_set_id = ? AND definition_id = ?",
                (different[0], first[0], first[1]),
            )
        else:
            store.connection.execute("UPDATE event SET flags_json = '[\"tuple\",[]]' WHERE material = 1")
        with pytest.raises(AssertionError):
            verify(root, truth, store, result.generation)


def test_owned_process_cancel_rolls_back_and_retry_equals_clean(corpus, tmp_path):
    psutil = pytest.importorskip("psutil", reason="install .[bench] for owned process-tree cancellation")
    root, truth = corpus
    database = tmp_path / "cancel.sqlite"
    old = built(root, database, truth["commits"][4])
    previous_digest = digest(database)
    marker = tmp_path / "before-publication.json"
    script = Path(__file__).resolve().parents[1] / "verify" / "index_fault_child.py"
    environment = {**os.environ, "PYTHONPATH": str(script.parents[1] / "src"), "GITA_ENABLE_LEGACY": "0"}
    process = subprocess.Popen(
        [sys.executable, str(script), str(root), str(database), truth["commits"][-1], str(marker)],
        env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    owned = psutil.Process(process.pid)
    identity = owned.create_time()
    try:
        deadline = monotonic() + 25
        while not marker.exists() and process.poll() is None and monotonic() < deadline:
            sleep(0.05)
        assert marker.exists(), "child did not reach persisted, unpublished transaction"
        with IndexStore.open(database) as reader:
            assert reader.published_generation("default") == old.generation
        children = [(child.pid, child.create_time()) for child in owned.children(recursive=True)]
    finally:
        children = locals().get("children", [(child.pid, child.create_time())
                                            for child in owned.children(recursive=True)])
        for pid, created in reversed(children + [(owned.pid, identity)]):
            try:
                current = psutil.Process(pid)
                if current.create_time() == created:
                    current.kill()
            except psutil.NoSuchProcess:
                pass
        process.wait(timeout=10)
    assert digest(database) == previous_digest
    with IndexStore.open(database) as store:
        assert store.connection.execute("SELECT count(*) FROM generation").fetchone()[0] == 1
    built(root, database, truth["commits"][-1])
    clean = tmp_path / "clean.sqlite"
    built(root, clean, truth["commits"][-1])
    assert digest(database) == digest(clean)
