"""Working-tree state, not just working-tree diff.

On the uncommitted-work task the agent ran `gita diff`, then `git status --short`,
then `git diff` -- every repetition, three git calls after gita had answered.

The reason is a real gap: `git diff HEAD` does not list untracked files, so a new
file an agent has just written is invisible to gita. "What did I change" includes
files that do not exist in HEAD yet, and answering only half the question sends
the agent to git for the other half.
"""

from __future__ import annotations

import subprocess

import pytest

from gita import diff_revisions
from gita.vcs.git import Repo


@pytest.fixture
def workspace(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "app.py").write_bytes(b"def handle(request):\n    return request\n")
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    return Repo(tmp_path)


class TestUntrackedFiles:
    def test_a_new_file_is_visible(self, workspace):
        """An agent writes a new module and asks what changed. It must be told."""
        (workspace.root / "service.py").write_bytes(
            b"def start(port):\n    return port\n")
        changeset = diff_revisions(workspace, "HEAD", None)
        assert any("service.py" in c.entity.path for c in changeset.material())

    def test_entities_inside_a_new_file_are_extracted(self, workspace):
        (workspace.root / "service.py").write_bytes(
            b"def start(port):\n    return port\n")
        ids = {c.entity.id for c in diff_revisions(workspace, "HEAD", None).material()}
        assert "service.py::start" in ids

    def test_tracked_and_untracked_appear_together(self, workspace):
        (workspace.root / "app.py").write_bytes(
            b"def handle(request, context):\n    return request\n")
        (workspace.root / "service.py").write_bytes(b"def start():\n    return 1\n")
        paths = {c.entity.path for c in diff_revisions(workspace, "HEAD", None).material()}
        assert {"app.py", "service.py"} <= paths

    def test_ignored_files_stay_invisible(self, workspace):
        (workspace.root / ".gitignore").write_bytes(b"*.log\n")
        (workspace.root / "debug.log").write_bytes(b"noise\n")
        paths = {c.entity.path for c in diff_revisions(workspace, "HEAD", None).material()}
        assert "debug.log" not in paths

    def test_untracked_are_not_reported_for_commit_ranges(self, workspace):
        """A commit range is history; the working tree has no place in it."""
        (workspace.root / "service.py").write_bytes(b"def start():\n    return 1\n")
        changeset = diff_revisions(workspace, "HEAD", "HEAD")
        assert not any("service.py" in c.entity.path for c in changeset.material())

    def test_untracked_directory_contents_are_listed(self, workspace):
        pkg = workspace.root / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_bytes(b"def helper():\n    return 1\n")
        paths = {c.entity.path for c in diff_revisions(workspace, "HEAD", None).material()}
        assert any(p.endswith("pkg/mod.py") for p in paths)


class TestRepoUntracked:
    def test_lists_untracked_paths(self, workspace):
        (workspace.root / "new.py").write_bytes(b"x = 1\n")
        assert "new.py" in workspace.untracked()

    def test_empty_when_clean(self, workspace):
        assert workspace.untracked() == []

    def test_excludes_ignored(self, workspace):
        (workspace.root / ".gitignore").write_bytes(b"secret.txt\n")
        (workspace.root / "secret.txt").write_bytes(b"shh\n")
        assert "secret.txt" not in workspace.untracked()
