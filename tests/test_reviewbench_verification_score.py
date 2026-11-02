from copy import deepcopy

import pytest

from gita.eval.reviewbench.common import ProtocolError
from gita.eval.reviewbench.verification_score import retained_scoring_input


@pytest.fixture
def scored():
    return {"pr_key": "task", "golden": [{"tp_fp": "tp"}], "candidate_count": 3,
            "correspondences": [
                {"candidate_index": 0, "matched_golden_indices": [0]},
                {"candidate_index": 1, "matched_golden_indices": [0]},
                {"candidate_index": 2, "matched_golden_indices": []}],
            "classifications": [
                {"candidate_index": 1, "tp_fp": "tp", "severity": "medium", "category": "bug"},
                {"candidate_index": 2, "tp_fp": "fp", "severity": "low", "category": "bug"}]}


def test_retained_scoring_re_resolves_duplicate_claims_without_new_labels(scored):
    before = deepcopy(scored)
    filtered = retained_scoring_input(scored, [1, 2])
    assert filtered["candidate_count"] == 2
    assert filtered["correspondences"][0] == {"candidate_index": 0, "matched_golden_indices": [0]}
    assert filtered["classifications"] == [
        {"candidate_index": 1, "tp_fp": "fp", "severity": "low", "category": "bug"}]
    assert scored == before
    filtered["golden"][0]["tp_fp"] = "fp"
    assert scored == before


def test_empty_delivered_review_is_not_assigned_perfect_precision(scored):
    filtered = retained_scoring_input(scored, [])
    assert filtered["candidate_count"] == 0
    assert filtered["correspondences"] == filtered["classifications"] == []


def test_missing_classification_is_not_defaulted_to_fp(scored):
    scored["classifications"] = []
    with pytest.raises(ProtocolError, match="additional source-supported"):
        retained_scoring_input(scored, [2])


@pytest.mark.parametrize("indices", [[True], [-1], [3], [0, 0], [0.0]])
def test_invalid_indices_do_not_silently_select_different_candidates(scored, indices):
    with pytest.raises(ProtocolError):
        retained_scoring_input(scored, indices)
