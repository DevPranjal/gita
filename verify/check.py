"""Independent verification of what has actually shipped.

The point of this script is to be able to *fail*. It does not re-run gita's own
test suite -- a bad test would pass its own assertions -- it re-derives each
claim from scratch and, wherever possible, checks gita against git as an
independent oracle.

    python verify/check.py              everything that has shipped
    python verify/check.py --stage ws1  one workstream
    python verify/check.py -v           show the commands being run

Exit code is 0 only if every check on a shipped stage passed.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPUS = ROOT / "spikes" / "attribution" / "corpus"
VERBOSE = False

PASS, FAIL, SKIP, EXPECTED = "PASS", "FAIL", "SKIP", "EXPECTED FAIL"


def _module_exists(name: str) -> bool:
    # find_spec raises rather than returning None when the parent is absent.
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@dataclass
class Result:
    label: str
    measured: str
    verdict: str
    note: str = ""


@dataclass
class Stage:
    key: str
    title: str
    module: str | None
    results: list[Result] = field(default_factory=list)

    @property
    def shipped(self) -> bool:
        if self.module is None:
            return True
        return _module_exists(self.module)


def git(*args: str, cwd: Path) -> str:
    if VERBOSE:
        print(f"    $ git {' '.join(args)}", file=sys.stderr)
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True,
                          text=True, errors="replace").stdout


def _scratch(name: str) -> Path:
    path = Path(tempfile.gettempdir()) / name
    shutil.rmtree(path, ignore_errors=True, onerror=_force)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _force(func, path, _):
    import os
    os.chmod(path, 0o700)
    func(path)


# ---------------------------------------------------------------- WS-1 ----

def build_merge_repo() -> Path:
    """A history that is mostly merges, which is what real projects look like."""
    root = _scratch("gita-verify-merges")
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "v@example.com", cwd=root)
    git("config", "user.name", "verify", cwd=root)

    (root / "app.py").write_text("def handle(request):\n    return 1\n")
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", "first", cwd=root)

    for n in range(1, 6):
        git("checkout", "-q", "-b", f"feature-{n}", cwd=root)
        (root / "app.py").write_text(f"def handle(request):\n    return {n + 1}\n")
        (root / f"mod{n}.py").write_text(f"def added_{n}():\n    return {n}\n")
        git("add", "-A", cwd=root)
        git("commit", "-q", "-m", f"feature {n}", cwd=root)
        git("checkout", "-q", "main", cwd=root)
        git("merge", "-q", "--no-ff", "-m", f"merge feature {n}",
            f"feature-{n}", cwd=root)
    return root


def count_git_processes(fn) -> tuple[int, float]:
    from gita.vcs.git import Repo

    calls: list[tuple[str, ...]] = []
    original = Repo._run

    def recording(self, *args, check=True):
        calls.append(args)
        return original(self, *args, check=check)

    Repo._run = recording
    try:
        started = time.perf_counter()
        fn()
        elapsed = time.perf_counter() - started
    finally:
        Repo._run = original
    return len(calls), elapsed


def check_ws1() -> list[Result]:
    from gita.history import series
    from gita.vcs.git import Repo

    root = build_merge_repo()
    repo = Repo(root)
    merges = len(git("rev-list", "--merges", "HEAD", cwd=root).split())
    results = []

    # The negative control: force the old path back on and measure both.
    original = Repo.supports_diff_merges
    Repo.supports_diff_merges = property(lambda self: False)
    try:
        before, before_s = count_git_processes(lambda: series(Repo(root), limit=30))
    finally:
        Repo.supports_diff_merges = original
    after, after_s = count_git_processes(lambda: series(Repo(root), limit=30))

    results.append(Result(
        "merge fallback removed",
        f"{after} git process (old path: {before})",
        PASS if after == 1 and before > after else FAIL,
        f"{merges} merges; old path cost one process each, {before_s:.2f}s -> {after_s:.2f}s",
    ))

    # git is the oracle: whatever git says changed, gita must report identically.
    mismatches, compared = 0, 0
    for record in repo.walk(limit=30):
        expected = repo.changed_files(record.parent, record.sha,
                                      supported_only=False)
        compared += 1
        if {(f.status, f.path) for f in record.files} \
                != {(f.status, f.path) for f in expected}:
            mismatches += 1
    results.append(Result(
        "file lists identical to git",
        f"{compared} commits, {mismatches} mismatched",
        PASS if mismatches == 0 and compared > 0 else FAIL,
        "gita's bulk walk vs `git diff --name-status`, commit by commit",
    ))

    # Same question asked the slow way must give the same answer.
    fast = series(repo, limit=30)
    slow = series(repo, limit=30, batched=False)
    same = ([s.sha for s in fast] == [s.sha for s in slow]
            and all([str(c) for c in a.changes] == [str(c) for c in b.changes]
                    for a, b in zip(fast, slow)))
    results.append(Result(
        "answers unchanged by the speedup",
        f"{len(fast)} commits compared",
        PASS if same else FAIL,
        "bulk walk vs one-git-call-per-commit, entity by entity",
    ))

    # Real history, not a fixture.
    for name in ("flask", "express", "ripgrep"):
        path = CORPUS / name
        if not path.exists():
            results.append(Result(f"real history: {name}", "corpus absent", SKIP))
            continue
        real = Repo(path)
        bad = 0
        for record in real.walk(limit=40):
            expected = real.changed_files(record.parent, record.sha,
                                          supported_only=False)
            if {(f.status, f.path) for f in record.files} \
                    != {(f.status, f.path) for f in expected}:
                bad += 1
        results.append(Result(
            f"real history: {name}",
            f"40 commits, {bad} mismatched",
            PASS if bad == 0 else FAIL,
        ))

    return results


# ------------------------------------------------------- Year 1 target ----

def build_refactor_repo() -> Path:
    """One function: born, edited, renamed, moved, edited. Five commits.

    Ground truth is exact by construction. This is the history Year 1 exists to
    get right, and the one place you can watch the product thesis succeed or
    fail with your own eyes.
    """
    root = _scratch("gita-verify-refactor")
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "v@example.com", cwd=root)
    git("config", "user.name", "verify", cwd=root)

    v1 = 'def validate_token(raw):\n    parts = raw.split(".")\n    return len(parts) == 3\n'
    v2 = ('def validate_token(raw):\n    parts = raw.split(".")\n'
          '    if len(parts) != 3:\n        return False\n    return all(parts)\n')
    v3 = v2.replace("validate_token", "check_token")
    v5 = v3.replace("all(parts)", "all(p.strip() for p in parts)")

    def commit(files: dict[str, str], message: str):
        for name, body in files.items():
            (root / name).write_text(body)
        git("add", "-A", cwd=root)
        git("commit", "-q", "-m", message, cwd=root)

    commit({"auth.py": v1}, "add token validation")
    commit({"auth.py": v2}, "reject empty segments")
    commit({"auth.py": v3}, "rename to check_token")
    commit({"auth.py": "# moved to security.py\n", "security.py": v3},
           "move to security.py")
    commit({"security.py": v5}, "strip segments before checking")
    return root


def check_year1_target() -> list[Result]:
    from gita.history import entity_history
    from gita.vcs.git import Repo

    root = build_refactor_repo()
    found = len(entity_history(Repo(root), "check_token", limit=20))
    git_found = len([ln for ln in git("log", "--follow", "--oneline", "--",
                                      "security.py", cwd=root).splitlines() if ln])

    indexed = _module_exists("gita.index.query")
    verdict = PASS if found == 5 else (FAIL if indexed else EXPECTED)
    return [Result(
        "history of a renamed and moved function",
        f"gita {found} of 5  (git: {git_found} of 5)",
        verdict,
        "the defect Year 1 exists to fix; becomes 5 of 5 when WS-4 lands"
        if verdict == EXPECTED else "",
    )]


# ------------------------------------------------------------------------

STAGES = [
    Stage("ws1", "Enumeration", None),
    Stage("ws2", "Store", "gita.index.store"),
    Stage("ws3", "Builder", "gita.index.builder"),
    Stage("ws4", "Query and CLI", "gita.index.query"),
    Stage("ws5", "Verification", "gita.index.verify"),
]

RUNNERS = {"ws1": check_ws1}


def demo() -> int:
    """Build the ground-truth repository and hand it over to be poked at."""
    root = build_refactor_repo()
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)

    print(f"""
