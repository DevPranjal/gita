"""Minimal git plumbing -- enough to feed the differ two revisions of a file."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryFile
from threading import Event, Timer
from typing import BinaryIO

from ..entities.languages import is_supported
from ..index.model import GitOid

#: Pseudo-revisions. `git diff HEAD` compares against the working tree, which is
#: the most common thing an agent looks at and used to fail outright.
WORKTREE = None
STAGED = "STAGED"

#: git's canonical empty tree -- the only sane base for a root commit.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def is_pseudo_rev(rev: str | None) -> bool:
    return rev is None or rev == STAGED


@dataclass(frozen=True, slots=True)
class ChangedFile:
    status: str
    path: str
    old_path: str | None = None

    @property
    def is_added(self) -> bool:
        return self.status in ("A", "?")

    @property
    def is_deleted(self) -> bool:
        return self.status == "D"

    @property
    def source_path(self) -> str:
        return self.old_path or self.path


class GitError(RuntimeError):
    pass


#: `--diff-merges` arrived in git 2.31. Below it, merges need an explicit diff.
_DIFF_MERGES_SINCE = (2, 31)
_VERSION = re.compile(r"git version (\d+)\.(\d+)")


def _supports_diff_merges(version_output: str) -> bool:
    found = _VERSION.search(version_output)
    if found is None:
        return False
    return (int(found.group(1)), int(found.group(2))) >= _DIFF_MERGES_SINCE


@lru_cache(maxsize=1)
def _git_version() -> str:
    """Probed once per process: the binary cannot change under us mid-run."""
    try:
        result = subprocess.run(["git", "--version"], capture_output=True,
                                check=False)
    except OSError:
        return ""
    return result.stdout.decode("utf8", "replace")


#: Record separator. Note it is *not* safe to find these with `splitlines()`:
#: Python treats \x1e as a line boundary, so the marker vanishes into the split.
_RECORD_MARK = "\x1e"
_WALK_FORMAT = _RECORD_MARK + "%H%x1f%s%x1f%aI%x1f%P"


@dataclass(slots=True)
class CommitRecord:
    """One commit, its first parent, and what it touched."""

    sha: str
    subject: str
    date: str
    parent: str
    is_merge: bool = False
    files: list[ChangedFile] = field(default_factory=list)


def _changed_file(line: str) -> ChangedFile | None:
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    status = parts[0]
    if status.startswith("R") and len(parts) >= 3:
        return ChangedFile("R", parts[2], parts[1])
    return ChangedFile(status[0], parts[1])


def _readable(args: tuple[str, ...], stderr: bytes) -> str:
    """git's diagnostics are written for a human at a terminal, not for an agent.

    A failed revision returned four lines, three of them advice about `--`, plus
    our own flags -- the exact low-signal noise gita exists to remove.
    """
    lines = [line.strip() for line in
             stderr.decode("utf8", "replace").splitlines() if line.strip()]
    first = lines[0] if lines else ""
    for prefix in ("fatal: ", "error: "):
        if first.startswith(prefix):
            first = first[len(prefix):]
    return first or f"git {args[0]} failed"


class _ObjectReader:
    """One long-lived `git cat-file --batch` per repository.

    Reading blobs with `git show` costs a process spawn each -- ~67ms, which was
    54% of the time spent answering a history query. This is the plumbing command
    built for the job: write a spec, read a header and that many bytes.

    Falls back silently: a missing or broken batch process must cost speed, never
    an answer.
    """

    def __init__(self, root: Path):
        self._root = root
        self._proc: subprocess.Popen | None = None
        self._usable = True

    def _process(self) -> subprocess.Popen | None:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        if not self._usable:
            return None
        try:
            self._proc = subprocess.Popen(
                ["git", "-C", str(self._root), "cat-file", "--batch"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._usable = False
            self._proc = None
        return self._proc

    def read(self, spec: str) -> bytes | None:
        """Blob bytes for ``rev:path``, or None if absent. Raises on unusable."""
        proc = self._process()
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise _Unbatched
        try:
            proc.stdin.write(spec.encode("utf8", "replace") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline()
            if not header:
                raise _Unbatched
            parts = header.decode("utf8", "replace").split()
            if len(parts) < 3 or parts[-2] != "blob":
                return None                      # missing, ambiguous, or a tree
            size = int(parts[-1])
            data = proc.stdout.read(size)
            proc.stdout.read(1)                  # trailing newline
        except (OSError, ValueError):
            self.close()
            raise _Unbatched from None
        return data or None

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass

    def __del__(self) -> None:  # pragma: no cover - interpreter teardown
        self.close()


class _Unbatched(Exception):
    """The batch process cannot serve this read; use a plain command."""


class Repo:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._objects = _ObjectReader(self.root)
        self._diff_merges: bool | None = None

    @property
    def supports_diff_merges(self) -> bool:
        """Whether `git log` can list a merge's files against its first parent.

        Without it every merge costs an extra process, and merges are the common
        case -- flask is 110 of 150 commits.
        """
        if self._diff_merges is None:
            self._diff_merges = _supports_diff_merges(_git_version())
        return self._diff_merges

    def _run(self, *args: str, check: bool = True) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, check=False,
        )
        # A bad flag yields empty stdout, which reads exactly like "no changes".
        if check and result.returncode != 0:
            raise GitError(_readable(args, result.stderr))
        return result.stdout

    def text(self, *args: str, check: bool = True) -> str:
        return self._run(*args, check=check).decode("utf8", "replace")

    def blob(self, rev: str | None, path: str) -> bytes | None:
        # Absent on one side is normal for added and deleted files.
        if rev is WORKTREE:
            candidate = self.root / path
            try:
                return candidate.read_bytes() or None
            except OSError:
                return None
        if rev == STAGED:
            return self._run("show", f":{path}", check=False) or None
        try:
            return self._objects.read(f"{rev}:{path}")
        except _Unbatched:
            return self._run("show", f"{rev}:{path}", check=False) or None

    def walk(self, since: str | None = None, until: str = "HEAD",
             limit: int = 20, paths: list[str] | None = None,
             first_parent: bool = False) -> list["CommitRecord"]:
        """Commits with their first parent and changed files, in one git call.

        Asking per commit cost two processes each. `git log --name-status` gives
        the same information in one pass -- except for merges, which git omits
        unless asked. `--diff-merges=first-parent` asks, which matters because
        merges are the common case: flask is 110 of 150 commits, and each one
        used to cost a process.
        """
        span = [f"{since}..{until}"] if since else [until]
        limit_args = ["--", *paths] if paths else []
        merge_args = ["--diff-merges=first-parent"] if self.supports_diff_merges else []
        walk_args = ["--first-parent"] if first_parent else []
        raw = self.text("log", f"--format={_WALK_FORMAT}", "--name-status",
                        *merge_args, *walk_args,
                        f"-n{limit}", *span, *limit_args, check=False)

        records: list[CommitRecord] = []
        for chunk in raw.split(_RECORD_MARK):
            if not chunk.strip():
                continue
            header, _, body = chunk.partition("\n")
            sha, subject, date, parents = (header.split("\x1f") + ["", "", ""])[:4]
            if not sha:
                continue
            parent_list = parents.split()
            record = CommitRecord(
                sha=sha, subject=subject, date=date,
                parent=parent_list[0] if parent_list else EMPTY_TREE,
                is_merge=len(parent_list) > 1,
            )
            for line in body.splitlines():
                entry = _changed_file(line) if line.strip() else None
                if entry is not None:
                    record.files.append(entry)
            records.append(record)

        if not self.supports_diff_merges:
            for record in records:
                if record.is_merge:
                    record.files = self.changed_files(record.parent, record.sha,
                                                      supported_only=False,
                                                      paths=paths)
        return records

    def resolve(self, rev: str | None) -> str:
        if is_pseudo_rev(rev) or rev == EMPTY_TREE:
            return rev or "WORKTREE"
        # Plain `rev-parse` echoes an unknown argument back on stdout, so its
        # output is truthy even on failure and every guard built on it passed.
        return self.text("rev-parse", "--verify", "--quiet", rev, check=False).strip()

    def merge_base(self, left: str, right: str) -> str:
        base = self.text("merge-base", left, right, check=False).strip()
        if not base:
            raise GitError(f"no merge base between {left} and {right}")
        return base

    def is_repository(self) -> bool:
        return bool(self.text("rev-parse", "--git-dir", check=False).strip())

    def parent(self, rev: str) -> str:
        return self.text("rev-parse", f"{rev}^").strip()

    def base_of(self, rev: str) -> str:
        """The commit to diff ``rev`` against, or the empty tree for a root commit."""
        parts = self.text("rev-list", "--parents", "-n1", rev, check=False).split()
        return parts[1] if len(parts) >= 2 else EMPTY_TREE

    def untracked(self) -> list[str]:
        """Files that exist only in the working tree.

        `git diff HEAD` cannot see these, so a file an agent has just written is
        invisible unless we ask for it separately. Ignored files stay ignored.
        """
        raw = self.text("ls-files", "--others", "--exclude-standard", check=False)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _diff_args(self, base: str, head: str | None) -> list[str]:
        if head is WORKTREE:
            return [base]          # base vs working tree
        if head == STAGED:
            return ["--cached", base]
        return [base, head]

    def changed_files(self, base: str, head: str | None = WORKTREE,
                      supported_only: bool = True,
                      paths: list[str] | None = None) -> list[ChangedFile]:
        # Restricting here lets git prune by tree hash, which is the only reason
        # `git log -- <path>` is fast. Filtering after the fact cannot.
        limit = ["--", *paths] if paths else []
        raw = self.text("diff", "--name-status", "-M",
                        *self._diff_args(base, head), *limit)
        files: list[ChangedFile] = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0]
            if status.startswith("R") and len(parts) >= 3:
                entry = ChangedFile("R", parts[2], parts[1])
            else:
                entry = ChangedFile(status[0], parts[1])
            if supported_only and not is_supported(entry.path):
                continue
            files.append(entry)
        return files

    def raw_diff(self, base: str, head: str | None = WORKTREE,
                 paths: list[str] | None = None) -> str:
        """What git costs to convey the same change.

        `git diff` cannot show an untracked file at all, so comparing against it
        alone budgets gita to nothing for content only gita can see. The honest
        baseline is what the agent actually runs: the diff, plus reading the
        files git omitted.
        """
        args = ["diff", "--no-color", "--no-ext-diff", *self._diff_args(base, head)]
        if paths:
            args += ["--", *paths]
        raw = self.text(*args)

        if head is WORKTREE:
            wanted = set(paths) if paths else None
            for path in self.untracked():
                if wanted is not None and path not in wanted:
                    continue
                blob = self.blob(WORKTREE, path)
                if blob is not None:
                    raw += f"\n--- /dev/null\n+++ b/{path}\n"
                    raw += blob.decode("utf8", "replace")
        return raw


class GitObjectError(GitError):
    """A typed failure of committed, local-only object access."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GitObjectInfo:
    oid: GitOid
    object_type: str
    size: int


