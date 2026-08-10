"""WS-4 — context layers: L0 headline, L1 rolled entity view, L2 hunks on demand."""

from .cluster import Cluster, cluster_changes, head_of
from .layers import ContextView, build_view, fit_text
from .navigate import (
    expand,
    filter_changes,
    filtered_view,
    focus,
    focus_label,
    matches,
    terms_of,
)
from .patch import entity_diff
from .rank import is_test_path, score_change
from .rollup import MAX_DEPTH, fit_lines, rollup_lines
from .tokens import count_tokens, token_method

__all__ = [
    "MAX_DEPTH",
    "Cluster",
    "ContextView",
    "build_view",
    "cluster_changes",
    "count_tokens",
    "entity_diff",
    "expand",
    "filter_changes",
    "filtered_view",
    "fit_lines",
    "fit_text",
    "focus",
    "focus_label",
    "head_of",
    "is_test_path",
    "matches",
    "rollup_lines",
    "score_change",
    "terms_of",
    "token_method",
]
