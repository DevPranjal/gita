"""Offline conformance against downloaded, exact pinned official TS sources.

These tests make no model calls. Set REVIEWBENCH_FROZEN_ROOT after provision.
Absent provision is a named skip, never a scientific scorer fallback.
"""
import os
from pathlib import Path

import pytest

from gita.eval.reviewbench.judge import official, resolve


@pytest.fixture
def frozen_root():
    value = os.environ.get("REVIEWBENCH_FROZEN_ROOT")
    if not value:
        pytest.skip("Pinned official scorer fixture not provisioned; set REVIEWBENCH_FROZEN_ROOT")
    return Path(value)


@pytest.mark.parametrize("correspondences,count", [
    ([], 0),
    ([{"candidate_index": 0, "matched_golden_indices": [0]}], 1),
    ([{"candidate_index": 0, "matched_golden_indices": [1, 0]},
      {"candidate_index": 1, "matched_golden_indices": [0]},
      {"candidate_index": 2, "matched_golden_indices": [1]}], 3),
    ([{"candidate_index": 0, "matched_golden_indices": [1]},
      {"candidate_index": 1, "matched_golden_indices": [1]}], 2),
    ([{"candidate_index": 0, "matched_golden_indices": []}], 1),
])
def test_pinned_resolver_conformance(frozen_root, correspondences, count):
    rows = [{"golden": [{}, {}], "correspondences": correspondences, "candidate_count": count}]
    native = official(frozen_root, rows, resolve_only=True)[0]
    local = resolve(correspondences, 2, count)
    assert dict(native["matched"]) == local["matched"]
    assert set(native["covered"]) == local["covered"]
    assert set(native["unmatched"]) == local["unmatched"]


def test_official_ratios_nulls_strata_novel_and_known_fp(frozen_root):
    golden = [{"file": "a.py", "start_line": 1, "end_line": 1, "message": "issue",
               "tp_fp": "tp", "severity": "high", "category": "bug"},
              {"file": "a.py", "start_line": 2, "end_line": 2, "message": "incorrect",
               "tp_fp": "fp", "severity": "low", "category": "readability"}]
    rows = [{"pr_key": "test", "golden": golden, "candidate_count": 3,
             "correspondences": [{"candidate_index": 0, "matched_golden_indices": [0]},
                                 {"candidate_index": 1, "matched_golden_indices": [1]},
                                 {"candidate_index": 2, "matched_golden_indices": []}],
             "classifications": [{"candidate_index": 2, "tp_fp": "tp",
                                  "severity": "medium", "category": "bug"}]}]
    score = official(frozen_root, rows)["per_pr"][0]["metrics"]
    assert score["overall"]["grounded_recall"] == 1
    assert score["overall"]["grounded_precision"] == 0.5
    assert score["overall"]["augmented_precision"] == pytest.approx(2 / 3)
    assert score["overall"]["augmented_recall"] == 1
    assert score["overall"]["novel_tp_count"] == 1
    empty = official(frozen_root, [{"pr_key": "empty", "golden": [],
                                   "candidate_count": 0, "correspondences": [],
                                   "classifications": []}])
    assert empty["per_pr"][0]["metrics"]["overall"]["grounded_recall"] is None
    assert empty["per_pr"][0]["metrics"]["overall"]["augmented_precision"] is None


def test_self_consistent_wrong_pinned_source_is_rejected(frozen_root, tmp_path):
    from gita.eval.reviewbench.common import ProtocolError, digest, read_json, write_json
    from gita.eval.reviewbench.dataset import verify
    frozen = read_json(frozen_root / "freeze.json")
    frozen["sources"]["scripts/eval/scorer.ts"] = "f" * 64
    frozen["digest"] = digest({k: v for k, v in frozen.items() if k != "digest"})
    write_json(tmp_path / "freeze.json", frozen)
    with pytest.raises(ProtocolError, match="source identities"):
        verify(tmp_path)
