"""Tests for similarity-based rename detection in gita.symdiff.

These cover the v0-pairing failure modes:

* rename + body edit in one commit (was only matchable when bodies were
  byte-identical),
* swap-like edits where two functions both change but one is clearly the
  renamed counterpart of an old one,
* false positives — small unrelated functions should NOT pair up.
"""

from __future__ import annotations

from gita.symdiff import diff_sources


BASE = """\
def get_user(uid):
    row = db.lookup('users', uid)
    if row is None:
        return None
    return User.from_row(row)
"""

RENAME_PLUS_EDIT = """\
def fetch_user(uid):
    row = db.lookup('users', uid)
    if row is None:
        return None
    user = User.from_row(row)
    return user
"""


def _ops(entry: dict) -> list[dict]:
    return entry["ops"]


def test_rename_plus_body_edit_detected_as_single_rename() -> None:
    entry = diff_sources(BASE, RENAME_PLUS_EDIT, path="users.py")
    kinds = [op["op"] for op in _ops(entry)]
    assert "rename_symbol" in kinds, kinds
    # No spurious add/remove for the same logical symbol.
    assert "add_symbol" not in kinds
    assert "remove_symbol" not in kinds
    rename = next(op for op in _ops(entry) if op["op"] == "rename_symbol")
    assert rename["from"] == "get_user"
    assert rename["to"] == "fetch_user"


def test_pure_rename_still_detected() -> None:
    only_rename = BASE.replace("get_user", "fetch_user")
    entry = diff_sources(BASE, only_rename, path="users.py")
    kinds = [op["op"] for op in _ops(entry)]
    assert "rename_symbol" in kinds
    assert "modify_body" not in kinds


def test_unrelated_small_functions_do_not_pair() -> None:
    """Two tiny, semantically different functions should not be matched."""
    a = "def alpha():\n    return 1\n"
    b = "def beta():\n    raise SystemExit('totally different body')\n"
    entry = diff_sources(a, b, path="m.py")
    kinds = [op["op"] for op in _ops(entry)]
    assert "rename_symbol" not in kinds
    assert "add_symbol" in kinds and "remove_symbol" in kinds


def test_best_score_wins_when_two_candidates() -> None:
    """If old `foo` is more similar to new `bar` than to new `baz`, pair foo↔bar."""
    old = (
        "def foo(x):\n"
        "    if x is None:\n"
        "        return 0\n"
        "    return x * x + 7\n"
    )
    new = (
        "def bar(x):\n"   # near-identical body — strong candidate
        "    if x is None:\n"
        "        return 0\n"
        "    return x * x + 7\n"
        "\n"
        "def baz():\n"    # totally unrelated new symbol
        "    return None\n"
    )
    entry = diff_sources(old, new, path="m.py")
    renames = [op for op in _ops(entry) if op["op"] == "rename_symbol"]
    assert len(renames) == 1
    assert renames[0]["from"] == "foo"
    assert renames[0]["to"] == "bar"
    adds = [op["name"] for op in _ops(entry) if op["op"] == "add_symbol"]
    assert adds == ["baz"]


def test_class_and_function_with_same_body_do_not_pair() -> None:
    """Renames must preserve kind (function ↔ function, class ↔ class)."""
    old = "def foo():\n    return 1\n"
    new = "class foo:\n    pass\n"
    entry = diff_sources(old, new, path="m.py")
    kinds = [op["op"] for op in _ops(entry)]
    assert "rename_symbol" not in kinds


def test_unparseable_side_falls_back_gracefully() -> None:
    entry = diff_sources("def ok():\n    pass\n", "def broken(:\n", path="m.py")
    assert entry["status"] == "modified"
    assert entry["ops"] == [{"op": "format_only", "detail": "unparseable on one side"}]
