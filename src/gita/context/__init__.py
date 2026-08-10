"""WS-4 — context layers: L0 headline, L1 rolled entity view, L2 hunks on demand."""

from .cluster import Cluster, cluster_changes, head_of
from .layers import ContextView, build_view, fit_text
from .navigate import expand, intents_of, query_view, relevance, terms_of
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
    "fit_lines",
    "fit_text",
    "head_of",
    "intents_of",
    "is_test_path",
    "query_view",
    "relevance",
    "rollup_lines",
    "score_change",
    "terms_of",
    "token_method",
]
