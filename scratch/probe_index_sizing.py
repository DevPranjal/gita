"""Sizing data for the entity index.

`git log --raw` hands us the blob sha for every (commit, path) touch, which is
exactly the parse workload. The ratio of distinct blobs to total touches decides
whether a persistent parse cache is worth building.
"""
import subprocess, sys
from collections import Counter
from pathlib import Path

CORPUS = Path("spikes/attribution/corpus")
SUPPORTED = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".md", ".yaml", ".yml",
             ".json", ".toml", ".sql"}

print(f"{'repo':<10}{'commits':>8}{'touches':>9}{'distinct':>9}{'dedup':>7}"
      f"{'parsed':>8}{'files/commit':>13}")

totals = Counter()
for repo in sorted(p for p in CORPUS.iterdir() if p.is_dir()):
    out = subprocess.run(
        ["git", "log", "--first-parent", "--raw", "--no-renames",
         "--format=%x00%H", "--no-abbrev"],
        cwd=repo, capture_output=True, text=True, errors="replace").stdout

    commits = touches = 0
    blobs: set[tuple[str, str]] = set()
    for line in out.splitlines():
        if line.startswith("\x00"):
            commits += 1
        elif line.startswith(":"):
            meta, _, path = line.partition("\t")
            if Path(path).suffix.lower() not in SUPPORTED:
                continue
            parts = meta.split()
            if len(parts) < 5 or parts[4].startswith("D"):
                continue
            touches += 1
            blobs.add((parts[3], path))          # new blob sha, path

    dedup = 1 - len(blobs) / touches if touches else 0
    print(f"{repo.name:<10}{commits:>8}{touches:>9}{len(blobs):>9}{dedup:>6.0%}"
          f"{len(blobs):>8}{touches/max(commits,1):>13.1f}")
    totals["commits"] += commits
    totals["touches"] += touches
    totals["blobs"] += len(blobs)

t, b = totals["touches"], totals["blobs"]
print(f"\n{'ALL':<10}{totals['commits']:>8}{t:>9}{b:>9}{1-b/t:>6.0%}")
print(f"\nparse workload is {b/t:.0%} of naive; a persistent cache removes {1-b/t:.0%}")
print(f"touches per commit: {t/totals['commits']:.1f}")