@dataclass(frozen=True, slots=True)
class GitObject:
    oid: GitOid
    object_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class CommittedCommit:
    oid: GitOid
    tree: GitOid
    parents: tuple[GitOid, ...]
    message: bytes


@dataclass(frozen=True, slots=True)
class GitTreeEntry:
    path: bytes
    oid: GitOid
    object_type: str
    mode: int


@dataclass(frozen=True, slots=True)
class GitTreeChange:
    path: bytes
    before: GitTreeEntry | None
    after: GitTreeEntry | None


class CommittedRepo:
    """Pinned objects only; no worktree fallback, replacement, or lazy fetch.

    Tree/diff pages hold at most 1000 entries, with a 1 MiB record limit.
    Whole objects default to a 5 MiB cap and at most two streams may be active.
    Metadata-only object_info is not subject to the payload cap. Blocking Git
    operations have a 30-second deadline; close abandoned iterators or the repo.
    """

    _TIMEOUT = 30
    _CHUNK_SIZE = 65536
    _RECORD_LIMIT = 1024 * 1024
    _MODE_TYPES = {
        0o040000: "tree", 0o100644: "blob", 0o100755: "blob",
        0o120000: "blob", 0o160000: "commit",
    }

    def __init__(self, root: str | Path, *, max_object_bytes: int = 5 * 1024 * 1024,
                 max_streams: int = 2):
        self._closed = False
        self._batch: subprocess.Popen[bytes] | None = None
        self._batch_discarded = False
        self._info_batch: subprocess.Popen[bytes] | None = None
        self._info_batch_discarded = False
        self._streams: dict[subprocess.Popen[bytes], BinaryIO] = {}
        for name, value in (("max_object_bytes", max_object_bytes), ("max_streams", max_streams)):
            if type(value) is not int or value <= 0:
                raise GitObjectError("invalid_limit", f"{name} must be a positive integer")
        self._max_object_bytes = max_object_bytes
        self._max_streams = max_streams
        self._env = os.environ.copy()
        self._env.update({
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "",
        })
        try:
            self.root = Path(root).resolve()
            output = self._run("rev-parse", "--show-object-format").stdout.strip()
            if output not in (b"sha1", b"sha256"):
                raise GitObjectError("unsupported_object_format", "Expected SHA-1 or SHA-256 storage")
            self.object_format = output.decode("ascii")
            common = self._run("rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
            if common.returncode or common.stdout.startswith(b"--path-format="):
                common = self._run("rev-parse", "--git-common-dir")
            raw_path = common.stdout.removesuffix(b"\n")
            if not raw_path or b"\0" in raw_path:
                raise GitObjectError("invalid_repository", "Missing Git common directory")
            common_path = Path(os.fsdecode(raw_path))
            self.common_dir = (self.root / common_path).resolve()
            if not self.common_dir.is_dir():
                raise GitObjectError("invalid_repository", "Git common directory is not a directory")
            self._check_grafts()
        except (OSError, ValueError, TypeError) as error:
            failure = GitObjectError("invalid_repository", str(error))
            with self._cleanup_guard(failure):
                self.close()
            raise failure from error
        except BaseException as error:
            with self._cleanup_guard(error):
                self.close()
            raise

    def __enter__(self) -> CommittedRepo:
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        with self._cleanup_guard(exc_value):
            self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise GitObjectError("repository_closed", "Committed repository is closed")

    def _check_grafts(self) -> None:
        paths = [self.common_dir / "info" / "grafts"]
        override = self._env.get("GIT_GRAFT_FILE")
        if override:
            paths.append(self.root / override)
        for path in paths:
            try:
                with path.open("rb") as stream:
                    comment = False
                    while block := stream.readline(self._CHUNK_SIZE):
                        content = block.lstrip()
                        if not comment and content:
                            if not content.startswith(b"#"):
                                raise GitObjectError("unsupported_grafts", "Active Git grafts are unsupported")
                            comment = True
                        if block.endswith(b"\n"):
                            comment = False
            except FileNotFoundError:
                continue
            except OSError as error:
                raise GitObjectError("invalid_metadata", "Cannot inspect Git grafts") from error

    def _command(self, args: tuple[str, ...]) -> list[str]:
        self._ensure_open()
        if hasattr(self, "common_dir"):
            self._check_grafts()
        return ["git", "-C", str(self.root), "-c", "protocol.allow=never", *args]

    def _run(self, *args: str, check: bool = True,
             input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        try:
            result = subprocess.run(
                self._command(args), stdin=subprocess.DEVNULL if input is None else None,
                input=input, capture_output=True,
                check=False, env=self._env, timeout=self._TIMEOUT,
            )
        except subprocess.TimeoutExpired as error:
            raise GitObjectError("git_timeout", "Git command timed out") from error
        except (OSError, ValueError) as error:
            raise GitObjectError("git_command_failed", "Cannot launch Git") from error
        if check and result.returncode:
            raise GitObjectError("git_command_failed", _readable(args, result.stderr))
        return result

    @staticmethod
    @contextmanager
    def _cleanup_guard(error: BaseException | None) -> Iterator[None]:
        try:
            yield
        except (GitObjectError, OSError, ValueError, subprocess.SubprocessError) as cleanup:
            if error is None or isinstance(error, GeneratorExit):
                if isinstance(cleanup, GitObjectError):
                    raise
                raise GitObjectError("cleanup_failed", "Cannot close Git resources") from cleanup
            error.add_note(f"Git cleanup failed: {cleanup}")

    @staticmethod
    def _reap(process: subprocess.Popen[bytes]) -> None:
        try:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise GitObjectError("cleanup_failed", "Cannot reap Git child or close its pipes") from error

    def _close_batch(self, *, info: bool = False) -> None:
        attribute = "_info_batch" if info else "_batch"
        process = getattr(self, attribute)
        if process is not None:
            setattr(self, attribute + "_discarded", True)
            self._reap(process)
            setattr(self, attribute, None)
            setattr(self, attribute + "_discarded", False)

    def _close_stream(self, process: subprocess.Popen[bytes]) -> None:
        stderr = self._streams.get(process)
        if stderr is not None:
            self._reap(process)
            try:
                stderr.close()
            except (OSError, ValueError) as error:
                raise GitObjectError("cleanup_failed", "Cannot close Git stream diagnostics") from error
            del self._streams[process]

    def close(self) -> None:
        self._closed = True
        failures: list[GitObjectError] = []
        for info in (False, True):
            try:
                self._close_batch(info=info)
            except GitObjectError as error:
                failures.append(error)
        for process in tuple(self._streams):
            try:
                self._close_stream(process)
            except GitObjectError as error:
                failures.append(error)
        if failures:
            failure = GitObjectError("cleanup_failed", "Git resources remain pending cleanup; retry close()")
            for error in failures:
                failure.add_note(str(error))
            raise failure from failures[0]

    def __del__(self) -> None:  # pragma: no cover - interpreter teardown
        try:
            self.close()
        except Exception:
            pass

    @contextmanager
    def _deadline(self, process: subprocess.Popen[bytes]) -> Iterator[None]:
        expired = Event()

        def terminate() -> None:
            expired.set()
            try:
                process.kill()
            except OSError:
                pass

        timer = Timer(self._TIMEOUT, terminate)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
            timer.join()
            if expired.is_set():
                raise GitObjectError("git_timeout", "Git object operation timed out")

    def _validate_oid(self, oid: GitOid) -> None:
        self._ensure_open()
        if type(oid) is not GitOid or oid.object_format != self.object_format:
            raise GitObjectError("invalid_oid", "Expected a full GitOid matching repository storage")

    def _oid(self, raw: bytes, *, code: str = "invalid_object") -> GitOid:
        length = 40 if self.object_format == "sha1" else 64
        if len(raw) != length or re.fullmatch(rb"[0-9a-fA-F]+", raw) is None:
            raise GitObjectError(code, "Expected a complete repository-format object ID")
        return GitOid(self.object_format, bytes.fromhex(raw.decode("ascii")))

    def resolve(self, revision: str) -> GitOid:
        self._ensure_open()
        if (type(revision) is not str or not revision
                or revision in (STAGED, "WORKTREE") or revision.startswith("-")
                or any(character in revision for character in "\0\r\n")):
            raise GitObjectError("invalid_revision", "Expected a committed revision")
        output = self._run(
            "rev-parse", "--verify", "--end-of-options", revision + "^{commit}",
        ).stdout
        return self._oid(output.removesuffix(b"\n"), code="invalid_revision")

    def _batch_process(self, *, info: bool = False) -> subprocess.Popen[bytes]:
        attribute = "_info_batch" if info else "_batch"
        if getattr(self, attribute + "_discarded"):
            self._close_batch(info=info)
        process = getattr(self, attribute)
        if process is not None:
            if process.poll() is not None:
                error = GitObjectError("object_read_failed", "Git batch helper exited")
                with self._cleanup_guard(error):
                    self._close_batch(info=info)
                raise error
            return process
        try:
            process = subprocess.Popen(
                self._command(("cat-file", "--batch-check" if info else "--batch")), env=self._env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError) as error:
            raise GitObjectError("object_read_failed", "Cannot launch Git batch helper") from error
        setattr(self, attribute, process)
        return process

    def object_info(self, oid: GitOid) -> GitObjectInfo | None:
        self._validate_oid(oid)
        self._check_grafts()
        process = self._batch_process(info=True)
        try:
            if process.stdin is None or process.stdout is None:
                raise GitObjectError("object_read_failed", "Git batch pipes are unavailable")
            with self._deadline(process):
                expected = oid.digest.hex().encode("ascii")
                process.stdin.write(expected + b"\n")
                process.stdin.flush()
                header = process.stdout.readline(256)
                if header == expected + b" missing\n":
                    return None
                match = re.fullmatch(rb"([0-9a-fA-F]+) (blob|tree|commit|tag) ([0-9]+)\n", header)
                if match is None or self._oid(match[1]) != oid:
                    raise GitObjectError("invalid_object", "Invalid Git object metadata")
                return GitObjectInfo(oid, match[2].decode("ascii"), int(match[3]))
        except (OSError, ValueError, OverflowError) as error:
            failure = GitObjectError("object_read_failed", "Cannot read Git metadata response")
            with self._cleanup_guard(failure):
                self._close_batch(info=True)
            raise failure from error
        except BaseException as error:
            with self._cleanup_guard(error):
                self._close_batch(info=True)
            raise

    def read_object(self, oid: GitOid) -> GitObject | None:
        self._validate_oid(oid)
        self._check_grafts()
        process = self._batch_process()
        try:
            if process.stdin is None or process.stdout is None:
                raise GitObjectError("object_read_failed", "Git batch pipes are unavailable")
            with self._deadline(process):
                expected = oid.digest.hex().encode("ascii")
                process.stdin.write(expected + b"\n")
                process.stdin.flush()
                header = process.stdout.readline(256)
                if header == expected + b" missing\n":
                    return None
                match = re.fullmatch(rb"([0-9a-fA-F]+) (blob|tree|commit|tag) ([0-9]+)\n", header)
                if match is None or self._oid(match[1]) != oid:
                    raise GitObjectError("invalid_object", "Invalid Git batch header")
                object_type = match[2].decode("ascii")
                size = int(match[3])
                if size > self._max_object_bytes:
                    raise GitObjectError("resource_limit", "Git object exceeds max_object_bytes")
                digest = hashlib.new(self.object_format)
                digest.update(match[2] + b" " + str(size).encode("ascii") + b"\0")
                data = bytearray()
                while len(data) < size:
                    block = process.stdout.read(min(self._CHUNK_SIZE, size - len(data)))
                    if not block:
                        raise GitObjectError("object_read_failed", "Truncated Git object")
                    data.extend(block)
                    digest.update(block)
                if process.stdout.read(1) != b"\n":
                    raise GitObjectError("invalid_object", "Missing Git batch terminator")
                if digest.digest() != oid.digest:
                    raise GitObjectError("invalid_object", "Git object digest does not match its ID")
            return GitObject(oid, object_type, bytes(data))
        except (OSError, ValueError, OverflowError) as error:
            failure = GitObjectError("object_read_failed", "Cannot read Git batch response")
            with self._cleanup_guard(failure):
                self._close_batch()
            raise failure from error
        except BaseException as error:
            with self._cleanup_guard(error):
                self._close_batch()
            raise

    def commit(self, oid: GitOid) -> CommittedCommit:
        result = self.read_object(oid)
        if result is None:
            raise GitObjectError("object_missing", "Commit is unavailable locally")
        if result.object_type != "commit":
            raise GitObjectError("invalid_object", "Expected a commit object")
        headers, separator, message = result.data.partition(b"\n\n")
        lines = headers.split(b"\n")
        if not separator or not lines[0].startswith(b"tree ") or b"\0" in headers:
            raise GitObjectError("invalid_object", "Malformed commit headers")
        tree = self._oid(lines[0][5:])
        parents: list[GitOid] = []
        parent_headers = True
        previous = b"tree"
        identities: set[bytes] = set()
        for line in lines[1:]:
            if line.startswith(b"parent "):
                if not parent_headers:
                    raise GitObjectError("invalid_object", "Commit parents are out of header order")
                parents.append(self._oid(line[7:]))
                previous = b"parent"
                continue
            parent_headers = False
            if line.startswith(b" "):
                if previous in (b"tree", b"parent"):
                    raise GitObjectError("invalid_object", "Invalid commit header continuation")
                continue
            key, space, value = line.partition(b" ")
            if not space or not value or re.fullmatch(rb"[A-Za-z][A-Za-z0-9-]*", key) is None:
                raise GitObjectError("invalid_object", "Malformed commit header")
            if key in (b"tree", b"parent") or key in identities:
                raise GitObjectError("invalid_object", "Duplicate or misplaced commit header")
            if key in (b"author", b"committer"):
                identities.add(key)
            previous = key
        if identities != {b"author", b"committer"}:
            raise GitObjectError("invalid_object", "Commit identity headers are missing")
        return CommittedCommit(oid, tree, tuple(parents), message)

    def _records(self, args: tuple[str, ...]) -> Iterator[bytes]:
        """Stream NUL records, spooling diagnostics to disk instead of a pipe."""
        self._ensure_open()
        if len(self._streams) >= self._max_streams:
            raise GitObjectError("resource_limit", "Git stream count exceeds max_streams")
        process = None
        stderr = None
        try:
            stderr = TemporaryFile()
            process = subprocess.Popen(
                self._command(args), env=self._env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=stderr,
            )
            self._streams[process] = stderr
            if process.stdout is None:
                raise GitObjectError("git_command_failed", "Git output pipe is unavailable")
            pending = bytearray()
            while True:
                self._ensure_open()
                with self._deadline(process):
                    chunk = process.stdout.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                start = 0
                while start < len(chunk):
                    end = chunk.find(b"\0", start)
                    stop = len(chunk) if end < 0 else end
                    if len(pending) + stop - start > self._RECORD_LIMIT:
                        raise GitObjectError("record_too_large", "Git record exceeds 1 MiB")
                    pending.extend(chunk[start:stop])
                    if end < 0:
                        break
                    record = bytes(pending)
                    pending.clear()
                    yield record
                    start = end + 1
            returncode = process.wait(timeout=self._TIMEOUT)
            if returncode:
                stderr.seek(0)
                raise GitObjectError("io_error", _readable(args, stderr.read(4096)))
            if pending:
                raise GitObjectError("invalid_output", "Unterminated Git record")
        except subprocess.TimeoutExpired as error:
            raise GitObjectError("git_timeout", "Git record stream timed out") from error
        except (OSError, ValueError) as error:
            raise GitObjectError("git_command_failed", "Cannot read Git record stream") from error
        finally:
            with self._cleanup_guard(sys.exc_info()[1]):
                if process is not None:
                    self._close_stream(process)
                elif stderr is not None:
                    stderr.close()

    @staticmethod
    def _page_size(page_size: int) -> None:
        if type(page_size) is not int or not 1 <= page_size <= 1000:
            raise GitObjectError("invalid_page_size", "Page size must be an integer from 1 to 1000")

    def _entry(self, path: bytes, raw_oid: bytes, raw_mode: bytes,
               object_type: bytes | None = None) -> GitTreeEntry:
        if (not path or b"\0" in path
                or any(part in (b"", b".", b"..") for part in path.split(b"/"))):
            raise GitObjectError("invalid_output", "Invalid raw Git path")
        if re.fullmatch(rb"[0-7]{6}", raw_mode) is None:
            raise GitObjectError("invalid_output", "Invalid Git mode")
        mode = int(raw_mode, 8)
        expected_type = self._MODE_TYPES.get(mode)
        if expected_type is None or (object_type is not None and object_type != expected_type.encode("ascii")):
            raise GitObjectError("invalid_output", "Git mode and object type disagree")
        return GitTreeEntry(path, self._oid(raw_oid, code="invalid_output"), expected_type, mode)

    def iter_tree(self, oid: GitOid, *, page_size: int = 1000) -> Iterator[tuple[GitTreeEntry, ...]]:
        self._page_size(page_size)
        committed = self.commit(oid)
        records = self._records(("ls-tree", "-rz", "--full-tree", committed.tree.digest.hex(), "--"))
        page: list[GitTreeEntry] = []
        try:
            for record in records:
                header, separator, path = record.partition(b"\t")
                fields = header.split(b" ")
                if not separator or len(fields) != 3:
                    raise GitObjectError("invalid_output", "Malformed ls-tree record")
                page.append(self._entry(path, fields[2], fields[0], fields[1]))
                if len(page) == page_size:
                    yield tuple(page)
                    page.clear()
            if page:
                yield tuple(page)
        finally:
            with self._cleanup_guard(sys.exc_info()[1]):
                records.close()

    def changes(self, parent: GitOid | None, oid: GitOid, *,
                page_size: int = 1000) -> Iterator[tuple[GitTreeChange, ...]]:
        self._page_size(page_size)
        committed = self.commit(oid)
        if parent is None:
            if committed.parents:
                raise GitObjectError("invalid_parent", "A non-root commit requires an explicit base")
            revisions = ("--root", oid.digest.hex())
        else:
            self.commit(parent)
            revisions = (parent.digest.hex(), oid.digest.hex())
        yield from self._tree_changes(revisions, page_size)

    def tree_changes(self, parent: GitOid, tree: GitOid, *,
                     page_size: int = 1000) -> Iterator[tuple[GitTreeChange, ...]]:
        """Diff trees whose provenance the caller already validated from commits.

        Git traverses changed tree branches; do not reread cached commit objects
        through the batch reader merely to rediscover their tree IDs.
        """
        self._page_size(page_size)
        self._validate_oid(parent)
        self._validate_oid(tree)
        yield from self._tree_changes((parent.digest.hex(), tree.digest.hex()), page_size)

    def _tree_changes(self, revisions: tuple[str, ...], page_size: int) -> Iterator[tuple[GitTreeChange, ...]]:
        records = self._records((
            "diff-tree", "--raw", "-z", "--no-renames", "--no-commit-id", "-r",
            "--no-abbrev", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
            "-O", os.devnull, *revisions, "--",
        ))
        page: list[GitTreeChange] = []
        zeros = b"0" * (40 if self.object_format == "sha1" else 64)
        try:
            for header in records:
                fields = header.split(b" ")
                if len(fields) != 5 or not fields[0].startswith(b":") or fields[4] not in (b"A", b"D", b"M", b"T"):
                    raise GitObjectError("invalid_output", "Malformed raw diff header")
                path = next(records, None)
                if path is None:
                    raise GitObjectError("invalid_output", "Raw diff path is missing")
                endpoints: list[GitTreeEntry | None] = []
                for raw_mode, raw_oid in ((fields[0][1:], fields[2]), (fields[1], fields[3])):
                    if raw_mode == b"000000":
                        if raw_oid != zeros:
                            raise GitObjectError("invalid_output", "Absent diff endpoint has an object ID")
                        endpoints.append(None)
                    else:
                        endpoints.append(self._entry(path, raw_oid, raw_mode))
                before, after = endpoints
                presence = (before is not None, after is not None)
                expected = {b"A": (False, True), b"D": (True, False), b"M": (True, True), b"T": (True, True)}
                if presence != expected[fields[4]]:
                    raise GitObjectError("invalid_output", "Raw diff status disagrees with endpoints")
                page.append(GitTreeChange(path, before, after))
                if len(page) == page_size:
                    yield tuple(page)
                    page.clear()
            if page:
                yield tuple(page)
        finally:
            with self._cleanup_guard(sys.exc_info()[1]):
                records.close()

    def source(self, oid: GitOid, path: bytes, blob: GitOid, mode: int,
               start: int, end: int) -> bytes:
        self._validate_oid(oid)
        self._validate_oid(blob)
        if (type(path) is not bytes or type(mode) is not int or mode not in (0o100644, 0o100755)
                or type(start) is not int or type(end) is not int or not 0 <= start <= end):
            raise GitObjectError("source_unavailable", "Invalid committed source request")
        pages = self.iter_tree(oid)
        found = False
        try:
            for page in pages:
                for entry in page:
                    if entry.path == path:
                        if entry.oid != blob or entry.mode != mode or entry.object_type != "blob":
                            raise GitObjectError("source_unavailable", "Committed source identity does not match")
                        found = True
                        break
                if found:
                    break
        finally:
            with self._cleanup_guard(sys.exc_info()[1]):
                pages.close()
        if not found:
            raise GitObjectError("source_unavailable", "Committed source path is absent")
        result = self.read_object(blob)
        if result is None or result.object_type != "blob" or end > len(result.data):
            raise GitObjectError("source_unavailable", "Committed source bytes or bounds are unavailable")
        return result.data[start:end]

    def shallow_boundaries(self) -> frozenset[GitOid]:
        self._ensure_open()
        self._check_grafts()
        boundaries: set[GitOid] = set()
        length = 40 if self.object_format == "sha1" else 64
        override = self._env.get("GIT_SHALLOW_FILE")
        if override == "":
            return frozenset()
        try:
            path = self.common_dir / "shallow" if override is None else self.root / override
            with path.open("rb") as stream:
                while line := stream.readline(length + 2):
                    boundaries.add(self._oid(line.removesuffix(b"\n"), code="invalid_metadata"))
        except FileNotFoundError:
            return frozenset()
        except (OSError, ValueError) as error:
            raise GitObjectError("invalid_metadata", "Cannot read shallow boundaries") from error
        return frozenset(boundaries)
