"""The suite's own preconditions.

A run that silently depends on the developer's Git configuration is measuring
the machine, not the code. These assert the isolation `conftest` establishes, so
that losing it fails here with one sentence naming the cause -- rather than as
~199 fixture errors spread across files that have nothing to do with it.
"""

from __future__ import annotations

import os
import subprocess


def _resolved(repo, name: str) -> str:
    """What git actually resolves a setting to inside a fixture repository."""
    return subprocess.run(
        ["git", "-C", str(repo.root), "config", "--get", name],
        capture_output=True, text=True).stdout.strip()


class TestGitIsIsolatedFromThisMachine:
    def test_the_system_config_is_dropped(self):
        """Git for Windows ships its own /etc/gitconfig; nothing here wants it."""
        assert os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"

    def test_the_global_config_is_replaced(self):
        """Also covers the XDG path, which a bare HOME override would miss."""
        replacement = os.environ.get("GIT_CONFIG_GLOBAL")
        assert replacement and os.path.exists(replacement)

    def test_no_settings_are_injected_through_the_environment(self):
        """The mechanism GIT_CONFIG_NOSYSTEM cannot reach.

        git treats these as command-line values, so no config file overrides
        them. VS Code ships `safe.bareRepository=explicit` this way, which made
        every bare-repository fixture fail in a terminal launched from the
        editor and pass in one launched from the shell.
        """
        assert not os.environ.get("GIT_CONFIG_COUNT")
        assert not os.environ.get("GIT_CONFIG_PARAMETERS")
        assert not [name for name in os.environ
                    if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]

    def test_a_bare_repository_is_usable(self, tmp_path):
        """What `safe.bareRepository=explicit` was breaking."""
        bare = tmp_path / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True,
                       capture_output=True)
        written = subprocess.run(
            ["git", "-C", str(bare), "hash-object", "-w", "--stdin"],
            input=b"hello\n", capture_output=True, check=True)
        assert written.stdout.strip()

    def test_line_endings_are_never_translated(self, repo):
        """The setting that cost 199 errors: `git add` refusing LF fixtures with
        `fatal: LF would be replaced by CRLF`."""
        assert _resolved(repo, "core.autocrlf") == "false"
        assert _resolved(repo, "core.safecrlf") == "false"

    def test_an_identity_exists_without_each_fixture_supplying_one(self, tmp_path):
        """A fixture that forgets to set one should still be able to commit."""
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True,
                       capture_output=True)
        (tmp_path / "f.txt").write_bytes(b"one\n")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "one"],
                       check=True, capture_output=True)

    def test_committed_bytes_survive_a_round_trip(self, repo):
        """Byte-exact evidence is the whole premise; a translation layer would
        corrupt it even where it did not refuse the write outright."""
        blob = subprocess.run(
            ["git", "-C", str(repo.root), "cat-file", "blob", "HEAD:app.py"],
            capture_output=True, check=True).stdout
        assert blob and b"\r\n" not in blob
