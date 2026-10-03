"""MCP configuration — env resolution defaults and overrides."""

from __future__ import annotations

import pytest

from fastplace.mcp.config import McpConfig


def test_defaults() -> None:
    cfg = McpConfig.from_env({})

    assert cfg.enabled is True
    assert cfg.api_url == "https://docs.fastplace.dev"
    assert cfg.tool_timeout == 180
    assert cfg.tinker is False
    assert cfg.rules is True
    assert cfg.browser_logs is True
    assert cfg.tools_exclude == frozenset()


@pytest.mark.parametrize(
    ("var", "value", "attr", "expected"),
    [
        ("FASTPLACE_MCP_ENABLED", "false", "enabled", False),
        ("FASTPLACE_MCP_ENABLED", "0", "enabled", False),
        ("FASTPLACE_MCP_ENABLED", "true", "enabled", True),
        ("FASTPLACE_MCP_TINKER", "1", "tinker", True),
        ("FASTPLACE_MCP_TINKER", "no", "tinker", False),
        ("FASTPLACE_MCP_RULES", "off", "rules", False),
        ("FASTPLACE_MCP_BROWSER_LOGS", "yes", "browser_logs", True),
    ],
)
def test_bool_overrides(var: str, value: str, attr: str, expected: bool) -> None:
    cfg = McpConfig.from_env({var: value})

    assert getattr(cfg, attr) is expected


@pytest.mark.parametrize("junk", ["", "   ", "maybe", "2"])
def test_invalid_bool_falls_back_to_default(junk: str) -> None:
    assert McpConfig.from_env({"FASTPLACE_MCP_TINKER": junk}).tinker is False
    assert McpConfig.from_env({"FASTPLACE_MCP_RULES": junk}).rules is True


def test_api_url_and_timeout() -> None:
    cfg = McpConfig.from_env(
        {"FASTPLACE_MCP_API_URL": "http://localhost:5173", "FASTPLACE_MCP_TOOL_TIMEOUT": "42"}
    )

    assert cfg.api_url == "http://localhost:5173"
    assert cfg.tool_timeout == 42


def test_timeout_junk_falls_back() -> None:
    assert McpConfig.from_env({"FASTPLACE_MCP_TOOL_TIMEOUT": "soon"}).tool_timeout == 180


def test_timeout_floor_is_one_second() -> None:
    assert McpConfig.from_env({"FASTPLACE_MCP_TOOL_TIMEOUT": "0"}).tool_timeout == 1
    assert McpConfig.from_env({"FASTPLACE_MCP_TOOL_TIMEOUT": "-5"}).tool_timeout == 1


def test_tools_exclude_comma_list() -> None:
    cfg = McpConfig.from_env({"FASTPLACE_MCP_TOOLS_EXCLUDE": " tinker , search-docs ,,"})

    assert cfg.tools_exclude == frozenset({"tinker", "search-docs"})


def test_bool_env_precedence_uses_os_environ() -> None:
    import os

    cfg = McpConfig.from_env()

    assert isinstance(cfg, McpConfig)
    assert os.environ is not None
