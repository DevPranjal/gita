"""The transitional release must exercise its indexed default without opt-in."""

import asyncio
import importlib
import io
import json
import os
import subprocess
import sys
from http.client import HTTPConnection
from threading import Thread

import pytest

from gita.cli import main
from gita.mcp import tools


@pytest.fixture(autouse=True)
def indexed_default(monkeypatch):
    monkeypatch.delenv("GITA_ENABLE_LEGACY", raising=False)


def cli(repo, *args):
    output = io.StringIO()
    code = main(["-C", str(repo.root), "--json", *args], out=output)
    return code, json.loads(output.getvalue())


@pytest.mark.parametrize("command", [
    ["diff"], ["darshan"], ["show", "handle"], ["shloka", "handle"],
    ["expand", "Store"], ["vistaar", "Store"], ["diff", "--indexed"],
    ["history", "handle", "--legacy"],
])
def test_legacy_cli_refuses_before_git_or_parsing(repo, monkeypatch, command):
    def forbidden(*args, **kwargs):
        pytest.fail("A disabled command must not read Git or reconstruct entities")

    monkeypatch.setattr(type(repo), "resolve", forbidden)
    code, result = cli(repo, *command)
    assert code == 2
    assert result["error"]["code"] == "legacy_disabled"
    assert "GITA_ENABLE_LEGACY=1" in result["error"]["message"]


@pytest.mark.parametrize("command", ["history", "katha"])
def test_missing_index_never_builds_parses_or_writes(repo, monkeypatch, command):
    from gita.index import builder, evidence
    from gita.index.store import IndexStore

    def forbidden(*args, **kwargs):
        pytest.fail("Indexed reads must not build or parse")

    monkeypatch.setattr(builder, "build", forbidden)
    monkeypatch.setattr(evidence, "parse_evidence", forbidden)
    location = IndexStore.locate(repo)
    assert not location.exists()
    code, result = cli(repo, command, "handle")
    assert code == 3
    assert result["error"]["code"] == "not_built"
    assert not location.exists()
    result = tools.history_tool(str(repo.root), entity="handle")
    assert result["error"]["code"] == "not_built"
    assert not location.exists()


def test_history_is_indexed_even_when_legacy_enabled(repo, monkeypatch):
    assert cli(repo, "index", "--workers", "1")[0] == 0
    first = cli(repo, "history", "handle", "--brief")[1]
    compatibility = cli(repo, "history", "handle", "--brief", "--indexed")[1]
    assert first == compatibility
    assert first["results"]
    assert first["generation"].startswith("gen1_")
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "1")
    assert cli(repo, "history", "handle", "--brief")[1] == first
    assert tools.history_tool(str(repo.root), entity="handle", brief=True) == first
    legacy = tools.history_tool(str(repo.root), entity="handle", legacy=True, brief=True)
    assert legacy["events"]


def test_indexed_reads_and_selectors_need_no_parser_after_build(repo, monkeypatch):
    from gita.index import builder, evidence
    from gita.vcs.git import CommittedRepo

    assert cli(repo, "index", "--workers", "2", "--scope", "named")[0] == 0

    def forbidden(*args, **kwargs):
        pytest.fail("A published indexed query must not build, parse, or reconstruct")

    monkeypatch.setattr(builder, "build", forbidden)
    monkeypatch.setattr(evidence, "parse_evidence", forbidden)
    newest = cli(repo, "history", "handle", "--scope", "named", "--order", "newest")[1]
    oldest = cli(repo, "katha", "handle", "--scope", "named", "--order", "oldest")[1]
    assert newest["results"] == list(reversed(oldest["results"]))
    assert cli(repo, "id", "handle", "--scope", "named")[1]["results"]
    assert cli(repo, "index", "--status", "--scope", "named")[1]["generation"] == newest["generation"]
    ranged = cli(repo, "history", "HEAD^", "HEAD", "--scope", "named")[1]
    assert ranged["results"]
    assert ranged["generation"] == newest["generation"]
    from gita.index.query import open_index
    with CommittedRepo(repo.root) as repository:
        with open_index(repository, "named") as session:
            eid = session.resolve("handle")["results"][0]["eid"]
            occurrence = session.state_at(eid)["occurrence"]
            assert b"def handle" in session.source(occurrence)
    source = tools.source_tool(str(repo.root), occurrence, scope="named")
    assert source["results"][0]["content"].startswith("def handle")
    from gita.web.__main__ import DemoView
    view = DemoView(repo.root, scope="named")
    assert view.bootstrap()["legacy_enabled"] is False
    assert view.entity("app.py::handle", view.head)["source"]["results"][0]["content"].startswith("def handle")


