"""Minimal git plumbing -- enough to feed the differ two revisions of a file."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..entities.languages import is_supported

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
             limit: int = 20, paths: list[str] | None = None) -> list["CommitRecord"]:
        """Commits with their first parent and changed files, in one git call.

        Asking per commit cost two processes each. `git log --name-status` gives
        the same information in one pass. Merges are the exception: git prints no
        file list for them, so those fall back to an explicit diff.
        """
        span = [f"{since}..{until}"] if since else [until]
        limit_args = ["--", *paths] if paths else []
        raw = self.text("log", f"--format={_WALK_FORMAT}", "--name-status",
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

        for record in records:
            if record.is_merge:
                record.files = self.changed_files(record.parent, record.sha,
                                                  supported_only=False, paths=paths)
        return records

    def resolve(self, rev: str | None) -> str:
        if is_pseudo_rev(rev) or rev == EMPTY_TREE:
            return rev or "WORKTREE"
        # Plain `rev-parse` echoes an unknown argument back on stdout, so its
        # output is truthy even on failure and every guard built on it passed.
        return self.text("rev-parse", "--verify", "--quiet", rev, check=False).strip()

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
