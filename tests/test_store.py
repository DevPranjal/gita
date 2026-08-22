"""Content addressing for parsed trees.

Walking twenty commits of one file parsed 4.2MB of source, 43% of it a second
time: commit N's "before" is commit N-1's "after". A parse depends only on the
bytes and the path, so it can be reused wherever those repeat -- which is the
same reasoning that makes git name objects after their content.
"""

from __future__ import annotations

import pytest

from gita.entities.extractor import extract_path
from gita.entities.store import TreeStore

SOURCE = b"def a():\n    return 1\n"
OTHER = b"def a():\n    return 2\n"


@pytest.fixture
def counting():
    calls = []

    def parse(blob, path):
        calls.append((blob, path))
        return extract_path(blob, path)

    parse.calls = calls
    return parse


class TestReuse:
    def test_identical_content_is_parsed_once(self, counting):
        store = TreeStore()
        first = store.get(SOURCE, "a.py", counting)
        second = store.get(SOURCE, "a.py", counting)
        assert len(counting.calls) == 1
        assert first is second

    def test_different_content_is_parsed_again(self, counting):
        store = TreeStore()
        store.get(SOURCE, "a.py", counting)
        store.get(OTHER, "a.py", counting)
        assert len(counting.calls) == 2

    def test_same_bytes_at_a_different_path_are_a_different_tree(self, counting):
        """The path selects the language, so it belongs in the key."""
        store = TreeStore()
        store.get(SOURCE, "a.py", counting)
        store.get(SOURCE, "b.py", counting)
        assert len(counting.calls) == 2

    def test_a_cached_tree_equals_an_uncached_parse(self):
        store = TreeStore()
        cached = store.get(SOURCE, "a.py", extract_path)
        direct = extract_path(SOURCE, "a.py")
        assert sorted(cached.entities) == sorted(direct.entities)

    def test_hits_and_misses_are_counted(self, counting):
        store = TreeStore()
        store.get(SOURCE, "a.py", counting)
        store.get(SOURCE, "a.py", counting)
        assert (store.misses, store.hits) == (1, 1)


class TestBounded:
    def test_capacity_is_respected(self, counting):
        store = TreeStore(capacity=4)
        for i in range(10):
            store.get(f"def f{i}(): pass\n".encode(), "a.py", counting)
        assert len(store) <= 4

    def test_the_least_recently_used_entry_goes_first(self, counting):
        store = TreeStore(capacity=2)
        store.get(SOURCE, "a.py", counting)
        store.get(OTHER, "a.py", counting)
        store.get(SOURCE, "a.py", counting)          # refresh the first
        store.get(b"def c(): pass\n", "a.py", counting)   # evicts OTHER
        before = len(counting.calls)
        store.get(SOURCE, "a.py", counting)
        assert len(counting.calls) == before, "the refreshed entry should survive"

    def test_a_failed_parse_is_remembered_too(self):
        """Unparseable content should not be retried on every commit."""
        calls = []

        def failing(blob, path):
            calls.append(path)
            return None

        store = TreeStore()
        store.get(b"\x00\x01", "weird.bin", failing)
        store.get(b"\x00\x01", "weird.bin", failing)
        assert len(calls) == 1

    def test_clear_empties_it(self, counting):
        store = TreeStore()
        store.get(SOURCE, "a.py", counting)
        store.clear()
        assert len(store) == 0 and store.hits == 0
