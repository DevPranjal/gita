"""Output robustness and completeness, both found in iteration 4.

An agent's second command was `$env:PYTHONIOENCODING='utf-8'; gita diff ...`
because the first attempt died on Windows: piped stdout is cp1252 and the
headline contained a middle dot. That task cost +149%.

`gita history` answered "when did this change" but not "what changed", so the
agent fell back to `git log` and `git show` -- the same incompleteness that made
`gita diff` expensive before one-shot answers.
"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from gita.cli import main
from gita.cli import render
from gita.cli import _Tee
from gita.vcs.git import Repo


def run(repo, *args) -> tuple[int, str]:
    out = io.StringIO()
    code = main(["-C", str(repo.root), *args], out=out)
    return code, out.getvalue()


@pytest.fixture
def evolving(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True)

    def commit(msg, src):
        (tmp_path / "svc.py").write_bytes(src)
        git("add", "-A")
        git("commit", "-q", "-m", msg)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    commit("add fetch", b"def fetch(url):\n    return get(url)\n")
    commit("retry on failure",
           b"def fetch(url):\n    for _ in range(3):\n        return get(url)\n")
    return Repo(tmp_path)


class TestAsciiSafeOutput:
    """Non-ASCII in output cost a whole task in iteration 4."""

    def test_diff_output_is_ascii(self, evolving):
        _, output = run(evolving, "diff", "HEAD^", "HEAD")
        output.encode("ascii")  # must not raise

    def test_history_output_is_ascii(self, evolving):
        _, output = run(evolving, "history", "fetch")
        output.encode("ascii")

    def test_usage_is_ascii(self, evolving):
        run(evolving)[1].encode("ascii")

    def test_survives_a_cp1252_stream(self, evolving):
        """Reproduces the failure exactly: a stream that cannot encode UTF-8."""
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="")
        code = main(["-C", str(evolving.root), "diff", "HEAD^", "HEAD"], out=stream)
        stream.flush()
        assert code == 0
        assert b"fetch" in buffer.getvalue()


class TestNonAsciiSourceIsNotMangled:
    """Source code is not ours to corrupt.

    `got-new-option` carries a `->` arrow inside a quoted doc line. On a cp1252
    stream the old fallback turned it into `?`, which is a fact reported wrong,
    and the retry double-counted every token we claimed to have emitted.
    """

    TEXT = "left \u2192 right"

    def test_utf8_is_preserved_on_a_cp1252_stream(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="")
        render.write(stream, self.TEXT)
        stream.flush()
        assert "\u2192" in buffer.getvalue().decode("utf8")

    def test_a_stream_that_cannot_reconfigure_still_gets_the_answer(self):
        class Stubborn:
            encoding = "ascii"

            def __init__(self):
                self.written = []

            def write(self, text):
                text.encode("ascii")  # raises on non-ASCII, like a real stream
                self.written.append(text)
                return len(text)

        out = Stubborn()
        render.write(out, self.TEXT)
        assert "right" in "".join(out.written)

    def test_the_retry_is_not_counted_twice(self):
        """What we bill an agent for must be what the agent received."""
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", newline="")
        tee = _Tee(stream)
        render.write(tee, self.TEXT)
        stream.flush()
        assert tee.text.count("right") == 1


class TestHistoryAnswersWhatChanged:
    """Saying *when* without *what* sent the agent back to git log."""

    def test_includes_the_code_that_changed(self, evolving):
        code, output = run(evolving, "history", "fetch")
        assert code == 0
        assert "@@" in output
        assert "range(3)" in output

    def test_still_lists_the_commits(self, evolving):
        _, output = run(evolving, "history", "fetch")
        assert "body_changed" in output
        assert "retry on failure" in output

    def test_brief_omits_the_code(self, evolving):
        _, output = run(evolving, "history", "fetch", "--brief")
        assert "@@" not in output
        assert "retry on failure" in output

    def test_respects_a_budget(self, evolving):
        from gita.context import count_tokens

        _, output = run(evolving, "history", "fetch", "--budget", "40")
        assert count_tokens(output) <= 40

    def test_json_carries_the_detail(self, evolving):
        payload = json.loads(run(evolving, "history", "fetch", "--json")[1])
        assert payload["events"]
        assert any(e.get("patch") for e in payload["events"])
