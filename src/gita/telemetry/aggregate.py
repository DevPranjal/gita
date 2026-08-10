"""Turning events into the numbers the dashboard is allowed to show.

The paired figure is the honest headline: the same task run in both arms.
Unpaired averages are sensitive to which tasks happened to run where, so they
are reported but never presented as the result.
"""

from __future__ import annotations

from collections import defaultdict

LAYERS = ("L0", "L1", "L2")
_LAYER_RANK = {layer: index for index, layer in enumerate(LAYERS)}

BASELINE_ARM = "git"
TREATMENT_ARM = "gita"


def _reduction(baseline: float, treatment: float) -> float | None:
    if not baseline:
        return None
    return 1 - treatment / baseline


def _arm_stats(events: list[dict]) -> dict[str, dict]:
    per_arm: dict[str, dict] = {}
    for arm in sorted({e.get("arm", "unknown") for e in events}):
        rows = [e for e in events if e.get("arm", "unknown") == arm]
        tokens = sum(int(e.get("output_tokens") or 0) for e in rows)
        sessions = {e.get("session") for e in rows if e.get("session")}
        latencies = [int(e["latency_ms"]) for e in rows if e.get("latency_ms") is not None]

        per_arm[arm] = {
            "calls": len(rows),
            "sessions": len(sessions) or 1,
            "tokens": tokens,
            "avg_tokens_per_call": tokens / len(rows) if rows else 0,
            "avg_tokens_per_session": tokens / (len(sessions) or 1),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        }
    return per_arm


def _drill_depth(events: list[dict]) -> dict[str, float]:
    """How deep agents actually had to go, one vote per session.

    If sessions routinely stop at L0 or L1, progressive disclosure is doing its
    job -- this is the metric no line-diff tool can report.
    """
    deepest: dict[str, str] = {}
    for e in events:
        layer, session = e.get("layer"), e.get("session")
        if layer not in _LAYER_RANK or not session:
            continue
        current = deepest.get(session)
        if current is None or _LAYER_RANK[layer] > _LAYER_RANK[current]:
            deepest[session] = layer

    total = len(deepest)
    if not total:
        return {layer: 0.0 for layer in LAYERS}
    counts = defaultdict(int)
    for layer in deepest.values():
        counts[layer] += 1
    return {layer: counts[layer] / total for layer in LAYERS}


def _paired_tasks(events: list[dict]) -> list[dict]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for e in events:
        task, arm = e.get("task"), e.get("arm")
        if task and arm:
            totals[(task, arm)] += int(e.get("output_tokens") or 0)

    tasks = sorted({task for task, _ in totals})
    rows = []
    for task in tasks:
        baseline = totals.get((task, BASELINE_ARM))
        treatment = totals.get((task, TREATMENT_ARM))
        if baseline is None or treatment is None:
            continue  # only tasks run in both arms can be compared
        rows.append({
            "task": task,
            "baseline_tokens": baseline,
            "treatment_tokens": treatment,
            "reduction": _reduction(baseline, treatment),
        })
    return rows


def summarise(events: list[dict]) -> dict:
    events = [e for e in events if isinstance(e, dict)]
    by_arm = _arm_stats(events)
    tasks = _paired_tasks(events)

    baseline = by_arm.get(BASELINE_ARM)
    treatment = by_arm.get(TREATMENT_ARM)
    both = baseline is not None and treatment is not None

    paired_baseline = sum(t["baseline_tokens"] for t in tasks)
    paired_treatment = sum(t["treatment_tokens"] for t in tasks)

    return {
        "calls": len(events),
        "sessions": len({e.get("session") for e in events if e.get("session")}),
        "errors": sum(1 for e in events if e.get("ok") is False),
        "tokens_note": "tool output injected into context, not total model spend",
        "by_arm": by_arm,
        "savings": {
            "per_call": _reduction(baseline["avg_tokens_per_call"],
                                   treatment["avg_tokens_per_call"]) if both else None,
            "per_session": _reduction(baseline["avg_tokens_per_session"],
                                      treatment["avg_tokens_per_session"]) if both else None,
            "paired": _reduction(paired_baseline, paired_treatment) if tasks else None,
            "tokens_saved": (paired_baseline - paired_treatment) if tasks else None,
        },
        "drill_depth": _drill_depth(events),
        "tasks": tasks,
    }
