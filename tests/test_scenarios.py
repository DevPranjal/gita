"""End-to-end scenario tests.

Each scenario is a directory under `tests/scenarios/` containing
`base.py`, `ours.py`, `theirs.py`, and `expected.py`. The merge must
produce `expected.py` byte-for-byte AND report zero conflicts.
"""

from __future__ import annotations

from pathlib import Path

import libcst as cst
import pytest

from gitpp.merge import merge_modules

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "scenario",
    [
        "parallel-methods",
        pytest.param(
            "import-reorder-add",
            marks=pytest.mark.xfail(reason="v0.0: import-group set merge not implemented yet"),
        ),
        pytest.param(
            "rename-vs-edit",
            marks=pytest.mark.xfail(
                reason="v0.0: stable IDs + symbol-rename propagation not implemented yet"
            ),
        ),
    ],
)
def test_scenario_merges_to_expected(scenario: str) -> None:
    d = SCENARIOS_DIR / scenario
    base = cst.parse_module(_read(d / "base.py"))
    ours = cst.parse_module(_read(d / "ours.py"))
    theirs = cst.parse_module(_read(d / "theirs.py"))
    expected = _read(d / "expected.py")

    merged, conflicts = merge_modules(base, ours, theirs)

    assert conflicts == [], f"unexpected conflicts: {conflicts}"
    assert merged.code == expected, (
        f"\n--- expected ---\n{expected}\n--- got ---\n{merged.code}\n"
    )