@pytest.mark.parametrize("value", ["", "true", "false", "yes", "01", " 1", "2"])
def test_invalid_flag_is_not_silently_enabled_or_disabled(repo, monkeypatch, value):
    monkeypatch.setenv("GITA_ENABLE_LEGACY", value)
    code, result = cli(repo, "index", "--status")
    assert code == 2
    assert result["error"]["code"] == "invalid_configuration"
    assert "GITA_ENABLE_LEGACY" in result["error"]["message"]
    assert tools.history_tool(str(repo.root), entity="handle")["error"]["code"] == "invalid_configuration"


def test_zero_disables_and_one_restores_legacy(repo, monkeypatch):
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "0")
    assert cli(repo, "diff", "HEAD^", "HEAD")[1]["error"]["code"] == "legacy_disabled"
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "1")
    assert cli(repo, "diff", "HEAD^", "HEAD")[0] == 0
    assert tools.show_tool(str(repo.root), "app.py::Store::get")["patch"]
    assert cli(repo, "history", "handle", "--legacy", "--brief")[1]["events"]
    assert cli(repo, "index", "--workers", "1")[0] == 0
    assert cli(repo, "diff", "HEAD^", "HEAD", "--indexed")[0] == 0


@pytest.mark.parametrize("module,name,args", [
    ("gita.revisions", "diff_revisions", (None,)),
    ("gita.history", "series", (None,)),
    ("gita.history", "entity_history", (None, "handle")),
    ("gita.diff.differ", "diff_trees", (None, None)),
    ("gita.diff.differ", "diff_files", ([],)),
    ("gita.diff.differ", "reconcile_moves", ([],)),
    ("gita.context.patch", "entity_diff", (None, "a", "b", "app.py::handle")),
    ("gita.context.answer", "material_patch", (None, "a", "b", None)),
    ("gita.index.compare", "compare", (None, "a", "b")),
])
def test_direct_reconstruction_apis_require_opt_in(module, name, args):
    function = getattr(importlib.import_module(module), name)
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        function(*args)


def test_root_exports_lazy_and_gated_but_version_stable(monkeypatch):
    import gita

    assert gita.__version__ == "1.0.0"
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        getattr(gita, "diff_revisions")
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "1")
    assert callable(gita.diff_revisions)
    retained_extract = gita.extract_path
    monkeypatch.delenv("GITA_ENABLE_LEGACY")
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        getattr(gita, "diff_revisions")
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        retained_extract(b"def f(): pass", "a.py")
    from gita.entities.extractor import extract_path
    assert extract_path(b"def f(): pass", "a.py") is not None


def test_indexed_imports_do_not_load_legacy_review_pipeline():
    script = """
import sys
import gita
from gita.cli import _parser
from gita.cli import indexed
from gita.mcp import tools
from gita.web import __main__
from gita.index import builder, query
for name in ('gita.revisions', 'gita.diff.differ', 'gita.context.answer',
             'gita.context.patch', 'gita.context.layers', 'gita.history',
             'gita.index.compare'):
    assert name not in sys.modules, name
assert gita.__version__ == '1.0.0'
"""
    subprocess.run([sys.executable, "-c", script], check=True, env=dict(os.environ))


