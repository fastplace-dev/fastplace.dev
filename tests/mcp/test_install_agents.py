"""Agent registry: 13 Boost-parity agents, their paths, keys, formats."""

from __future__ import annotations

from pathlib import Path

from fastplace.mcp.install.agents import AGENTS, get_agent, specs_for


def test_thirteen_agents_registered():
    assert len(AGENTS) == 13
    assert {a.key for a in AGENTS} == {
        "claude_code",
        "cursor",
        "codex",
        "copilot",
        "zed",
        "amp",
        "antigravity",
        "opencode",
        "factory",
        "grok_build",
        "junie",
        "kiro",
        "pi",
    }


def test_claude_code_paths():
    agent = get_agent("claude_code")
    assert agent.mcp_config_path == ".mcp.json"
    assert agent.mcp_config_key == "mcpServers"
    assert agent.guidelines_path == "AGENTS.md"
    assert agent.skills_path == ".claude/skills"
    assert agent.project_detect == (".claude",)


def test_pi_has_no_mcp_surface():
    agent = get_agent("pi")
    assert agent.mcp_config_path is None
    assert agent.guidelines_path == "AGENTS.md"
    assert agent.skills_path == ".pi/skills"


def test_toml_agents():
    for key in ("codex", "grok_build"):
        agent = get_agent(key)
        assert agent.toml is True
        assert agent.mcp_config_key == "mcp_servers"
    assert get_agent("codex").mcp_config_path == ".codex/config.toml"
    assert get_agent("grok_build").mcp_config_path == ".grok/config.toml"


def test_alternative_config_keys():
    assert get_agent("copilot").mcp_config_key == "servers"
    assert get_agent("zed").mcp_config_key == "context_servers"
    assert get_agent("amp").mcp_config_key == "amp.mcpServers"
    assert get_agent("opencode").mcp_config_key == "mcp"


def test_opencode_is_jsonc_with_special_shape():
    agent = get_agent("opencode")
    assert agent.jsonc is True
    assert agent.mcp_config_path in ("opencode.json", "opencode.jsonc")
    assert agent.server_config == "opencode"


def test_get_agent_unknown_key_raises():
    import pytest

    with pytest.raises(KeyError):
        get_agent("nope")


def test_specs_for_resolves_names_and_defaults_to_detected(tmp_path: Path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()

    resolved = specs_for(None, project=tmp_path)

    assert [a.key for a in resolved] == ["claude_code", "codex"]

    explicit = specs_for(["pi"], project=tmp_path)
    assert [a.key for a in explicit] == ["pi"]


def test_specs_for_empty_detection_is_empty(tmp_path: Path):
    assert specs_for(None, project=tmp_path) == []
