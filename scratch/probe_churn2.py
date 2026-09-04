"""Validate the churn probe against git's own file-level counts.

Pairs each commit with its real parent (sha^) rather than its neighbour in log
order, which is wrong across merges, and cross-checks the result.
"""
import subprocess, sys, time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gita.revisions import diff_revisions  # noqa: E402
from gita.vcs.git import Repo  # noqa: E402

target = Path("spikes/attribution/corpus/flask")
repo = Repo(target)


def g(*a):
    return subprocess.run(("git",) + a, cwd=target, capture_output=True,
                          text=True).stdout.strip()


shas = g("rev-list", "--first-parent", "-n", "150", "HEAD").splitlines()
print(f"{len(shas)} commits, first-parent walk\n")

probe = "src/flask/sansio/app.py"
touch = len(g("log", "--first-parent", "--format=%H", f"{shas[-1]}..HEAD", "--",
              probe).splitlines())
print(f"git says {probe} is touched by {touch} of these commits")

churn: Counter[str] = Counter()
iface: Counter[str] = Counter()
merges = 0
t0 = time.perf_counter()
for sha in shas:
    parents = g("rev-parse", f"{sha}^@").splitlines()
    if not parents:
        continue
    if len(parents) > 1:
        merges += 1
    try:
        cs = diff_revisions(repo, parents[0], sha)  # first parent: what this commit brought in
    except Exception:
        continue
    for ch in cs.changes:
        if ch.is_noise or ch.entity.is_container:
            continue
        key = f"{ch.entity.path}::{ch.entity.name}"
        churn[key] += 1
        if ch.affects_interface:
            iface[key] += 1
el = time.perf_counter() - t0

print(f"gita says the busiest entity in that file changed "
      f"{max([n for k, n in churn.items() if k.startswith(probe)] or [0])} times")
print(f"({merges} merge commits skipped, {el:.0f}s total, {el/len(shas)*1000:.0f} ms/commit)\n")

print("most-churned entities")
for key, n in churn.most_common(10):
    path, name = key.split("::")
    print(f"  {n:>3}x  {name:<30}{path}" + (f"   [{iface[key]} interface]" if iface[key] else ""))

tot = sum(churn.values())
top = sum(n for _, n in churn.most_common(max(1, len(churn) // 20)))
print(f"\n{len(churn)} entities, {tot} material changes")
print(f"top 5% of entities carry {top/max(tot,1):.0%} of all change")
