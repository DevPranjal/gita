"""Where does index build time actually go?

Splits a history walk into three phases -- enumerate, read blobs, parse -- so the
build design targets the phase that dominates rather than the one that is easiest
to optimise.
"""
import subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gita.entities.extractor import extract_path  # noqa: E402
from gita.vcs.git import Repo  # noqa: E402

REPO = Path("spikes/attribution/corpus/flask")
N = 300
SUPPORTED = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".md", ".yaml", ".yml",
             ".json", ".toml", ".sql"}
repo = Repo(REPO)

# --- phase A: enumerate (commit, path, blob) ------------------------------
t0 = time.perf_counter()
out = subprocess.run(
    ["git", "log", "--first-parent", "--raw", "--no-renames", "-n", str(N),
     "--format=%x00%H", "--no-abbrev"],
    cwd=REPO, capture_output=True, text=True, errors="replace").stdout
work: list[tuple[str, str]] = []
commits = 0
for line in out.splitlines():
    if line.startswith("\x00"):
        commits += 1
    elif line.startswith(":"):
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if Path(path).suffix.lower() in SUPPORTED and len(parts) >= 5 \
                and not parts[4].startswith("D") and set(parts[3]) != {"0"}:
            work.append((parts[3], path))
enumerate_s = time.perf_counter() - t0
distinct = list(dict.fromkeys(work))

# --- phase B: read blobs --------------------------------------------------
# One process per blob, sampled: spawning 900 processes on Windows is the point
# of the measurement, not something to sit through.
SAMPLE = 60
t0 = time.perf_counter()
for sha, _ in distinct[:SAMPLE]:
    subprocess.run(["git", "cat-file", "blob", sha], cwd=REPO, capture_output=True)
read_naive_s = (time.perf_counter() - t0) / SAMPLE * len(distinct)

# Interleaved write/read: writing every request before reading any response
# deadlocks once git fills the 64 KB stdout pipe.
t0 = time.perf_counter()
proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=REPO,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
blobs = []
total_bytes = 0
for sha, path in distinct:
    proc.stdin.write(f"{sha}\n".encode())
    proc.stdin.flush()
    header = proc.stdout.readline().split()
    size = int(header[2])
    data = proc.stdout.read(size)
    proc.stdout.read(1)
    total_bytes += size
    blobs.append((data, path))
proc.stdin.close()
proc.wait()
read_batch_s = time.perf_counter() - t0

# --- phase C: parse -------------------------------------------------------
t0 = time.perf_counter()
entities = 0
for data, path in blobs:
    try:
        tree = extract_path(data, path)
        entities += len(tree.entities) if tree else 0
    except Exception:
        pass
parse_s = time.perf_counter() - t0

mb = total_bytes / 1e6
print(f"flask, {commits} commits, {len(distinct)} distinct blobs, {mb:.1f} MB, "
      f"{entities} entities\n")
print(f"  A enumerate            {enumerate_s:6.2f}s   {enumerate_s/commits*1000:6.1f} ms/commit")
print(f"  B read (process each)  {read_naive_s:6.2f}s   {read_naive_s/commits*1000:6.1f} ms/commit"
      f"   (extrapolated from {SAMPLE})")
print(f"  B read (cat-file batch){read_batch_s:6.2f}s   {read_batch_s/commits*1000:6.1f} ms/commit"
      f"   {read_naive_s/max(read_batch_s,1e-9):.0f}x faster")
print(f"  C parse                {parse_s:6.2f}s   {parse_s/commits*1000:6.1f} ms/commit"
      f"   {mb/parse_s:.1f} MB/s")

best = enumerate_s + read_batch_s + parse_s
print(f"\n  best sequential total  {best:6.2f}s   {best/commits*1000:6.1f} ms/commit")
print(f"  parse is {parse_s/best:.0%} of it -> {'parallelise parsing' if parse_s/best > .5 else 'I/O bound'}")
print(f"\n  projected full index of 10,000 commits: {best/commits*10000/60:.1f} min sequential")
print(f"  with 8-way parallel parse:               "
      f"{(enumerate_s + read_batch_s + parse_s/8)/commits*10000/60:.1f} min")