A repository has been built at:

  {root}

Five commits, one function. The ground truth is exact by construction:

  1  add token validation          validate_token born in auth.py
  2  reject empty segments         body edited
  3  rename to check_token         renamed, body identical
  4  move to security.py           moved to another file
  5  strip segments before ...     body edited again

Every one of those five commits touches the same function. Try these:

  cd {root}

  git log --oneline
  git log --follow --oneline -- security.py
      git finds all five, by guessing from file similarity.

  "{python}" -m gita history check_token
      gita finds two. It loses the thread at the rename, because today an
      entity's identity IS its location -- "auth.py::validate_token".
      This is the defect Year 1 exists to fix.

  "{python}" -m gita diff HEAD~3 HEAD~2 --brief
      commit 3, the rename.  gita reports:  check_token  [renamed]

  "{python}" -m gita diff HEAD~2 HEAD~1 --brief
      commit 4, the move.    gita reports:  check_token  [moved]

The last two are the interesting part: the engine detects both, correctly.
It just has nowhere to put the answer, so it recomputes and discards it every
time. When WS-3 and WS-4 land, `gita history check_token` returns five.
""")
    return 0


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", help="only this workstream, e.g. ws1")
    parser.add_argument("--demo", action="store_true",
                        help="build a repository to explore by hand")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    if args.demo:
        return demo()

    print("\ngita — stage verification")
    print("=" * 72)
    print("Every check below is re-derived from scratch. Where gita and git can")
    print("answer the same question, git is treated as the oracle.\n")

    failures = 0
    for stage in STAGES:
        if args.stage and stage.key != args.stage:
            continue
        header = f"{stage.key.upper():<6}{stage.title}"
        if not stage.shipped:
            print(f"{header:<52}{'NOT BUILT YET':>20}")
            continue
        print(f"{header:<52}{'SHIPPED':>20}")
        for result in RUNNERS.get(stage.key, lambda: [])():
            mark = result.verdict
            print(f"  {result.label:<40}{result.measured:<28}{mark}")
            if result.note:
                print(f"    {result.note}")
            if mark == FAIL:
                failures += 1
        print()

    if not args.stage or args.stage == "year1":
        print("\nYear 1 acceptance")
        for result in check_year1_target():
            print(f"  {result.label:<40}{result.measured:<28}{result.verdict}")
            if result.note:
                print(f"    {result.note}")
            if result.verdict == FAIL:
                failures += 1
        print()

    print("=" * 72)
    if failures:
        print(f"{failures} check(s) FAILED — a claim in the specs does not hold.")
    else:
        print("All checks on shipped stages passed.")
    print("\nTo verify the test suite independently:")
    print("  .venv\\Scripts\\python.exe -m pytest -q\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
