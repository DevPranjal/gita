"""Independent AST validation permits only lexically verified trailing trivia."""
import importlib
from pathlib import Path

import pytest
from types import SimpleNamespace
import json


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setenv("GITA_BENCH_SOURCE", str(Path(__file__).resolve().parents[1] / "src"))
    return importlib.import_module("verify.public_index_worker")


def test_comment_tail_is_not_missing_definition(worker):
    source = b"def f():\n    return 1\n    # trailing explanation\n"
    span = worker.ast_spans(source)["f"]
    assert worker.matches_ast_span(source, span, span)
    assert worker.matches_ast_span(source, span, (span[0], len(source)))
    assert worker.matches_ast_span(source, span, (span[0], len(source) - 1))
    assert not worker.matches_ast_span(source, span, (span[0], len(source) - 5))
    followed = source + b"other = 2\n"
    assert worker.matches_ast_span(followed, span, (span[0], len(source)))


def test_extra_code_wrong_start_and_shortened_body_still_fail(worker):
    source = b"def f():\n    return 1\nother = 2\n"
    span = worker.ast_spans(source)["f"]
    assert not worker.matches_ast_span(source, span, (span[0], len(source)))
    assert not worker.matches_ast_span(source, span, (span[0] + 1, span[1]))
    assert not worker.matches_ast_span(source, span, (span[0], span[1] - 1))
    assert not worker.matches_ast_span(source, span, (span[0], len(source) + 1))


def test_new_syntax_uses_explicit_isolated_stdlib_oracle(worker, monkeypatch):
    source = b"not valid old syntax"
    monkeypatch.setenv("GITA_AST_ORACLE_PYTHON", "qualified-python")
    def newer(args, **kwargs):
        assert args[:3] == ["qualified-python", "-I", "-c"]
        assert kwargs["input"] == source
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "version": "3.13.0", "spans": {"f": [0, 10]}}).encode(), stderr=b"")
    monkeypatch.setattr(worker.subprocess, "run", newer)
    assert worker.ast_spans(source) == {"f": (0, 10)}
    assert "3.13.0" in worker.AST_ORACLE_VERSIONS


def test_new_oracle_syntax_failure_is_not_a_success(worker, monkeypatch):
    monkeypatch.setenv("GITA_AST_ORACLE_PYTHON", "qualified-python")
    monkeypatch.setattr(worker.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout=b"", stderr=b"SyntaxError"))
    with pytest.raises(AssertionError, match="refused syntax"):
        worker.ast_spans(b"def broken(")
