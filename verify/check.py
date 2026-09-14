"""Independent verification of registered checkpoints.

The point of this script is to be able to *fail*. It does not re-run gita's own
test suite -- a bad test would pass its own assertions -- it re-derives each
claim from scratch and, wherever possible, checks gita against git as an
independent oracle.

    python verify/check.py              registered checkpoints and Year 1 target
    python verify/check.py --stage ws1  one workstream
    python verify/check.py --stage f1-evidence  evidence cache checkpoint only
    python verify/check.py --stage f1-worker  bounded parse worker checkpoint only
    python verify/check.py --stage f1  store and committed evidence checkpoint only
    python verify/check.py --stage f2-events  independent event classification checkpoint only
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


def check_f1_worker() -> list[Result]:
    """Check worker isolation and cache rollback, retaining all fixtures."""
    from hashlib import sha256

    results = []
    root = None
    label = "worker evidence matches direct parse"
    try:
        root = Path(tempfile.mkdtemp(prefix="gita-verify-worker-")).resolve()
        from gita.index.evidence import EvidenceBundle, parse_evidence
        from gita.index.store import IndexStore
        from gita.index.worker import EvidenceWorker, EvidenceWorkerError

        sources = {
            "committed.py": b'def committed(value):\n    return value + 1\n',
            "staged.py": b'def staged(value):\n    return value * 2\n',
            "retry.py": b'def retry(value):\n    return value - 3\n',
        }
        baselines = []
        hashes = {}
        for name, source in sources.items():
            fixture = root / name
            fixture.write_bytes(source)
            hashes[name] = sha256(fixture.read_bytes()).digest()
            baselines.append(parse_evidence(fixture.read_bytes(), name.encode("ascii")))
        actual = []
        with EvidenceWorker() as worker:
            for name in ("committed.py", "staged.py"):
                bundle = worker.parse((root / name).read_bytes(), name.encode("ascii"))
                if type(bundle) is not EvidenceBundle or not bundle.result.complete:
                    raise ValueError("worker did not return a complete EvidenceBundle")
                actual.append(bundle)
        same = (actual == baselines[:2] and all(
            sha256((root / name).read_bytes()).digest() == digest
            for name, digest in hashes.items()
        ))
        results.append(Result(
            label, "2 inputs, one reused worker", PASS if same else FAIL,
            f"Full bundles and source hashes compared; fixtures retained at {root}. "
            "Direct baseline shares the parser: serialization check, not an independent AST oracle.",
        ))

        committed, staged = actual
        expected_retry = baselines[2]
        database = root / "evidence.sqlite"
        label = "cache transaction rollback"
        with IndexStore.create(database) as writer:
            with writer.transaction():
                writer.put_parse(committed.result, committed.versions)
            label = "deadline refusal is terminal"
            timed_out = False
            timeout_result = None
            with EvidenceWorker(timeout_seconds=1e-9) as limited:
                try:
                    with writer.transaction():
                        writer.put_parse(staged.result, staged.versions)
                        staged_visible = writer.get_parse(staged.result.id) == staged.result
                        timeout_result = limited.parse(
                            (root / "retry.py").read_bytes(), b"retry.py",
                        )
                        raise ValueError("immediate deadline unexpectedly returned evidence")
                except EvidenceWorkerError as error:
                    if error.code != "resource_limit":
                        raise
                    timed_out = True
                closed = False
                try:
                    limited.parse((root / "retry.py").read_bytes(), b"retry.py")
                except EvidenceWorkerError as error:
                    if error.code != "worker_closed":
                        raise
                    closed = True
            rolled_back = not writer.connection.in_transaction
        results.append(Result(
            label, f"timeout={timed_out}, closed={closed}",
            PASS if timed_out and timeout_result is None and closed else FAIL,
            "NOTE: Immediate deadline includes startup; not a parse-speed benchmark. "
            "resource_limit returns no partial ParseResult; subsequent parse must report worker_closed.",
        ))

        label = "cache transaction rollback"
        database_hash = sha256(database.read_bytes()).digest()
        committed_ids = {version.id for version in committed.versions}
        with IndexStore.open(database) as reader:
            with reader.snapshot():
                preserved = (reader.get_parse(committed.result.id) == committed.result
                             and all(reader.get_version(version.id) == version
                                     for version in committed.versions))
                absent = all(
                    reader.get_parse(bundle.result.id) is None
                    and all(reader.get_version(version.id) is None
                            for version in bundle.versions if version.id not in committed_ids)
                    for bundle in (staged, expected_retry)
                )
                rollback_ok = (staged_visible and rolled_back and preserved and absent
                               and reader.check_integrity() == ())
        rollback_ok = rollback_ok and sha256(database.read_bytes()).digest() == database_hash
        results.append(Result(
            label, f"kept={preserved}, absent={absent}", PASS if rollback_ok else FAIL,
            f"Committed bundle survives; staged and timed-out evidence absent after reopen: {database}. "
            "Read-only inspection leaves the database hash unchanged.",
        ))

        label = "fresh worker retry and explicit persist"
        with EvidenceWorker() as fresh:
            retried = fresh.parse((root / "retry.py").read_bytes(), b"retry.py")
            if type(retried) is not EvidenceBundle or not retried.result.complete:
                raise ValueError("retry did not return a complete EvidenceBundle")
        retry_same = retried == expected_retry
        with IndexStore.open(database, read_only=False) as writer:
            not_persisted = writer.get_parse(retried.result.id) is None
            with writer.transaction():
                writer.put_parse(retried.result, retried.versions)
        database_hash = sha256(database.read_bytes()).digest()
        with IndexStore.open(database) as reader:
            with reader.snapshot():
                reopened = all(
                    reader.get_parse(bundle.result.id) == bundle.result
                    and all(reader.get_version(version.id) == version for version in bundle.versions)
                    for bundle in (committed, retried)
                ) and reader.get_parse(staged.result.id) is None and reader.check_integrity() == ()
        unchanged = (sha256(database.read_bytes()).digest() == database_hash and all(
            sha256((root / name).read_bytes()).digest() == digest
            for name, digest in hashes.items()
        ))
        results.append(Result(
            label, f"same={retry_same}, reopened={reopened}",
            PASS if retry_same and not_persisted and reopened and unchanged else FAIL,
            "Fresh worker matches the direct bundle including all IDs; only the explicit writer "
            f"persists it. Source and read-only database hashes unchanged; fixtures retained at {root}.",
        ))
    except Exception as error:
        note = f"{type(error).__name__}: {error}"
        if root is not None:
            note += f"; fixtures retained at {root}"
        results.append(Result(label, "checkpoint could not finish", FAIL, note))
    return results


def check_f1_foundation() -> list[Result]:
    """Check F1 storage primitives against retained, committed byte evidence."""
    import os
    from hashlib import blake2b, sha256

    results = []
    root = None
    label = "committed source ignores dirty worktree"
    try:
        root = Path(tempfile.mkdtemp(prefix="gita-verify-foundation-")).resolve()
        repository = root / "repo"
        repository.mkdir()
        from gita.index.evidence import EvidenceBundle, EvidenceLimits
        from gita.index.model import ChangeEvent, EntityOccurrence, IdentityContext, LineageSeed
        from gita.index.store import IndexStore, IndexStoreError
        from gita.index.worker import EvidenceWorker
        from gita.vcs.git import CommittedRepo

        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith("GIT_")}
        environment.update({
            "GIT_AUTHOR_NAME": "verify", "GIT_AUTHOR_EMAIL": "verify@example.com",
            "GIT_COMMITTER_NAME": "verify", "GIT_COMMITTER_EMAIL": "verify@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "", "GIT_NO_LAZY_FETCH": "1",
        })

        def command(*args: str) -> None:
            subprocess.run(
                ("git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                 "-c", f"core.hooksPath={os.devnull}", "-c", "protocol.allow=never", *args),
                cwd=repository, env=environment, check=True, capture_output=True,
            )

        path = b"auth.py"
        function_source = b"def validate_token(raw):\r\n    return bool(raw)"
        source = b"\xef\xbb\xbf# caf\xc3\xa9\r\n" + function_source + b"\r\n"
        dirty = b"def broken(:\n"
        notes = b"Unrelated tracked note.\n"
        command("init", "-q", "-b", "main", "--object-format=sha1")
        command("config", "core.autocrlf", "false")
        command("config", "protocol.allow", "never")
        (repository / "auth.py").write_bytes(source)
        command("add", "--", "auth.py")
        command("commit", "-q", "-m", "root fixture")
        limits = EvidenceLimits(4096, 10, 16384)
        with CommittedRepo(repository, max_object_bytes=limits.max_blob_bytes) as committed:
            root_oid = committed.resolve("HEAD")
            first = committed.commit(root_oid)
            (repository / "notes.txt").write_bytes(notes)
            command("add", "--", "notes.txt")
            command("commit", "-q", "-m", "unrelated note")
            later_oid = committed.resolve("HEAD")
            later = committed.commit(later_oid)
            (repository / "auth.py").write_bytes(dirty)
            pages = tuple(committed.iter_tree(root_oid, page_size=1))
            if len(pages) != 1 or len(pages[0]) != 1:
                raise ValueError("root inventory must contain exactly auth.py")
            entry = pages[0][0]
            blob = committed.read_object(entry.oid)
            if (entry.path != path or entry.object_type != "blob" or entry.mode != 0o100644
                    or blob is None or blob.oid != entry.oid or blob.object_type != "blob"
                    or blob.data != source):
                raise ValueError("committed blob must preserve the exact BOM/UTF-8/CRLF fixture")
            pinned = (committed.common_dir == (repository / ".git").resolve()
                      and committed.resolve("HEAD~1") == root_oid and root_oid != later_oid
                      and first.oid == root_oid and not first.parents
                      and later.oid == later_oid and later.parents == (root_oid,)
                      and (repository / "auth.py").read_bytes() == dirty)
            with EvidenceWorker() as worker:
                bundle = worker.parse(blob.data, path, committed.object_format, limits)
        if (type(bundle) is not EvidenceBundle or not bundle.result.complete
                or bundle.result.blob != entry.oid or bundle.result.path != path
                or len(bundle.result.definitions) != 2):
            raise ValueError("worker must return complete evidence with root and function definitions")
        definitions = tuple(sorted(bundle.result.definitions,
                                   key=lambda definition: not definition.synthetic_root))
        versions = {version.id: version for version in bundle.versions}
        function = definitions[1]
        exact_spans = (
            definitions[0].synthetic_root and not function.synthetic_root
            and function.kind == "function" and function.names == ("validate_token",)
            and (function.span.start_byte, function.span.end_byte)
            == (source.index(function_source), source.index(function_source) + len(function_source))
            and all(versions[definition.version_id].source_digest == blake2b(
                source[definition.span.start_byte:definition.span.end_byte], digest_size=32,
            ).digest() for definition in definitions)
        )
        results.append(Result(
            label, "1 pinned blob, 2 definitions", PASS if pinned and exact_spans else FAIL,
            f"Exact bytes and independent function slice; fixtures retained at {root}. "
            "F1 primitives only; no lineage traversal or indexed CLI.",
        ))

        label = "root publication and read-only reopen"
        database = root / "evidence.sqlite"
        context = IdentityContext(
            root_oid, "root", (), bundle.result.parse_fingerprint,
            sha256(b"manual-storage-policy-v1").digest(),
            (("max_blob_bytes", limits.max_blob_bytes),
             ("max_definitions", limits.max_definitions),
             ("max_representation_bytes", limits.max_representation_bytes)),
        )
        occurrences = tuple(EntityOccurrence(
            root_oid, path, entry.oid, entry.object_type, entry.mode, definition.id,
        ) for definition in definitions)
        seeds = tuple(LineageSeed(context.id, root_oid, occurrence.id, "root")
                      for occurrence in occurrences)
        births = tuple(ChangeEvent(
            context.id, seed.id, None, root_oid, None, occurrence.id, ("added",), None,
        ) for seed, occurrence in zip(seeds, occurrences, strict=True))

        def stored_checkpoint(store: IndexStore) -> tuple:
            return (
                store.published_generation("manual"), store.generation(generation),
                store.members(generation), store.file_states(generation, 0),
                store.get_parse(bundle.result.id),
                tuple(store.get_version(version.id) for version in bundle.versions),
            )

        with IndexStore.create(database) as writer:
            if writer.connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise ValueError("foundation fixture requires schema 2")
            with writer.transaction():
                writer.put_interpretation("parse", context.parse_fingerprint, (("manual_fixture", 1),))
                writer.put_interpretation("match", context.match_fingerprint, (("manual_fixture", 1),))
                for commit in (first, later):
                    writer.put_commit(commit.oid, commit.tree, commit.parents, message=commit.message)
                writer.put_context(context)
                writer.put_parse(bundle.result, bundle.versions)
                for occurrence in occurrences:
                    writer.put_occurrence(occurrence)
                for seed in seeds:
                    writer.put_lineage(seed)
                bindings = writer.put_binding_set(bundle.result.id, tuple(
                    (definition.id, seed.id) for definition, seed in zip(definitions, seeds, strict=True)
                ))
                generation = writer.begin_generation("manual", context, root_oid)
                writer.add_member(generation, 0, root_oid)
                state = writer.put_file_state(
                    generation, path, 0, None, "present", entry.oid, entry.object_type,
                    entry.mode, bundle.result.id, bindings,
                )
                writer.set_frontier(generation, path, state)
                for birth in births:
                    writer.attach_event(generation, 0, writer.put_event(birth))
                writer.put_coverage(generation, 0, path, "extraction", "complete", 0)
                writer.publish_generation(generation)
            original = stored_checkpoint(writer)
        database_hash = sha256(database.read_bytes()).digest()
        state_fields = ("path", "from_seq", "to_seq", "status", "blob", "object_type",
                        "mode", "parse_id", "binding_set")
        with IndexStore.open(database) as reader:
            with reader.snapshot():
                restored = stored_checkpoint(reader)
                record = reader.generation(generation)
                states = reader.file_states(generation, 0)
                reopened = (
                    restored == original and reader.published_generation("manual") == generation
                    and record["state"] == "published" and record["tip"] == root_oid
                    and record["context_id"] == context.id and record["predecessor"] is None
                    and reader.members(generation) == (root_oid,)
                    and len(states) == 1 and tuple(states[0][key] for key in state_fields)
                    == (path, 0, None, "present", entry.oid, "blob", entry.mode, bundle.result.id, bindings)
                    and restored[4] == bundle.result and restored[5] == bundle.versions
                    and reader.check_integrity() == ()
                )
            read_only_rejected = False
            try:
                with reader.transaction():
                    reader.begin_generation("read-only", context, root_oid)
            except IndexStoreError as error:
                if error.code != "read_only":
                    raise
                read_only_rejected = True
        results.append(Result(
            label, f"reopened={reopened}, read-only={read_only_rejected}",
            PASS if reopened and read_only_rejected
            and sha256(database.read_bytes()).digest() == database_hash else FAIL,
            "Full parse, versions, membership and state compared; manual fixture manifests "
            "do not claim a complete matching policy.",
        ))

        label = "copied successor transaction rollback"
        with IndexStore.open(database, read_only=False) as writer:
            abort = RuntimeError("intentional foundation successor rollback")
            try:
                with writer.transaction():
                    successor = writer.begin_generation("manual", context, later_oid,
                                                        predecessor=generation)
                    inherited = writer.file_states(successor, 0)
                    copied = (writer.members(successor) == (root_oid,) and len(inherited) == 1
                              and tuple(inherited[0][key] for key in state_fields)
                              == tuple(states[0][key] for key in state_fields))
                    writer.add_member(successor, 1, later_oid)
                    staged = (writer.generation(successor)["state"] == "staging"
                              and writer.members(successor) == (root_oid, later_oid))
                    raise abort
            except RuntimeError as error:
                if error is not abort:
                    raise
            rolled_back = False
            try:
                writer.generation(successor)
            except IndexStoreError as error:
                if error.code != "generation_gone":
                    raise
                rolled_back = True
            rollback_ok = (copied and staged and rolled_back and not writer.connection.in_transaction
                           and stored_checkpoint(writer) == original)
            results.append(Result(
                label, f"copied={copied}, rolled-back={rolled_back}", PASS if rollback_ok else FAIL,
                "Injected RuntimeError before successor publication; old pointer and evidence unchanged.",
            ))

            label = "published generation GC protection"
            protected = False
            try:
                with writer.transaction():
                    writer.collect_generation(generation)
            except IndexStoreError as error:
                if error.code != "generation_in_use":
                    raise
                protected = True
            results.append(Result(
                label, f"generation_in_use={protected}",
                PASS if protected and stored_checkpoint(writer) == original else FAIL,
                "Explicit collection must refuse the active publication without changing it.",
            ))

            label = "explicit staging GC and final integrity"
            with writer.transaction():
                disposable = writer.begin_generation("manual-disposable", context, root_oid)
            with writer.transaction():
                writer.collect_generation(disposable)
            collected = False
            try:
                writer.generation(disposable)
            except IndexStoreError as error:
                if error.code != "generation_gone":
                    raise
                collected = True
        database_hash = sha256(database.read_bytes()).digest()
        with IndexStore.open(database) as reader:
            with reader.snapshot():
                publications = tuple(tuple(row) for row in reader.connection.execute(
                    "SELECT name, published_generation_id FROM scope "
                    "WHERE published_generation_id IS NOT NULL",
                ))
                intact = (stored_checkpoint(reader) == original and reader.check_integrity() == ()
                          and publications == (("manual", generation),)
                          and reader.connection.execute("SELECT count(*) FROM generation").fetchone()[0] == 1)
        with CommittedRepo(repository) as committed:
            final_blob = committed.read_object(entry.oid)
            source_intact = (final_blob is not None and final_blob.data == source
                             and committed.resolve("HEAD") == later_oid
                             and (repository / "auth.py").read_bytes() == dirty
                             and (repository / "notes.txt").read_bytes() == notes)
        results.append(Result(
            label, f"collected={collected}, intact={intact}",
            PASS if collected and intact and source_intact
            and sha256(database.read_bytes()).digest() == database_hash else FAIL,
            f"Only an unused staging generation collected; files retained at {root}. "
            "Original committed bytes and reopened evidence preserved; no F2 readiness claim.",
        ))
    except Exception as error:
        note = f"{type(error).__name__}: {error}"
        if root is not None:
            note += f"; fixtures retained at {root}"
        results.append(Result(label, "checkpoint could not finish", FAIL, note))
    return results


def check_f2_events() -> list[Result]:
    """Check classification of caller-accepted endpoints using in-memory bytes."""
    results = []
    label = "event classification setup"
    note = "Accepted endpoints are fixtures; no lineage inference or traversal."
    try:
        from dataclasses import replace

        from gita.index import matching
        from gita.index.evidence import parse_evidence
        from gita.index.model import ChangeEvent, EntityOccurrence, GitOid, content_id

        before_commit = GitOid("sha1", b"a" * 20)
        after_commit = GitOid("sha1", b"b" * 20)
        parent_identity = content_id("eid1", ("manual-parent",))

        def records(source, path, names, commit):
            bundle = parse_evidence(source, path)
            if not bundle.result.complete:
                raise ValueError(f"fixture parse incomplete: {bundle.result.status}")
            definition, = (definition for definition in bundle.result.definitions
                           if definition.names == names and not definition.synthetic_root)
            version = next(version for version in bundle.versions
                           if version.id == definition.version_id)
            occurrence = EntityOccurrence(
                commit, path, bundle.result.blob, "blob", 0o100644, definition.id,
            )
            return occurrence, definition, version

        def compare(before, after):
            return matching.classify_transition(
                before[0], after[0], before[1], after[1], before[2], after[2],
                source_parent_identity=parent_identity,
                destination_parent_identity=parent_identity,
            )

        label = "combined rename, move and content edits"
        before = records(b"def original(value):\n    return value\n",
                         b"src/\xff\told.py", ("original",), before_commit)
        after = records(b"def renamed(value, extra):\n    return value + extra\n",
                        b"dst/\xfe\tnew.py", ("renamed",), after_commit)
        flags = compare(before, after)
        expected = ("body_changed", "moved", "renamed", "signature_changed")
        results.append(Result(label, repr(flags), PASS if flags == expected else FAIL, note))

        label = "coordinate-only prefix insertion"
        source = b"def sample():\n    return 1\n"
        before = records(source, b"sample.py", ("sample",), before_commit)
        after = records(b"# inserted prefix\n" + source, b"sample.py", ("sample",), after_commit)
        flags = compare(before, after)
        shifted = (before[1].span != after[1].span
                   and before[1].parse_id != after[1].parse_id
                   and before[2].id == after[2].id)
        results.append(Result(
            label, f"shifted={shifted}, flags={flags!r}",
            PASS if shifted and flags == () else FAIL, note,
        ))

        label = "descendant-only class edit is nonmaterial"
        before = records(b"class Sample:\n    def method(self):\n        return 1\n",
                         b"sample.py", ("Sample",), before_commit)
        after = records(b"class Sample:\n    def method(self):\n        return 2\n",
                        b"sample.py", ("Sample",), after_commit)
        flags = compare(before, after)
        event = ChangeEvent(
            content_id("ctx1", ("manual-context",)),
            content_id("eid1", ("manual-class",)),
            before_commit, after_commit, before[0].id, after[0].id, flags, None,
        ) if flags else None
        nonmaterial = event is not None and event.material is False
        results.append(Result(
            label, f"flags={flags!r}, nonmaterial={nonmaterial}",
            PASS if flags == ("descendant_changed",) and nonmaterial
            and before[2].own is not None and before[2].own == after[2].own
            and before[2].subtree != after[2].subtree else FAIL, note,
        ))

        label = "unavailable leaf representation is not cosmetic"
        before = records(source, b"sample.py", ("sample",), before_commit)
        after = records(b"def sample():\n    return  1\n",
                        b"sample.py", ("sample",), after_commit)
        version = replace(after[2], own=None, signature=None, body=None, subtree=None)
        definition = replace(after[1], version_id=version.id)
        occurrence = replace(after[0], definition_id=definition.id)
        after = occurrence, definition, version
        flags = compare(before, after)
        results.append(Result(
            label, repr(flags),
            PASS if flags == ("representation_unknown",)
            and before[2].source_digest != after[2].source_digest else FAIL, note,
        ))
    except Exception as error:
        results.append(Result(
            label, "checkpoint could not finish", FAIL,
            f"{type(error).__name__}: {error}; {note}",
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
    Stage("f1-evidence", "Evidence cache checkpoint", "gita.index.evidence"),
    Stage("f1-worker", "Bounded parse worker checkpoint", "gita.index.worker"),
    Stage("f1", "Store and committed evidence checkpoint", "gita.index.store"),
    Stage("f2-events", "Independent event classification checkpoint", "gita.index.matching"),
    Stage("ws2", "Store", "gita.index.store"),
    Stage("ws3", "Builder", "gita.index.builder"),
    Stage("ws4", "Query and CLI", "gita.index.query"),
    Stage("ws5", "Verification", "gita.index.verify"),
]

RUNNERS = {"ws1": check_ws1, "f1-evidence": check_f1_evidence, "f1-worker": check_f1_worker,
           "f1": check_f1_foundation, "f2-events": check_f2_events}


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
