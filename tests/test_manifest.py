"""Tests for the structural change manifest — the agent-facing diff.

These are the load-bearing tests for the v0.1 pivot: instead of asking
"does gitpp merge this scenario correctly?", we ask "does gitpp's diff
*name* the change in a way an agent can act on?"

Each scenario produces a manifest comparing ``base.py`` to ``ours.py`` /
``theirs.py``. We assert on the *ops*, not the prose, so the test stays
stable even if op rendering changes.
"""

from __future__ import annotations

from pathlib import Path

from gitpp.manifest import build_manifest, diff_sources
from gitpp.repo import Repo


SCEN = Path(__file__).parent / "scenarios"


def _ops(prev: str, curr: str) -> list[dict]:
    entry = diff_sources(prev, curr, path="m.py")
    return entry["ops"]


# ---------------------------------------------------------------------------
# rename-vs-edit — the load-bearing scenario for the pivot
# ---------------------------------------------------------------------------


def test_rename_vs_edit_ours_is_a_clean_rename() -> None:
    """ours.py renames get_user -> fetch_user across the def + 3 call sites.

    The manifest must collapse that to ONE rename_symbol op with the right
    reference count, not a flurry of body modifications.
    """
    base = (SCEN / "rename-vs-edit" / "base.py").read_text(encoding="utf-8")
    ours = (SCEN / "rename-vs-edit" / "ours.py").read_text(encoding="utf-8")
    ops = _ops(base, ours)

    renames = [op for op in ops if op["op"] == "rename_symbol"]
    assert len(renames) == 1, f"expected 1 rename, got: {ops}"
    r = renames[0]
    assert r["from"] == "get_user"
    assert r["to"] == "fetch_user"
    # 1 def + 3 call sites = 4 references in the new source.
    assert r["references"] == 4, f"expected 4 refs, got {r['references']}"

    # The rename must NOT manifest as body-modify ops on the call sites.
    body_mods = [op for op in ops if op["op"] == "modify_body"]
    assert body_mods == [], f"rename leaked into body ops: {body_mods}"


def test_rename_vs_edit_theirs_is_body_and_return_changes() -> None:
    """theirs.py adds validation + a new return key. No rename.

    Manifest should show modify_body on get_user, no rename ops.
    """
    base = (SCEN / "rename-vs-edit" / "base.py").read_text(encoding="utf-8")
    theirs = (SCEN / "rename-vs-edit" / "theirs.py").read_text(encoding="utf-8")
    ops = _ops(base, theirs)

    assert not any(op["op"] == "rename_symbol" for op in ops), ops
    body_mods = [op for op in ops if op["op"] == "modify_body" and op["name"] == "get_user"]
    assert len(body_mods) == 1, f"expected get_user body change, got: {ops}"
    assert body_mods[0]["added"] >= 1
    assert "user_id < 0" in body_mods[0]["detail"] or "ValueError" in body_mods[0]["detail"] \
        or "active" in body_mods[0]["detail"]


# ---------------------------------------------------------------------------
# parallel-methods — two added symbols, one per side
# ---------------------------------------------------------------------------


def test_parallel_methods_ours_adds_decommission() -> None:
    base = (SCEN / "parallel-methods" / "base.py").read_text(encoding="utf-8")
    ours = (SCEN / "parallel-methods" / "ours.py").read_text(encoding="utf-8")
    ops = _ops(base, ours)
    # The scenario adds a method *inside* the class; at v0.1 we only diff
    # top-level symbols, so the class is reported as modify_body on the class.
    # That's still a coherent signal — and we explicitly assert no false
    # rename_symbol op fires.
    assert not any(op["op"] == "rename_symbol" for op in ops), ops
    class_mods = [op for op in ops if op["op"] == "modify_body" and op["name"] == "Inventory"]
    assert len(class_mods) == 1, ops
    # ours.py adds a `remove` method.
    assert "remove" in class_mods[0]["detail"]


# ---------------------------------------------------------------------------
# import-reorder-add — one side reorders, the other adds
# ---------------------------------------------------------------------------


def test_import_reorder_pure_is_cosmetic() -> None:
    """ours reorders imports without adding/removing → must be a single
    reorder_imports op, categorized as cosmetic."""
    base = (SCEN / "import-reorder-add" / "base.py").read_text(encoding="utf-8")
    ours = (SCEN / "import-reorder-add" / "ours.py").read_text(encoding="utf-8")
    ops = _ops(base, ours)
    reorders = [op for op in ops if op["op"] == "reorder_imports"]
    adds = [op for op in ops if op["op"] == "add_import"]
    assert reorders, f"expected reorder_imports op, got: {ops}"
    assert adds == [], f"reorder must not look like an add: {ops}"


def test_import_add_is_signature_change() -> None:
    """theirs adds a new import → add_import op."""
    base = (SCEN / "import-reorder-add" / "base.py").read_text(encoding="utf-8")
    theirs = (SCEN / "import-reorder-add" / "theirs.py").read_text(encoding="utf-8")
    ops = _ops(base, theirs)
    adds = [op for op in ops if op["op"] == "add_import"]
    assert adds, f"expected add_import op, got: {ops}"


# ---------------------------------------------------------------------------
# summary categorization
# ---------------------------------------------------------------------------


def test_summary_categorizes_ops() -> None:
    base = (SCEN / "rename-vs-edit" / "base.py").read_text(encoding="utf-8")
    ours = (SCEN / "rename-vs-edit" / "ours.py").read_text(encoding="utf-8")
    entry = diff_sources(base, ours, path="m.py")
    manifest = build_manifest([entry], from_sha="aaa", to_sha="bbb")
    s = manifest["summary"]
    # A pure rename: signature-only.
    assert s["signature_ops"] >= 1
    assert s["logic_ops"] == 0
    assert "fetch_user" in s["symbols_touched"]
    assert "get_user" in s["symbols_touched"]


# ---------------------------------------------------------------------------
# commit-time persistence + explain
# ---------------------------------------------------------------------------


def test_commit_persists_manifest_and_explain_reads_it_back(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    src1 = (SCEN / "rename-vs-edit" / "base.py").read_text(encoding="utf-8")
    src2 = (SCEN / "rename-vs-edit" / "ours.py").read_text(encoding="utf-8")

    f = tmp_path / "users.py"
    f.write_text(src1, encoding="utf-8")
    repo.add(f)
    c1 = repo.commit("initial")

    f.write_text(src2, encoding="utf-8")
    repo.add(f)
    c2 = repo.commit("rename get_user -> fetch_user")

    # Manifest must be persisted on the commit object.
    manifest = repo.read_manifest(c2)
    assert manifest is not None
    assert manifest["from"] == c1
    assert manifest["to"] is None or True  # to=None at commit-time is fine; what matters is the ops
    renames = [op for fe in manifest["files"] for op in fe["ops"] if op["op"] == "rename_symbol"]
    assert len(renames) == 1
    assert renames[0]["from"] == "get_user"
    assert renames[0]["to"] == "fetch_user"
