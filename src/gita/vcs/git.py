"""Minimal git plumbing -- enough to feed the differ two revisions of a file."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
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
        return self.status == "A"

    @property
    def is_deleted(self) -> bool:
        return self.status == "D"

    @property
    def source_path(self) -> str:
        return self.old_path or self.path


class GitError(RuntimeError):
    pass


class Repo:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _run(self, *args: str, check: bool = True) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, check=False,
        )
        # A bad flag yields empty stdout, which reads exactly like "no changes".
        if check and result.returncode != 0:
            detail = result.stderr.decode("utf8", "replace").strip()
            raise GitError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
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
        data = self._run("show", f"{rev}:{path}", check=False)
        return data or None

    def resolve(self, rev: str | None) -> str:
        if is_pseudo_rev(rev):
            return rev or "WORKTREE"
        return self.text("rev-parse", rev, check=False).strip()

    def parent(self, rev: str) -> str:
        return self.text("rev-parse", f"{rev}^").strip()

    def base_of(self, rev: str) -> str:
        """The commit to diff ``rev`` against, or the empty tree for a root commit."""
        parts = self.text("rev-list", "--parents", "-n1", rev, check=False).split()
        return parts[1] if len(parts) >= 2 else EMPTY_TREE

    def _diff_args(self, base: str, head: str | None) -> list[str]:
        if head is WORKTREE:
            return [base]          # base vs working tree
        if head == STAGED:
            return ["--cached", base]
        return [base, head]

    def changed_files(self, base: str, head: str | None = WORKTREE,
                      supported_only: bool = True) -> list[ChangedFile]:
        raw = self.text("diff", "--name-status", "-M", *self._diff_args(base, head))
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
        args = ["diff", "--no-color", "--no-ext-diff", *self._diff_args(base, head)]
        if paths:
            args += ["--", *paths]
        return self.text(*args)
