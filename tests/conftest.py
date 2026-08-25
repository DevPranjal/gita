import subprocess

import pytest

from gita.entities.store import TREES
from gita.telemetry.events import ENV_SINK
from gita.vcs.git import Repo


@pytest.fixture(autouse=True, scope="session")
def _telemetry_goes_nowhere_real(tmp_path_factory):
    """Tests must not write into whatever log the developer is collecting.

    They had been: a usage report over a real week counted 20 failed calls, all
    of them tests deliberately exercising error paths against pytest fixtures.
    Measurement that its own test suite contaminates cannot be trusted.
    """
    import os
    previous = os.environ.get(ENV_SINK)
    os.environ[ENV_SINK] = str(tmp_path_factory.mktemp("telemetry") / "usage.jsonl")
    yield
    if previous is None:
        os.environ.pop(ENV_SINK, None)
    else:
        os.environ[ENV_SINK] = previous


@pytest.fixture(autouse=True)
def _isolated_tree_store():
    """A process-wide cache must not leak results between tests."""
    TREES.clear()
    yield
    TREES.clear()


@pytest.fixture
def repo(tmp_path):
    """A small git repo with two commits and a variety of change kinds."""

    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    (tmp_path / "app.py").write_bytes(
        b"import os\n"
        b"\n"
        b"\n"
        b"def handle(request):\n"
        b"    total = 0\n"
        b"    return total\n"
        b"\n"
        b"\n"
        b"class Store:\n"
        b"    def get(self, key):\n"
        b"        return key\n"
        b"\n"
        b"    def put(self, key, value):\n"
        b"        return value\n"
    )
    (tmp_path / "test_app.py").write_bytes(
        b"def test_handle():\n    assert handle(1)\n"
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("add", "-A")
    run("commit", "-q", "-m", "first")

    (tmp_path / "app.py").write_bytes(
        b"import os\n"
        b"import sys\n"
        b"\n"
        b"\n"
        b"def handle(request, context):\n"
        b"    total = 0\n"
        b"    return total\n"
        b"\n"
        b"\n"
        b"class Store:\n"
        b"    def get(self, key):\n"
        b"        return self.data[key]\n"
        b"\n"
        b"    def put(self, key, value):\n"
        b"        return value\n"
    )
    (tmp_path / "test_app.py").write_bytes(
        b"def test_handle():\n    assert handle(1, 2)\n"
    )
    run("add", "-A")
    run("commit", "-q", "-m", "second")
    return Repo(tmp_path)
