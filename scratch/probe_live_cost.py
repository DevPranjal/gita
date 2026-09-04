"""Where does the *live* path lose its time, given blob reads are already batched?

Times diff_revisions end to end against its enumeration step alone, and against
bulk enumeration for the same commits.
"""
import subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gita.revisions import diff_revisions  # noqa: E402
from gita.vcs.git import Repo  # noqa: E402

REPO = Path("spikes/attribution/corpus/flask")
N = 100
repo = Repo(REPO)
shas = subprocess.run(["git", "rev-list", "--first-parent", "-n", str(N + 1), "HEAD"],
                      cwd=REPO, capture_output=True, text=True).stdout.split()
pairs = [(shas[i + 1], shas[i]) for i in range(N)]

t0 = time.perf_counter()
for base, head in pairs:
    repo.changed_files(base, head, supported_only=False)
enum_s = time.perf_counter() - t0

t0 = time.perf_counter()
subprocess.run(["git", "log", "--first-parent", "--raw", "--no-renames",
                "-n", str(N), "--format=%x00%H", "--no-abbrev"],
               cwd=REPO, capture_output=True, text=True)
bulk_s = time.perf_counter() - t0

t0 = time.perf_counter()
total_changes = 0
for base, head in pairs:
    total_changes += len(diff_revisions(repo, base, head).changes)
full_s = time.perf_counter() - t0

print(f"flask, {N} first-parent commit pairs, {total_changes} changes\n")
print(f"  diff_revisions, end to end     {full_s:6.2f}s   {full_s/N*1000:6.1f} ms/commit")
print(f"  of which: changed_files enum   {enum_s:6.2f}s   {enum_s/N*1000:6.1f} ms/commit"
      f"   ({enum_s/full_s:.0%} of total)")
print(f"  bulk enumeration, all at once  {bulk_s:6.2f}s   {bulk_s/N*1000:6.1f} ms/commit"
      f"   {enum_s/max(bulk_s,1e-9):.0f}x faster")
print(f"\n  remainder (read + parse + diff){full_s-enum_s:6.2f}s   {(full_s-enum_s)/N*1000:6.1f} ms/commit")
print(f"  ceiling if enumeration were free: {(full_s-enum_s)/full_s:.0%} of current cost")
