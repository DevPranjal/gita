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
def git_calls(monkeypatch):
    """Record every git subprocess issued through ``Repo._run``.

    WS-1 changes cost, not output, so its tests have to assert on the mechanism.
    Elapsed time is too noisy to assert on; the number of processes is exact.
    """
    recorded: list[tuple[str, ...]] = []
    original = Repo._run

    def recording(self, *args, check=True):
        recorded.append(args)
        return original(self, *args, check=check)

    monkeypatch.setattr(Repo, "_run", recording)

    class Calls:
        argv = recorded

        def of(self, subcommand: str) -> int:
            return sum(1 for args in recorded if args and args[0] == subcommand)

        def clear(self) -> None:
            recorded.clear()

    return Calls()


@pytest.fixture
def merge_repo(tmp_path):
    """A history that is mostly merges, as real projects are.

    Flask is 110 merges in 150 commits, and git prints no file list for a merge,
    so a linear fixture would never exercise the path that dominates.
    """

    def run(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    def write(name, body):
        (tmp_path / name).write_text(body)

    write("app.py", "def handle(request):\n    return 1\n")
    write("util.py", "def helper():\n    return 0\n")
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("add", "-A")
    run("commit", "-q", "-m", "first")

    for n in range(1, 4):
        run("checkout", "-q", "-b", f"feature-{n}")
        write("app.py", f"def handle(request):\n    return {n + 1}\n")
        write(f"mod{n}.py", f"def added_{n}():\n    return {n}\n")
        run("add", "-A")
        run("commit", "-q", "-m", f"work on feature {n}")
        run("checkout", "-q", "main")
        # --no-ff guarantees a merge commit even when the branch fast-forwards.
        run("merge", "-q", "--no-ff", "-m", f"merge feature {n}", f"feature-{n}")

    write("util.py", "def helper():\n    return 99\n")
    run("add", "-A")
    run("commit", "-q", "-m", "direct commit on main")
    return Repo(tmp_path)


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
