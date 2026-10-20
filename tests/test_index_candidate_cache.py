from types import SimpleNamespace

import pytest

from gita.index.builder import BuildError, _CandidateTokens


def test_candidate_cache_validates_once_and_evicts_by_bytes_and_tokens():
    class Store:
        calls = []

        def get_version(self, identifier):
            self.calls.append(identifier)
            return SimpleNamespace(own=("alpha", "beta"))

    store = Store()
    cache = _CandidateTokens(store, max_bytes=6, max_tokens=4, max_entries=2)
    row = {"version_id": "one", "own_payload": b"abc"}
    assert cache.get(row) == ("alpha", "beta")
    assert cache.get(row) == ("alpha", "beta")
    assert store.calls == ["one"]
    cache.get({"version_id": "two", "own_payload": b"def"})
    cache.get({"version_id": "three", "own_payload": b"ghi"})
    cache.get(row)
    assert store.calls == ["one", "two", "three", "one"]
    assert cache.bytes <= 6 and cache.tokens <= 4 and len(cache.entries) <= 2
    cache.get({"version_id": "large", "own_payload": b"1234567"})
    assert "large" not in cache.entries


def test_candidate_cache_refuses_missing_evidence():
    cache = _CandidateTokens(SimpleNamespace(get_version=lambda unused: None))
    with pytest.raises(BuildError, match="unavailable") as error:
        cache.get({"version_id": "missing", "own_payload": None})
    assert error.value.code == "corrupt"
