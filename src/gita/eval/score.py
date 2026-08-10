"""Scoring: quality first, then cost.

Token reduction on its own is meaningless -- returning nothing would score
perfectly. Every cost figure here is reported next to a recall figure, and the
headline metric divides one by the other.
"""

from __future__ import annotations

from collections import defaultdict


def recall(answer: str, must_mention: list[str]) -> float | None:
    """Fraction of required entities the answer actually named."""
    if not must_mention:
        return None
    haystack = (answer or "").lower()
    hits = sum(1 for token in must_mention if token.lower() in haystack)
    return hits / len(must_mention)


def score_run(answer: str, must_mention: list[str], usage: dict,
              events: list[dict]) -> dict:
    tool_tokens = sum(int(e.get("output_tokens") or 0) for e in events)
    gita_calls = [e for e in events if e.get("arm") == "gita"]
    git_calls = [e for e in events if e.get("arm") == "git"]
    layers = {e.get("layer") for e in gita_calls if e.get("layer")}

    return {
        "recall": recall(answer, must_mention),
        "answer_chars": len(answer or ""),
        "tool_tokens": tool_tokens,
        "gita_tool_tokens": sum(int(e.get("output_tokens") or 0) for e in gita_calls),
        "git_tool_tokens": sum(int(e.get("output_tokens") or 0) for e in git_calls),
        "tool_calls": len(events),
        "gita_calls": len(gita_calls),
        "git_calls": len(git_calls),
        "deepest_layer": max(layers, default=None),
        "used_gita": bool(gita_calls),
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "cached_tokens": int(usage.get("cached_tokens", 0)),
        "peak_prompt_tokens": int(usage.get("peak_prompt_tokens", 0)),
        "turns": int(usage.get("turns", 0)),
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarise_runs(runs: list[dict]) -> dict:
    if not runs:
        return {"by_arm": {}, "reduction": {}, "adoption_rate": None,
                "quality_delta": None, "tasks": []}

    by_arm: dict[str, dict] = {}
    for arm in sorted({r["arm"] for r in runs}):
        rows = [r for r in runs if r["arm"] == arm]
        recalls = [r["recall"] for r in rows if r.get("recall") is not None]
        prompt_total = sum(r.get("prompt_tokens", 0) for r in rows)
        # Cached context is billed at a steep discount, so uncached tokens are the
        # closest proxy for real cost. Raw prompt totals overstate what is paid.
        billed_total = sum(max(0, r.get("prompt_tokens", 0) - r.get("cached_tokens", 0))
                           for r in rows)
        correct = sum(recalls)

        by_arm[arm] = {
            "runs": len(rows),
            "mean_recall": _mean(recalls),
            "mean_prompt_tokens": _mean([r.get("prompt_tokens", 0) for r in rows]),
            "mean_billed_tokens": billed_total / len(rows) if rows else 0,
            "mean_tool_tokens": _mean([r.get("tool_tokens", 0) for r in rows]),
            "mean_turns": _mean([r.get("turns", 0) for r in rows]),
            "total_prompt_tokens": prompt_total,
            "total_billed_tokens": billed_total,
            # cost and quality in one number: cheap wrong answers score badly
            "tokens_per_correct_answer": prompt_total / correct if correct else None,
            "billed_per_correct_answer": billed_total / correct if correct else None,
        }

    # Only tasks present in both arms can be compared.
    totals: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"prompt_tokens": 0, "tool_tokens": 0, "recall": [], "runs": 0})
    for run in runs:
        bucket = totals[(run["task"], run["arm"])]
        bucket["prompt_tokens"] += run.get("prompt_tokens", 0)
        bucket["tool_tokens"] += run.get("tool_tokens", 0)
        bucket["runs"] += 1
        if run.get("recall") is not None:
            bucket["recall"].append(run["recall"])

    tasks = []
    paired = {"git_prompt": 0, "gita_prompt": 0, "git_tool": 0, "gita_tool": 0}
    for task in sorted({t for t, _ in totals}):
        git, gita = totals.get((task, "git")), totals.get((task, "gita"))
        if not git or not gita:
            continue
        paired["git_prompt"] += git["prompt_tokens"]
        paired["gita_prompt"] += gita["prompt_tokens"]
        paired["git_tool"] += git["tool_tokens"]
        paired["gita_tool"] += gita["tool_tokens"]
        tasks.append({
            "task": task,
            "git_prompt_tokens": git["prompt_tokens"],
            "gita_prompt_tokens": gita["prompt_tokens"],
            "git_tool_tokens": git["tool_tokens"],
            "gita_tool_tokens": gita["tool_tokens"],
            "git_recall": _mean(git["recall"]),
            "gita_recall": _mean(gita["recall"]),
            "reduction": (1 - gita["prompt_tokens"] / git["prompt_tokens"])
                         if git["prompt_tokens"] else None,
        })

    gita_runs = [r for r in runs if r["arm"] == "gita"]
    adoption = (sum(1 for r in gita_runs if r.get("used_gita")) / len(gita_runs)
                if gita_runs else None)

    quality_delta = None
    if "gita" in by_arm and "git" in by_arm:
        quality_delta = by_arm["gita"]["mean_recall"] - by_arm["git"]["mean_recall"]

    return {
        "by_arm": by_arm,
        "reduction": {
            "prompt_tokens": (1 - paired["gita_prompt"] / paired["git_prompt"])
                             if paired["git_prompt"] else None,
            "tool_tokens": (1 - paired["gita_tool"] / paired["git_tool"])
                           if paired["git_tool"] else None,
            "billed_tokens": (1 - by_arm["gita"]["total_billed_tokens"]
                              / by_arm["git"]["total_billed_tokens"])
            if by_arm.get("git", {}).get("total_billed_tokens") and "gita" in by_arm
            else None,
        },
        "adoption_rate": adoption,
        "quality_delta": quality_delta,
        "tasks": tasks,
    }
