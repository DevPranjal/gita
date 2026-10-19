"""Score unchanged retained findings without silently relabeling or rejudging them."""
from __future__ import annotations

from copy import deepcopy

from .common import require
from .judge import resolve


def retained_scoring_input(original: dict, kept_indices: list[int]) -> dict:
    count = original["candidate_count"]
    require(type(kept_indices) is list and all(type(i) is int and 0 <= i < count for i in kept_indices)
            and len(kept_indices) == len(set(kept_indices)), "Invalid retained finding indices")
    mapping = {old: new for new, old in enumerate(sorted(kept_indices))}
    matches = [{**deepcopy(row), "candidate_index": mapping[row["candidate_index"]]}
               for row in original["correspondences"] if row["candidate_index"] in mapping]
    matches.sort(key=lambda row: row["candidate_index"])
    unmatched = resolve(matches, len(original["golden"]), len(mapping))["unmatched"]
    classes = {mapping[row["candidate_index"]]: deepcopy(row)
               for row in original["classifications"] if row["candidate_index"] in mapping}
    require(unmatched <= classes.keys(),
            "Retained matching changed classification needs; additional source-supported judgment required")
    return {"pr_key": original["pr_key"], "golden": deepcopy(original["golden"]),
            "candidate_count": len(mapping), "correspondences": matches,
            "classifications": [{**classes[index], "candidate_index": index}
                                for index in sorted(unmatched)]}
