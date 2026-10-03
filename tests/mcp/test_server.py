"""Server assembly: tool registration, gates, excludes, prompt, resource."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import pytest

from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.server import SERVER_NAME, build_server


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (tmp_path / "storage" / "logs").mkdir(parents=True)
    return tmp_path


def _ctx(project: Path, **config_kwargs) -> McpContext:
    return McpContext(root=project, config=McpConfig(**config_kwargs))


async def test_server_name_and_default_tool_set(project):
    server = build_server(_ctx(project))

    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert server.name == SERVER_NAME == "fastplace-aibrain"
    # Clients render serverInfo on connect — the running framework's own
    # version, not the SDK's empty default.
    assert server.version == version("fastplace")
    assert names == {
        "application-info",
        "database-connections",
        "database-schema",
        "database-query",
        "read-log-entries",
        "last-error",
        "get-absolute-url",
        "search-docs",
        "browser-logs",
        "record-rule",
    }


async def test_read_only_tools_carry_readonly_hint(project):
    server = build_server(_ctx(project, tinker=True))

    tools = {tool.name: tool for tool in await server.list_tools()}

    assert tools["application-info"].annotations.read_only_hint is True
    assert tools["database-query"].annotations.read_only_hint is True
    # record-rule and tinker carry no annotations at all.
    assert tools["record-rule"].annotations is None
    assert tools["tinker"].annotations is None


async def test_tinker_hidden_when_disabled(project):
    server = build_server(_ctx(project))  # tinker default False

    names = {tool.name for tool in await server.list_tools()}
    assert "tinker" not in names


async def test_tinker_registered_when_enabled(project):
    server = build_server(_ctx(project, tinker=True))

    names = {tool.name for tool in await server.list_tools()}
    assert "tinker" in names


async def test_rules_disabled_hides_record_rule(project):
    server = build_server(_ctx(project, rules=False))

    names = {tool.name for tool in await server.list_tools()}
    assert "record-rule" not in names


async def test_browser_logs_disabled_hides_tool(project):
    server = build_server(_ctx(project, browser_logs=False))

    names = {tool.name for tool in await server.list_tools()}
    assert "browser-logs" not in names


async def test_tools_exclude_removes_named_tools(project):
    server = build_server(_ctx(project, tools_exclude=frozenset({"search-docs", "last-error"})))

    names = {tool.name for tool in await server.list_tools()}
    assert "search-docs" not in names
    assert "last-error" not in names
    assert "application-info" in names


async def test_prompts_and_resources_exposed(project):
    server = build_server(_ctx(project))

    prompts = await server.list_prompts()
    assert {p.name for p in prompts} == {"fastplace-code-simplifier"}

    resources = await server.list_resources()
    assert {str(r.uri) for r in resources} == {"fastplace://application-info"}