def test_mcp_discovery_follows_flag_and_handlers_recheck_it(repo, monkeypatch):
    pytest.importorskip("mcp")
    from gita.mcp.server import build_server

    def names(server):
        return {tool.name for tool in asyncio.run(server.list_tools())}

    default = build_server(str(repo.root))
    expected = {"gita_index", "gita_id", "gita_history", "gita_state", "gita_source", "gita_entities"}
    assert names(default) == expected
    assert set(tools.TOOLS) == expected
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "1")
    enabled = build_server(str(repo.root))
    assert names(enabled) == expected | {"gita_diff", "gita_show", "gita_expand"}
    assert set(tools.TOOLS) == names(enabled)
    monkeypatch.delenv("GITA_ENABLE_LEGACY")
    assert tools.diff_tool(str(repo.root))["error"]["code"] == "legacy_disabled"
    assert set(tools.TOOLS) == expected
    assert "legacy_disabled" in str(asyncio.run(enabled.call_tool("gita_diff", {})))
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "bad")
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY"):
        build_server(str(repo.root))


@pytest.mark.parametrize("route", ["/api/diff", "/api/patch", "/api/cli"])
def test_web_refuses_legacy_routes_before_arguments_or_cache(route):
    from gita.web.__main__ import DemoView

    view = object.__new__(DemoView)
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        if route == "/api/cli":
            view.cli("gita diff")
        else:
            view.api(route, {})


def test_web_http_refuses_legacy_routes_and_rechecks_cached_calls(monkeypatch):
    from gita.web.__main__ import DemoView, make_server

    view = object.__new__(DemoView)
    server = make_server(view, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for route in ("/api/diff", "/api/patch", "/api/cli"):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("POST" if route == "/api/cli" else "GET", route)
                response = connection.getresponse()
                assert response.status == 410
                assert json.loads(response.read())["error"]["code"] == "legacy_disabled"
            finally:
                connection.close()
        monkeypatch.setenv("GITA_ENABLE_LEGACY", "1")
        view.repo = "unused"
        monkeypatch.setattr("gita.web.__main__.diff_tool", lambda *a, **k: {"cached": True})
        assert view.compare("a", "b") == {"cached": True}
        monkeypatch.delenv("GITA_ENABLE_LEGACY")
        with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
            view.compare("a", "b")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("selector", ["--indexed", "--scope=default", "--order=oldest"])
def test_legacy_history_cannot_silently_select_indexed_semantics(repo, monkeypatch, selector):
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "1")
    code, result = cli(repo, "history", "handle", "--legacy", selector)
    assert code == 2
    assert result["error"]["code"] == "invalid_arguments"


def test_eval_refuses_before_side_effects(repo, monkeypatch):
    from gita.eval import main as evaluation
    from gita.eval.runner import ArmConfig, run_once
    from gita.eval.paired.runner import run_arm, run_pair

    directory = repo.root / "not-created"
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        run_once(None, ArmConfig.gita(), repo.root, directory, directory, directory, "unused")
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        run_arm({}, "gita", repo.root, directory, 1)
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY=1"):
        run_pair({}, directory, directory, 1)
    assert evaluation.main(["--tasks", str(directory / "missing.yaml")]) == 2
    assert not directory.exists()
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "bad")
    with pytest.raises(ValueError, match="GITA_ENABLE_LEGACY must"):
        run_once(None, ArmConfig.git(), repo.root, directory, directory, directory, "unused")
    assert not directory.exists()


def test_web_rejects_invalid_flag_before_creating_demo(monkeypatch, tmp_path, capsys):
    from gita.web import __main__ as web

    created = []
    monkeypatch.setenv("GITA_ENABLE_LEGACY", "true")
    monkeypatch.setattr(sys, "argv", ["gita.web", "--create-demo", "--repo", str(tmp_path / "demo")])
    monkeypatch.setattr(web, "create_demo", lambda path: created.append(path))
    with pytest.raises(SystemExit) as error:
        web.main()
    assert error.value.code == 1
    assert not created
    assert "GITA_ENABLE_LEGACY" in capsys.readouterr().err
