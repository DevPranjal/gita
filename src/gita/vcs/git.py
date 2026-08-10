"""Minimal git plumbing -- enough to feed the differ two revisions of a file."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..entities.languages import is_supported


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

    def blob(self, rev: str, path: str) -> bytes | None:
        # Absent on one side is normal for added and deleted files.
        data = self._run("show", f"{rev}:{path}", check=False)
        return data or None

    def resolve(self, rev: str) -> str:
        return self.text("rev-parse", rev).strip()

    def parent(self, rev: str) -> str:
        return self.text("rev-parse", f"{rev}^").strip()

    def changed_files(self, base: str, head: str,
                      supported_only: bool = True) -> list[ChangedFile]:
        raw = self.text("diff", "--name-status", "-M", base, head)
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

    def raw_diff(self, base: str, head: str, paths: list[str] | None = None) -> str:
        args = ["diff", "--no-color", "--no-ext-diff", base, head]
        if paths:
            args += ["--", *paths]
        return self.text(*args)
