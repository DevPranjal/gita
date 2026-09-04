"""Baseline for WS-1: what does the real history path actually spend?

The design document's 79.5 ms/commit came from a loop calling diff_revisions
directly. `series()` already routes through `repo.walk()`, so the honest
question is narrower: how many git processes does entity_history spawn, and
which of them are the per-merge fallback?
"""
import subprocess, sys, time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gita.history import entity_history, series  # noqa: E402
from gita.vcs.git import Repo  # noqa: E402

REPO = Path("spikes/attribution/corpus/flask")
LIMIT = 100

calls: list[tuple[str, ...]] = []
original = Repo._run


def recording(self, *args, check=True):
    calls.append(args)
    return original(self, *args, check=check)


Repo._run = recording
repo = Repo(REPO)

merges = len(subprocess.run(["git", "rev-list", "--merges", "-n", str(LIMIT), "HEAD"],
                            cwd=REPO, capture_output=True, text=True).stdout.split())
print(f"flask, limit={LIMIT}: {merges} of the last {LIMIT} commits are merges\n")

def history_of(name):
    """Resolve past an ambiguous bare name -- not what this probe is measuring."""
    from gita.context.resolve import Ambiguous
    try:
        return entity_history(repo, name, limit=LIMIT)
    except Ambiguous as exc:
        return entity_history(repo, sorted(exc.matches)[0], limit=LIMIT)


for label, fn in (("series(batched=True)", lambda: series(repo, limit=LIMIT)),
                  ("entity_history", lambda: history_of("stream_with_context"))):
    calls.clear()
    t0 = time.perf_counter()
    result = fn()
    el = time.perf_counter() - t0
    kinds = Counter(a[0] for a in calls)
    print(f"{label:<28} {el:6.2f}s  {len(calls):>4} git processes  "
          f"{len(result):>3} results")
    for k, n in kinds.most_common():
        note = "  <- per-merge fallback in walk()" if k == "diff" else ""
        print(f"    {k:<12}{n:>5}{note}")
    print()

Repo._run = original
