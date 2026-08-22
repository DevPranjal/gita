"""WS-6 MCP tools. The transport is thin; these functions are the contract."""

from __future__ import annotations

import pytest

from gita.mcp.tools import (
    TOOLS,
    diff_tool,
    expand_tool,
    show_tool,
)


class TestDiffTool:
    def test_returns_a_complete_answer_and_entity_ids(self, repo):
        result = diff_tool(str(repo.root))
        assert result["answer"]
        assert any("handle" in c["id"] for c in result["changes"])

    def test_answer_includes_code_so_no_second_call_is_needed(self, repo):
        assert "@@" in diff_tool(str(repo.root))["answer"]

    def test_respects_budget(self, repo):
        assert diff_tool(str(repo.root), budget=40)["tokens"] <= 40

    def test_no_next_steps_when_nothing_was_cut(self, repo):
        result = diff_tool(str(repo.root), budget=40000)
        if not result["truncated"]:
            assert result["next"] == []

    def test_reports_noise_it_removed(self, repo):
        assert diff_tool(str(repo.root))["noise_filtered"] > 0


class TestExpandTool:
    def test_lists_children(self, repo):
        result = expand_tool(str(repo.root), "app.py::Store")
        assert any("get" in line for line in result["lines"])

    def test_unknown_entity_reports_an_error_not_an_exception(self, repo):
        assert "error" in expand_tool(str(repo.root), "app.py::nope")


class TestShowTool:
    def test_returns_a_patch(self, repo):
        result = show_tool(str(repo.root), "app.py::Store::get")
        assert "self.data[key]" in result["patch"]

    def test_reports_its_cost(self, repo):
        assert show_tool(str(repo.root), "app.py::Store::get")["tokens"] > 0

    def test_unknown_entity_reports_an_error(self, repo):
        assert "error" in show_tool(str(repo.root), "app.py::nope")


class TestFiltering:
    def test_filter_narrows_changes(self, repo):
        result = diff_tool(str(repo.root), filter="handle")
        assert "handle" in result["answer"]
        assert "Store::put" not in result["answer"]

    def test_interface_only_is_exact(self, repo):
        result = diff_tool(str(repo.root), interface_only=True)
        assert "error" not in result


class TestFailureModes:
    @pytest.mark.parametrize("tool,args", [
        (diff_tool, ()),
    ])
    def test_non_repository_reports_an_error(self, tmp_path, tool, args):
        assert "error" in tool(str(tmp_path), *args)

    def test_bad_revision_reports_an_error(self, repo):
        assert "error" in diff_tool(str(repo.root), base="nope123")


class TestServer:
    """Wiring only -- the contract itself is covered above."""

    @pytest.fixture
    def server(self, repo):
        pytest.importorskip("mcp", reason="MCP SDK not installed")
        from gita.mcp.server import build_server

        return build_server(str(repo.root))

    def test_registers_every_tool(self, server):
        import asyncio

        names = {t.name for t in asyncio.run(server.list_tools())}
        assert names == set(TOOLS)

    def test_every_tool_is_described(self, server):
        import asyncio

        assert all(t.description for t in asyncio.run(server.list_tools()))

    def test_instructions_point_at_the_cheap_layer_first(self):
        from gita.mcp.server import SERVER_INSTRUCTIONS

        assert SERVER_INSTRUCTIONS.index("gita_diff") < \
               SERVER_INSTRUCTIONS.index("gita_show")
