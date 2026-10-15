"""Independent AST validation permits only lexically verified trailing trivia."""
import importlib
from pathlib import Path

import pytest


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
