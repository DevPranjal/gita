"""Large indexed pages remain usable without hiding integrity or coverage."""
from types import SimpleNamespace

import pytest

from gita.eval.reviewbench import evidence, preparation
from gita.eval.reviewbench.common import EvidenceBudgetError, IntegrityError, ProtocolError
from gita.index.model import GitOid


def test_index_pages_shrink_and_continue_without_losing_rows(monkeypatch):
    monkeypatch.setattr(evidence, "PAGE_CHARS", 300)
    monkeypatch.setattr(evidence, "MAX_RESPONSE_CHARS", 1200, raising=False)
    limits = []

    def entities(path, *, at, limit, cursor):
        assert isinstance(at, GitOid), "Already pinned OIDs must not be re-resolved as text"
        limits.append(limit)
        start = int(cursor or 0)
        end = min(start + limit, 50)
        return {"results": [{"number": i, "padding": "x" * 100} for i in range(start, end)],
                "page": {"shown": end - start, "next_cursor": str(end) if end < 50 else None},
                "coverage": {"status": "partial"}, "omitted": {"count": 50 - end}}

    session = SimpleNamespace(entities_in=entities, generation="generation",
                              context=SimpleNamespace(id="context"))
    broker = object.__new__(evidence.Evidence)
    broker.config = {"scope_digest": "task"}
    broker.secret = b"secret"
    broker.commit = lambda value: value
    broker._index = lambda commit: session
    broker.provenance = lambda commit: {"commit": commit}
    validated = []
    broker._occurrences = lambda value, selected: validated.append(value)
    cursor, rows = "", []
    while True:
        page = broker.entities("file.py", "a" * 40, cursor)
        assert page["coverage"]["status"] == "partial"
        rows.extend(item["number"] for item in page["results"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert rows == list(range(50))
    assert min(limits) < 30
    assert len(validated) > 1
    assert sum(len(page["results"]) for page in validated) == 50


@pytest.mark.parametrize("error,status", [
    (ProtocolError("Indexed evidence output limit"), "failed"),
    (IntegrityError("Wrong returned generation"), "integrity_failed"),
])
def test_expected_budget_refusal_is_not_index_corruption(repo, monkeypatch, error, status):
    base, head = repo.resolve("HEAD^"), repo.resolve("HEAD")
    scope = {"repo": str(repo.root), "task": {"head": head},
             "effective_base": base, "allowed_commits": [base, head], "history_ready": False}

    def refused(scope):
        raise error

    monkeypatch.setattr(preparation, "preflight", refused)
    result = preparation.build_indexes(scope)
    assert result["index_preparation"]["status"] == status


def test_graph_validation_batches_pinned_commits(repo, monkeypatch):
    scope = preparation.source_scope(
        {"base": repo.resolve("HEAD^"), "head": repo.resolve("HEAD"), "id": "task"}, repo.root,
    )
    calls = []
    original = evidence.git

    def observed(root, *args, **kwargs):
        if args[0] == "show":
            calls.append(args)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(evidence, "git", observed)
    broker = evidence.Evidence({**scope, "arm": "B", "cursor_secret": "secret"})
    broker.close()
    assert len(calls) == 1
    assert "--no-walk=unsorted" in calls[0]


def test_unicode_raw_pages_fit_serialized_sdk_budget():
    import json

    broker = object.__new__(evidence.Evidence)
    broker.config = {"scope_digest": "task"}
    broker.secret = b"secret"
    text, cursor, returned = "\u6f22\u5b57" * 10000, "", []
    while True:
        page = broker.page(text, "raw_diff", {}, cursor)
        assert len(json.dumps(page, ensure_ascii=True)) <= 6000
        returned.append(page["text"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert "".join(returned) == text


def test_unpaged_tool_cannot_trigger_sdk_file_offload():
    import json

    broker = object.__new__(evidence.Evidence)
    broker.tools = {"index_status"}
    broker.calls = 0
    broker.log = None
    broker.index_status = lambda: {"oversize": "x" * 30000}
    result = broker.execute("index_status", {})
    assert result["error"]["code"] == "evidence_budget"
    assert len(json.dumps(result)) < 8000


def test_internal_oracle_distinguishes_broker_budget_from_false_empty_answers(repo, monkeypatch):
    scope = preparation.source_scope(
        {"base": repo.resolve("HEAD^"), "head": repo.resolve("HEAD"), "id": "task"}, repo.root,
    )
    scope["history_ready"] = False
    scope = preparation.build_indexes(scope)
    assert scope["indexes"]

    def oversized(*args, **kwargs):
        raise EvidenceBudgetError("One reference exceeds the SDK response bound")

    monkeypatch.setattr(evidence.Evidence, "entities", oversized)
    checked = preparation.preflight(scope)
    assert checked["independent_occurrences_verified"] > 0
    assert checked["broker_budget_gap_files"] > 0
    monkeypatch.setattr(evidence.Evidence, "entities",
                        lambda *args, **kwargs: {"results": [], "next_cursor": None})
    with pytest.raises(IntegrityError):
        preparation.preflight(scope)
