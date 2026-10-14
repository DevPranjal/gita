"""WS-4 — context layers: L0 headline, L1 rolled entity view, L2 hunks on demand."""

from importlib import import_module

_EXPORTS = {
    **dict.fromkeys(("Cluster", "cluster_changes", "head_of"), ".cluster"),
    **dict.fromkeys(("ContextView", "build_view", "fit_text"), ".layers"),
    **dict.fromkeys(("expand", "filter_changes", "filtered_view", "focus",
                    "focus_label", "matches", "terms_of"), ".navigate"),
    "entity_diff": ".patch",
    **dict.fromkeys(("Intent", "intent_bonus", "is_test_path", "score_change"), ".rank"),
    **dict.fromkeys(("MAX_DEPTH", "fit_lines", "line_span", "rollup_lines"), ".rollup"),
    **dict.fromkeys(("count_tokens", "token_method"), ".tokens"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(_EXPORTS[name], __name__), name)

__all__ = [
    "MAX_DEPTH",
    "Cluster",
    "ContextView",
    "Intent",
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
    "intent_bonus",
    "is_test_path",
    "line_span",
    "matches",
    "rollup_lines",
    "score_change",
    "terms_of",
    "token_method",
]
