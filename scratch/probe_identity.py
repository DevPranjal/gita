"""Does entity identity survive a rename and a file move, where git's line
tracking does not? Builds a repo with known ground truth and asks both."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(tempfile.gettempdir()) / "gita-identity-probe"


def git(*a, cwd=ROOT):
    return subprocess.run(("git",) + a, cwd=cwd, capture_output=True, text=True).stdout


def commit(files: dict[str, str], msg: str):
    for name, body in files.items():
        (ROOT / name).write_text(body)
    git("add", "-A")
    git("-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "-m", msg)


V1 = 'def validate_token(raw):\n    parts = raw.split(".")\n    return len(parts) == 3\n'
V2 = ('def validate_token(raw):\n    parts = raw.split(".")\n'
      '    if len(parts) != 3:\n        return False\n    return all(parts)\n')
V3 = V2.replace("validate_token", "check_token")          # renamed, body identical
V5 = V3.replace("all(parts)", "all(p.strip() for p in parts)")

def _drop(func, path, _):
    os.chmod(path, 0o700)
    func(path)


shutil.rmtree(ROOT, onerror=_drop)
ROOT.mkdir(parents=True, exist_ok=True)
git("init", "-q")

commit({"auth.py": V1}, "add token validation")
commit({"auth.py": V2}, "reject empty segments")
commit({"auth.py": V3}, "rename to check_token")
commit({"auth.py": "# moved to security.py\n", "security.py": V3}, "move to security.py")
commit({"security.py": V5}, "strip segments before checking")

print("GROUND TRUTH: 5 commits, every one of them touches this single function.")
print("  born as validate_token in auth.py -> body edit -> renamed check_token")
print("  -> moved to security.py -> body edit\n")

print("=== git log --follow -- security.py ===")
out = git("--no-pager", "log", "--follow", "--oneline", "--", "security.py").strip()
print(out or "(nothing)")
print(f"  -> git found {len(out.splitlines())} of 5\n")

print("=== git log -L (line-range tracking, git's strongest tool here) ===")
res = subprocess.run(["git", "--no-pager", "log", "-L", ":check_token:security.py",
                      "--oneline"], cwd=ROOT, capture_output=True, text=True)
found = [ln for ln in res.stdout.splitlines() if ln and not ln.startswith(("diff", "---", "+++", "@@", "+", "-", " "))]
print("\n".join(found) if found else f"(failed: {res.stderr.strip()[:120]})")
print(f"  -> git found {len(found)} of 5\n")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gita.history import entity_history  # noqa: E402
from gita.vcs.git import Repo  # noqa: E402

print("=== gita history check_token ===")
try:
    events = entity_history(Repo(ROOT), "check_token", limit=50)
    for e in events:
        print(f"  {e}")
    print(f"  -> gita found {len(events)} of 5")
except Exception as exc:
    print(f"  raised {type(exc).__name__}: {exc}")
