"""Regression gates for independent source evidence and timed query correctness."""

from copy import deepcopy
from dataclasses import replace

import pytest

from gita.index.builder import BuildLimits, build
from gita.index.evidence import parse_evidence
from gita.index.query import IndexSession
from gita.index.store import IndexStore, IndexStoreError
from gita.index.worker import EvidenceWorker
from gita.vcs.git import CommittedRepo
from verify import index_corpus
from verify.index_bench_worker import run
from verify.index_corpus import generate
from verify.index_oracle import verify


@pytest.fixture(scope="module")
def query_corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("query-gates") / "corpus"
    truth = generate(root, files=3, functions=2, commits=10)
    config = {"workers": 1}
    assert run(config, truth, root, "query", True)["status"] == "prepared"
    return root, truth, config


@pytest.mark.parametrize("synthetic_root", (False, True))
def test_oracle_rejects_self_consistent_wrong_source_digest(tmp_path, monkeypatch, synthetic_root):
    root = tmp_path / "corpus"
    truth = generate(root, files=1, functions=1, commits=2)

    def corrupted_parse(self, *args, **kwargs):
        bundle = parse_evidence(*args, **kwargs)
        targets = {definition.version_id for definition in bundle.result.definitions
                   if definition.synthetic_root == synthetic_root}
        versions = tuple(replace(version, source_digest=b"z" * 32)
                         if version.id in targets else version for version in bundle.versions)
        version_ids = {old.id: new.id for old, new in zip(bundle.versions, versions, strict=True)}
        definition_ids, definitions = {}, []
        for definition in bundle.result.definitions:
            changed = replace(definition, version_id=version_ids[definition.version_id],
                              parent_id=definition_ids.get(definition.parent_id))
            definition_ids[definition.id] = changed.id
            definitions.append(changed)
        return replace(bundle, result=replace(bundle.result, definitions=tuple(definitions)),
                       versions=versions)

    monkeypatch.setattr(EvidenceWorker, "parse", corrupted_parse)
    with CommittedRepo(root) as repository, IndexStore.create(tmp_path / "oracle.sqlite") as store:
        result = build(repository, store, limits=BuildLimits(workers=1))
        assert store.published_generation("default") == result.generation
        for row in store.connection.execute("SELECT id FROM parse_result"):
            assert store.get_parse(row["id"]) is not None
        with pytest.raises(AssertionError, match="source digest"):
            verify(root, truth, store, result.generation)


@pytest.mark.parametrize("document", ("{", '["tuple",[]]'))
def test_oracle_rejects_malformed_version_json(tmp_path, document):
    root = tmp_path / "corpus"
    truth = generate(root, files=1, functions=1, commits=2)
    with CommittedRepo(root) as repository, IndexStore.create(tmp_path / "oracle.sqlite") as store:
        result = build(repository, store, limits=BuildLimits(workers=1))
        assert verify(root, truth, store, result.generation)["passed"]
        for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'version'",
        ).fetchall():
            store.connection.execute('DROP TRIGGER "' + row["name"].replace('"', '""') + '"')
        identifier = store.connection.execute("SELECT id FROM version LIMIT 1").fetchone()[0]
        store.connection.execute("UPDATE version SET fields_json = ? WHERE id = ?", (document, identifier))
        with pytest.raises(IndexStoreError):
            store.get_version(identifier)
        with pytest.raises(AssertionError, match="stored evidence"):
            verify(root, truth, store, result.generation)


def test_query_benchmark_accepts_correct_responses(query_corpus):
    root, truth, config = query_corpus
    sample = run(config, truth, root, "query", False)
    assert sample["status"] == "passed" and sample["correctness"]["passed"]
    assert sample["counters"]["warm_queries"] == 30
    assert sample["correctness"]["query_response_checks"] == 30
    assert all(len(sample["timings"][label + "_seconds"]) == 10
               for label in ("entities", "state", "history"))


@pytest.mark.parametrize("method,label", (
    ("entities_in", "entities"), ("state_at", "state"), ("history", "history"),
))
@pytest.mark.parametrize("corruption", ("none", "empty", "plausible"))
def test_query_benchmark_rejects_stable_wrong_responses(
    query_corpus, monkeypatch, method, label, corruption,
):
    root, truth, config = query_corpus
    original = getattr(IndexSession, method)
    answers = []

    def wrong(self, *args, **kwargs):
        if not answers:
            if corruption == "none":
                answer = None
            else:
                answer = deepcopy(original(self, *args, **kwargs))
                if corruption == "empty":
                    if method == "state_at":
                        answer.update(state="absent", occurrence=None, reason="before_birth")
                    else:
                        answer["results"] = []
                        answer["page"].update(shown=0, total=0, next_cursor=None)
                        answer["omitted"].update(count=0, count_known=True)
                elif method == "entities_in":
                    answer["results"][0]["span"]["end_byte"] += 1
                elif method == "state_at":
                    answer["occurrence"]["commit"]["hex"] = truth["commits"][0]
                else:
                    answer["results"][0]["flags"] = ["body_changed"]
            answers.append(answer)
        return answers[0]

    monkeypatch.setattr(IndexSession, method, wrong)
    with pytest.raises(AssertionError, match=label + " query"):
        run(config, truth, root, "query", False)


def test_source_gate_accepts_history_and_snapshot(tmp_path):
    root = tmp_path / "corpus"
    truth = generate(root, files=1, functions=1, commits=2)
    for snapshot in (False, True):
        with CommittedRepo(root) as repository, IndexStore.create(
            tmp_path / f"oracle-{snapshot}.sqlite",
        ) as store:
            result = build(repository, store, since=truth["commits"][-1] if snapshot else None,
                           limits=BuildLimits(workers=1))
            assert verify(root, truth, store, result.generation, snapshot=snapshot)["passed"]


def test_source_gate_hashes_original_utf8_crlf_bytes(tmp_path, monkeypatch):
    authored = index_corpus.source

    def source(*args, **kwargs):
        return authored(*args, **kwargs).replace(
            "second = first *", "second = len('café') + first *",
        ).replace("\n", "\r\n")

    monkeypatch.setattr(index_corpus, "source", source)
    root = tmp_path / "corpus"
    truth = generate(root, files=1, functions=2, commits=2)
    with CommittedRepo(root) as repository, IndexStore.create(tmp_path / "oracle.sqlite") as store:
        result = build(repository, store, limits=BuildLimits(workers=1))
        checked = verify(root, truth, store, result.generation)
        assert checked["source_digest_checks"] == 6


def test_query_benchmark_accepts_linked_history_decisions(tmp_path):
    root = tmp_path / "corpus"
    truth = generate(root, files=1, functions=1, commits=5)
    config = {"workers": 1}
    run(config, truth, root, "query", True)
    sample = run(config, truth, root, "query", False)
    assert sample["status"] == "passed"
    assert sample["correctness"]["query_response_checks"] == 30
