"""Independent verification of registered checkpoints.

The point of this script is to be able to *fail*. It does not re-run gita's own
test suite -- a bad test would pass its own assertions -- it re-derives each
claim from scratch and, wherever possible, checks gita against git as an
independent oracle.

    python verify/check.py              registered checkpoints and Year 1 target
    python verify/check.py --stage ws1  one workstream
    python verify/check.py --stage f1-evidence  evidence cache checkpoint only
    python verify/check.py -v           show the commands being run

Unverified stages are listed, never counted as successful checkpoints.
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
        return bool(self.key in RUNNERS
                    and (self.module is None or _module_exists(self.module)))


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


def check_f1_evidence() -> list[Result]:
    """Check only the evidence cache, retaining all fixtures for inspection."""
    import ast
    from hashlib import blake2b

    results = []
    root = None
    label = "evidence cache setup"
    try:
        root = Path(tempfile.mkdtemp(prefix="gita-verify-f1-")).resolve()
        from gita.index.evidence import parse_evidence
        from gita.index.model import ParseStatus
        from gita.index.store import IndexStore, IndexStoreError

        source = b'# byte prefix: \xc3\xa9\r\ndef sample():\r\n    return "a  b"\r\n'
        sources = {
            "source.py": source,
            "shifted.py": b"# inserted prefix\r\n" + source,
            "literal.py": source.replace(b'"a  b"', b'"a b"'),
            "invalid.py": b"def sample(:\n    return 1\n",
        }
        bundles = []
        for name, raw in sources.items():
            fixture = root / name
            fixture.write_bytes(raw)
            bundles.append(parse_evidence(fixture.read_bytes(), name.encode("ascii")))
        original, shifted, literal, invalid = bundles

        label = "original-byte spans and digests"
        mismatches = 0
        compared = 0
        for raw, bundle in zip(list(sources.values())[:3], bundles[:3]):
            node = ast.parse(raw).body[0]
            if not isinstance(node, ast.FunctionDef) or node.end_lineno is None \
                    or node.end_col_offset is None:
                raise ValueError("fixture must contain a function with AST end positions")
            offsets = [0]
            for line in raw.splitlines(keepends=True):
                offsets.append(offsets[-1] + len(line))
            start = offsets[node.lineno - 1] + node.col_offset
            end = offsets[node.end_lineno - 1] + node.end_col_offset
            definitions = [definition for definition in bundle.result.definitions
                           if not definition.synthetic_root]
            if not bundle.result.complete or len(definitions) != 1:
                mismatches += 1
                continue
            definition = definitions[0]
            version = next((version for version in bundle.versions
                            if version.id == definition.version_id), None)
            span = definition.span
            compared += 1
            if (definition.names != (node.name,)
                    or (span.start_byte, span.end_byte, span.start_line, span.end_line)
                    != (start, end, node.lineno, node.end_lineno)
                    or version is None
                    or version.source_digest != blake2b(raw[start:end], digest_size=32).digest()):
                mismatches += 1
        results.append(Result(
            label, f"{compared} spans, {mismatches} mismatched",
            PASS if compared == 3 and mismatches == 0 else FAIL,
            f"Independent Python AST and BLAKE2b-256; fixtures retained at {root}",
        ))

        label = "parse cache round trip"
        database = root / "evidence.sqlite"
        with IndexStore.create(database) as writer:
            abort = RuntimeError("intentional evidence rollback")
            try:
                with writer.transaction():
                    writer.put_parse(literal.result, literal.versions)
                    visible = writer.get_parse(literal.result.id) == literal.result
                    raise abort
            except RuntimeError as error:
                if error is not abort:
                    raise
            rolled_back = (visible and not writer.connection.in_transaction
                           and writer.get_parse(literal.result.id) is None
                           and all(writer.get_version(version.id) is None
                                   for version in literal.versions))
            with writer.transaction():
                for bundle in bundles:
                    writer.put_parse(bundle.result, bundle.versions)
        with IndexStore.open(database) as reader:
            with reader.snapshot():
                restored = [reader.get_parse(bundle.result.id) for bundle in bundles]
                same_versions = all(
                    reader.get_version(version.id) == version
                    for bundle in bundles for version in bundle.versions
                )
                round_trip = (restored == [bundle.result for bundle in bundles]
                              and same_versions and reader.check_integrity() == ())
            read_only_rejected = False
            try:
                with reader.transaction():
                    pass
            except IndexStoreError as error:
                if error.code != "read_only":
                    raise
                read_only_rejected = True
        results.append(Result(
            label, f"{len(bundles)} bundles reopened", PASS if round_trip else FAIL,
            f"Parse records and full versions compared; evidence cache only: {database}",
        ))

        label = "location and literal evidence"
        definitions = [next(definition for definition in bundle.result.definitions
                            if not definition.synthetic_root)
                       for bundle in (original, shifted, literal)]
        versions = [next(version for version in bundle.versions
                         if version.id == definition.version_id)
                    for bundle, definition in zip((original, shifted, literal), definitions)]
        same_leaf = versions[0] == versions[1]
        moved_span = (definitions[0].id != definitions[1].id
                      and definitions[0].span.start_byte != definitions[1].span.start_byte
                      and definitions[0].span.start_line != definitions[1].span.start_line)
        changed_literal = (versions[0].id != versions[2].id
                           and versions[0].body is not None and versions[2].body is not None
                           and versions[0].body != versions[2].body)
        results.append(Result(
            label, f"shift={same_leaf and moved_span}, literal={changed_literal}",
            PASS if same_leaf and moved_span and changed_literal else FAIL,
            "Prefix insertion preserves the leaf version; literal spaces change normalized body.",
        ))

        label = "syntax errors remain unknown"
        try:
            ast.parse(sources["invalid.py"])
        except SyntaxError:
            syntax_invalid = True
        else:
            syntax_invalid = False
        restored_invalid = restored[-1]
        unknown = (syntax_invalid and restored_invalid is not None
                   and restored_invalid == invalid.result
                   and restored_invalid.status == ParseStatus.PARSE_ERROR
                   and not restored_invalid.complete and not restored_invalid.definitions
                   and bool(restored_invalid.diagnostics) and not invalid.versions)
        results.append(Result(
            label, "syntax-error evidence reopened", PASS if unknown else FAIL,
            "Incomplete evidence must retain diagnostics and no authoritative definitions.",
        ))

        label = "rollback and read-only guards"
        results.append(Result(
            label, f"rollback={rolled_back}, read-only={read_only_rejected}",
            PASS if rolled_back and read_only_rejected else FAIL,
            "Aborted parse and versions absent; reopened reader rejects a write transaction.",
        ))
    except Exception as error:
        note = f"{type(error).__name__}: {error}"
        if root is not None:
            note += f"; fixtures retained at {root}"
        results.append(Result(label, "checkpoint could not finish", FAIL, note))
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
    Stage("f1-evidence", "Evidence cache checkpoint", "gita.index.evidence"),
    Stage("ws2", "Store", "gita.index.store"),
    Stage("ws3", "Builder", "gita.index.builder"),
    Stage("ws4", "Query and CLI", "gita.index.query"),
    Stage("ws5", "Verification", "gita.index.verify"),
]

RUNNERS = {"ws1": check_ws1, "f1-evidence": check_f1_evidence}


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
    parser.add_argument("--stage", choices=[stage.key for stage in STAGES] + ["year1"],
                        help="only this checkpoint or the year1 target")
    parser.add_argument("--demo", action="store_true",
                        help="build a repository to explore by hand")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    if args.demo:
        return demo()

    print("\ngita - stage verification")
    print("=" * 72)
    print("Every check below is re-derived from scratch. Where gita and git can")
    print("answer the same question, git is treated as the oracle.\n")

    failures = 0
    unverified = 0
    completed = 0
    for stage in STAGES:
        if args.stage and stage.key != args.stage:
            continue
        header = f"{stage.key.upper():<13}{stage.title}"
        if not stage.shipped:
            available = stage.module is None or _module_exists(stage.module)
            status = "NOT VERIFIED" if available else "NOT BUILT"
            print(f"{header:<52}{status:>20}")
            print("  Unverified: no runner or required module unavailable.")
            unverified += 1
            if args.stage:
                return 2
            continue
        print(f"{header:<52}{'CHECKPOINT':>20}")
        try:
            results = RUNNERS[stage.key]()
            if not results:
                results = [Result("no checks", "runner returned no checks", FAIL)]
        except Exception as error:
            results = [Result("runner exception", type(error).__name__, FAIL, str(error))]
        completed += 1
        for result in results:
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
        print(f"{failures} check(s) FAILED - a checkpoint claim does not hold.")
    elif unverified and completed:
        print("Completed registered checkpoint checks passed; unverified stages remain.")
    elif unverified:
        print("No registered checkpoint checks ran; unverified stages remain.")
    elif args.stage == "year1":
        print("Year 1 target checks completed.")
    else:
        print("Completed registered checkpoint checks passed.")
    print("\nTo verify the test suite independently:")
    print("  .venv\\Scripts\\python.exe -m pytest -q\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
