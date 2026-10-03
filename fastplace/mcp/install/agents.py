"""Agent registry — the 13 AI-agent integrations ``fastplace mcp install`` writes.

Mirrors the agent matrix of the MCP-install machinery this module ports:
each agent declares its project-detection markers, its MCP config file
(relative to the project root), the JSON/TOML key the servers live under,
where guidelines (AGENTS.md) go, and where skills are synced. Agents with
``mcp_config_path=None`` (``pi``) get guidelines + skills only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentSpec:
    key: str
    display: str
    project_detect: tuple[str, ...] = ()
    mcp_config_path: str | None = None
    mcp_config_key: str = "mcpServers"
    guidelines_path: str | None = "AGENTS.md"
    skills_path: str | None = None
    toml: bool = False
    jsonc: bool = False
    server_config: str = "stdio"  # "stdio" flat payload | "opencode" payload
    default_config: tuple[tuple[str, str], ...] = field(default=())


AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        key="claude_code",
        display="Claude Code",
        project_detect=(".claude",),
        mcp_config_path=".mcp.json",
        skills_path=".claude/skills",
    ),
    AgentSpec(
        key="cursor",
        display="Cursor",
        project_detect=(".cursor",),
        mcp_config_path=".cursor/mcp.json",
        skills_path=".cursor/skills",
    ),
    AgentSpec(
        key="codex",
        display="Codex",
        project_detect=(".codex",),
        mcp_config_path=".codex/config.toml",
        mcp_config_key="mcp_servers",
        toml=True,
        skills_path=".agents/skills",
    ),
    AgentSpec(
        key="copilot",
        display="GitHub Copilot",
        project_detect=(".vscode",),
        mcp_config_path=".vscode/mcp.json",
        mcp_config_key="servers",
        skills_path=".github/skills",
    ),
    AgentSpec(
        key="zed",
        display="Zed",
        project_detect=(".zed",),
        mcp_config_path=".zed/settings.json",
        mcp_config_key="context_servers",
        skills_path=".agents/skills",
    ),
    AgentSpec(
        key="amp",
        display="Amp",
        project_detect=(".amp",),
        mcp_config_path=".amp/settings.json",
        mcp_config_key="amp.mcpServers",
        skills_path=".agents/skills",
    ),
    AgentSpec(
        key="antigravity",
        display="Antigravity",
        project_detect=(".gemini",),
        mcp_config_path=".agents/mcp_config.json",
        skills_path=".agents/skills",
    ),
    AgentSpec(
        key="opencode",
        display="OpenCode",
        project_detect=("opencode.json", "opencode.jsonc"),
        mcp_config_path="opencode.jsonc",
        mcp_config_key="mcp",
        jsonc=True,
        server_config="opencode",
        default_config=(("$schema", "https://opencode.ai/config.json"),),
        skills_path=".agents/skills",
    ),
    AgentSpec(
        key="factory",
        display="Factory",
        project_detect=(".factory",),
        mcp_config_path=".factory/mcp.json",
        skills_path=".factory/skills",
    ),
    AgentSpec(
        key="grok_build",
        display="Grok Build",
        project_detect=(".grok",),
        mcp_config_path=".grok/config.toml",
        mcp_config_key="mcp_servers",
        toml=True,
        skills_path=".grok/skills",
    ),
    AgentSpec(
        key="junie",
        display="Junie",
        project_detect=(".junie", ".idea"),
        mcp_config_path=".junie/mcp/mcp.json",
        skills_path=".junie/skills",
    ),
    AgentSpec(
        key="kiro",
        display="Kiro",
        project_detect=(".kiro",),
        mcp_config_path=".kiro/settings/mcp.json",
        skills_path=".kiro/skills",
    ),
    AgentSpec(
        key="pi",
        display="Pi",
        project_detect=(".pi",),
        mcp_config_path=None,
        skills_path=".pi/skills",
    ),
)


def get_agent(key: str) -> AgentSpec:
    for spec in AGENTS:
        if spec.key == key:
            return spec
    raise KeyError(f"Unknown agent: {key!r}")


def detect_installed(project: Path) -> list[str]:
    """Agent keys with a project-level marker present, registry order."""
    found = []
    for spec in AGENTS:
        if any((project / marker).exists() for marker in spec.project_detect):
            found.append(spec.key)
    return found


def specs_for(keys: list[str] | None, *, project: Path) -> list[AgentSpec]:
    """Explicit keys in the order given, or every detected agent."""
    if keys is None:
        return [get_agent(key) for key in detect_installed(project)]
    return [get_agent(key) for key in keys]
