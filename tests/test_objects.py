"""Object access through git plumbing, not one process per read.

Answering "how did this function change over twenty commits" cost 98 git
subprocesses, 6.5 seconds of the 12 it took, at ~67ms of process spawn each.
Parsing was not the bottleneck; spawning was.

`git cat-file --batch` is the plumbing command built for exactly this: one
long-lived process that streams any number of objects. These tests pin the
behaviour that matters -- it must return the same bytes as `git show`, for text,
for binary, and for things that are not there at all.
"""

from __future__ import annotations

import subprocess

import pytest

from gita.vcs.git import STAGED, WORKTREE, Repo


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "a.py").write_bytes(b"def a():\n    return 1\n")
    (tmp_path / "bin.dat").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "utf8.py").write_bytes("x = 'caf\u00e9 \u2192'\n".encode("utf8"))
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "first")

    (tmp_path / "a.py").write_bytes(b"def a():\n    return 2\n")
    git("add", "-A")
    git("commit", "-q", "-m", "second")
    return Repo(tmp_path)


def via_show(repo: Repo, rev: str, path: str) -> bytes | None:
    out = subprocess.run(["git", "-C", str(repo.root), "show", f"{rev}:{path}"],
                         capture_output=True)
    return out.stdout or None


class TestBatchMatchesGitShow:
    @pytest.mark.parametrize("path", ["a.py", "utf8.py", "bin.dat"])
    def test_same_bytes_at_head(self, repo, path):
        assert repo.blob("HEAD", path) == via_show(repo, "HEAD", path)

    def test_same_bytes_at_an_older_revision(self, repo):
        assert repo.blob("HEAD^", "a.py") == via_show(repo, "HEAD^", "a.py")
        assert repo.blob("HEAD^", "a.py") != repo.blob("HEAD", "a.py")

    def test_binary_survives_exactly(self, repo):
        """A NUL-heavy blob is where a line-oriented reader would corrupt data."""
        assert repo.blob("HEAD", "bin.dat") == bytes(range(256)) * 4

    def test_missing_path_is_none(self, repo):
        assert repo.blob("HEAD", "nope.py") is None

    def test_missing_revision_is_none(self, repo):
        assert repo.blob("nosuchrev", "a.py") is None

    def test_empty_file_is_none_not_an_error(self, repo):
        (repo.root / "empty.py").write_bytes(b"")
        subprocess.run(["git", "-C", str(repo.root), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo.root), "commit", "-q", "-m", "empty"],
                       check=True, capture_output=True)
        assert repo.blob("HEAD", "empty.py") is None


class TestBatchIsActuallyBatched:
    def test_many_reads_do_not_spawn_many_processes(self, repo, monkeypatch):
        """The whole point: reads after the first must not cost a process."""
        spawns = []
        original = subprocess.Popen

        def counting(args, **kwargs):
            spawns.append(args)
            return original(args, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", counting)
        for _ in range(25):
            repo.blob("HEAD", "a.py")
            repo.blob("HEAD^", "a.py")
        assert len(spawns) <= 1

    def test_a_failed_batch_still_answers(self, repo, monkeypatch):
        """If plumbing is unavailable, fall back rather than lose the answer."""
        monkeypatch.setattr(type(repo._objects), "_process", lambda self: None)
        assert repo.blob("HEAD", "a.py") == via_show(repo, "HEAD", "a.py")

    def test_a_process_that_dies_mid_session_still_answers(self, repo):
        """A killed helper must degrade to a slower read, not an exception."""
        assert repo.blob("HEAD", "a.py") is not None       # start the helper
        repo._objects.close()
        assert repo.blob("HEAD", "a.py") == via_show(repo, "HEAD", "a.py")


class TestWorkingTreeAndIndexUnchanged:
    def test_worktree_reads_from_disk(self, repo):
        (repo.root / "a.py").write_bytes(b"scratch\n")
        assert repo.blob(WORKTREE, "a.py") == b"scratch\n"

    def test_absent_worktree_file_is_none(self, repo):
        assert repo.blob(WORKTREE, "gone.py") is None

    def test_index_is_still_readable(self, repo):
        assert repo.blob(STAGED, "a.py") is not None
