"""Does the diff layer detect the rename and the cross-file move, even though
entity_history missed them? Distinguishes 'engine cannot' from 'history does
not ask'."""
import subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "gita-identity-probe"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gita.revisions import diff_revisions  # noqa: E402
from gita.vcs.git import Repo  # noqa: E402

shas = subprocess.run(["git", "log", "--reverse", "--format=%H %s"], cwd=ROOT,
                      capture_output=True, text=True).stdout.strip().splitlines()
repo = Repo(ROOT)

for prev, cur in zip(shas, shas[1:]):
    psha, _ = prev.split(" ", 1)
    csha, subject = cur.split(" ", 1)
    cs = diff_revisions(repo, psha, csha)
    print(f"\n--- {subject} ---")
    for c in cs.changes:
        old, new = c.previous, c.current
        where = ""
        if old is not None and new is not None:
            if old.path != new.path:
                where = f"   {old.path} -> {new.path}"
            elif old.name != new.name:
                where = f"   {old.name} -> {new.name}"
        print(f"  {c.kind.value:<18} {c.entity.name:<20}{where}   id={c.entity.id[:24]}")
