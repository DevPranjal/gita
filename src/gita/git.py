"""Thin subprocess wrappers around the ``git`` binary.

Everything here returns plain Python types; nothing caches state. Errors from
git surface as :class:`GitError`. We never shell out with ``shell=True``.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class GitError(RuntimeError):
    def __init__(self, returncode: int, cmd: list[str], stderr: str) -> None:
        super().__init__(f"git {' '.join(cmd[1:])} failed ({returncode}): {stderr.strip()}")
        self.returncode = returncode
        self.cmd = cmd
        self.stderr = stderr


def _run(
    root: Path,
    args: list[str],
    *,
    check: bool = True,
    text: bool = True,
    input_: str | None = None,
) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(root), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=text,
        input=input_,
        encoding="utf-8" if text else None,
    )
    if check and proc.returncode != 0:
        raise GitError(proc.returncode, cmd, proc.stderr or "")
    return proc


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def discover_root(start: Path) -> Path:
    """Walk up from ``start`` to find the enclosing git work tree.

    Raises :class:`FileNotFoundError` if not inside a git repo.
    """
    start = start.resolve()
    proc = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise FileNotFoundError(f"not a git repository: {start}")
    return Path(proc.stdout.strip())


def init(root: Path) -> Path:
    """Initialize a new git repo at ``root`` (idempotent)."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(root)],
        check=True, capture_output=True, text=True,
    )
    return root


# ---------------------------------------------------------------------------
# refs
# ---------------------------------------------------------------------------


def rev_parse(root: Path, ref: str) -> str:
    """Resolve ``ref`` to a full 40-char sha. Raises on unknown ref."""
    proc = _run(root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    return proc.stdout.strip()


def head_sha(root: Path) -> str | None:
    """Current HEAD commit sha, or ``None`` if the repo has no commits yet."""
    try:
        return rev_parse(root, "HEAD")
    except GitError:
        return None


def current_branch(root: Path) -> str | None:
    """Symbolic branch name (e.g. ``main``), or ``None`` if detached/unborn."""
    proc = _run(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


# ---------------------------------------------------------------------------
# trees + blobs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeEntry:
    path: str
    blob_sha: str


def ls_tree(root: Path, ref: str) -> list[TreeEntry]:
    """Recursive tree listing at ``ref``. Only file blobs (no submodules)."""
    proc = _run(root, ["ls-tree", "-r", "-z", "--full-tree", ref])
    out: list[TreeEntry] = []
    for record in proc.stdout.split("\x00"):
        if not record:
            continue
        # "<mode> <type> <sha>\t<path>"
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if len(parts) < 3:
            continue
        _mode, typ, sha = parts[0], parts[1], parts[2]
        if typ != "blob":
            continue
        out.append(TreeEntry(path=path, blob_sha=sha))
    return out


def cat_blob(root: Path, blob_sha: str) -> str:
    """Read a blob as utf-8 text. Binary blobs raise UnicodeDecodeError."""
    proc = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-p", blob_sha],
        capture_output=True, check=True,
    )
    return proc.stdout.decode("utf-8")


def show_path_at(root: Path, ref: str, path: str) -> str | None:
    """Return file contents at ``ref:path``, or ``None`` if the path isn't there."""
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{path}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def log_shas(root: Path, ref: str = "HEAD", *, max_count: int | None = None) -> list[str]:
    """First-parent log from ``ref``, newest first."""
    args = ["log", "--first-parent", "--format=%H"]
    if max_count is not None:
        args.append(f"-n{max_count}")
    args.append(ref)
    try:
        proc = _run(root, args)
    except GitError:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


@dataclass(frozen=True)
class CommitMeta:
    sha: str
    parents: list[str]
    author_name: str
    author_email: str
    timestamp: int
    message: str


def commit_meta(root: Path, sha: str) -> CommitMeta:
    """Read commit metadata. Uses ``%x00`` field separators to be safe with newlines."""
    fmt = "%H%x00%P%x00%an%x00%ae%x00%at%x00%B"
    proc = _run(root, ["log", "-1", f"--format={fmt}", sha])
    raw = proc.stdout
    # %B may contain newlines + trailing one from git itself; rstrip safely.
    parts = raw.split("\x00", 5)
    h, p, an, ae, at, msg = parts
    return CommitMeta(
        sha=h.strip(),
        parents=[x for x in p.strip().split() if x],
        author_name=an,
        author_email=ae,
        timestamp=int(at),
        message=msg.rstrip("\n"),
    )


# ---------------------------------------------------------------------------
# status / index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusEntry:
    path: str
    index_status: str   # one letter, " " = unmodified in index
    work_status: str    # one letter, " " = unmodified in worktree
    orig_path: str | None = None  # set for rename/copy entries


def status(root: Path) -> list[StatusEntry]:
    """Parse ``git status --porcelain=v1 -z``. Includes untracked."""
    proc = _run(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    out: list[StatusEntry] = []
    records = proc.stdout.split("\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        if len(rec) < 3:
            i += 1
            continue
        x, y, path = rec[0], rec[1], rec[3:]
        orig = None
        # Rename/copy entries have the old name as the *next* record.
        if x in ("R", "C") and i + 1 < len(records):
            orig = records[i + 1]
            i += 2
        else:
            i += 1
        out.append(StatusEntry(path=path, index_status=x, work_status=y, orig_path=orig))
    return out


def staged_paths(root: Path) -> list[str]:
    """Paths with changes in the index (staged)."""
    return [
        e.path for e in status(root)
        if e.index_status not in (" ", "?")
    ]


def worktree_path(root: Path, path: str) -> Path:
    return root / path


def working_tree_text(root: Path, path: str) -> str | None:
    """Read a file from the working tree, or ``None`` if absent."""
    p = worktree_path(root, path)
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def staged_text(root: Path, path: str) -> str | None:
    """Read a file from the index (staged), or ``None`` if not staged."""
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f":{path}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# write operations
# ---------------------------------------------------------------------------


def add(root: Path, paths: list[str]) -> None:
    _run(root, ["add", "--", *paths])


def commit(root: Path, message: str, *, allow_empty: bool = False) -> str:
    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    _run(root, args)
    sha = head_sha(root)
    assert sha is not None
    return sha


def is_git_dir(path: Path) -> bool:
    return (path / ".git").exists()


# ---------------------------------------------------------------------------
# merge helpers
# ---------------------------------------------------------------------------


def is_merge_commit(root: Path, sha: str) -> bool:
    """True iff the commit has more than one parent."""
    return len(commit_meta(root, sha).parents) > 1


def merge_parents(root: Path, sha: str) -> list[str]:
    """Parents of ``sha`` (length > 1 iff merge commit)."""
    return list(commit_meta(root, sha).parents)


# ---------------------------------------------------------------------------
# checkout / restore (used by bisect to walk history safely)
# ---------------------------------------------------------------------------


@contextmanager
def checkout_then_restore(root: Path, sha: str) -> Iterator[None]:
    """Detach-checkout ``sha`` for the duration of the block, then restore.

    Captures the original branch (or detached sha) at entry and restores it
    on exit — even if the body raises. Caller is responsible for ensuring the
    working tree is clean before entering (otherwise git refuses the
    checkout).
    """
    branch = current_branch(root)
    original = branch if branch is not None else head_sha(root)
    if original is None:
        raise GitError(1, ["git", "rev-parse", "HEAD"], "no HEAD to restore")
    _run(root, ["checkout", "--detach", "--quiet", sha])
    try:
        yield
    finally:
        _run(root, ["checkout", "--quiet", original], check=False)
