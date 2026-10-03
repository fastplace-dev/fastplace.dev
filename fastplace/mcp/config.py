"""MCP server configuration — env-driven, mirroring the framework's config style."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _env_int(env: dict[str, str], key: str, default: int, *, floor: int = 0) -> int:
    try:
        return max(floor, int(env[key].strip()))
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class McpConfig:
    """Runtime switches for the MCP server and its tools."""

    enabled: bool = True
    api_url: str = "https://docs.fastplace.dev"
    tool_timeout: int = 180
    tinker: bool = False
    rules: bool = True
    browser_logs: bool = True
    tools_exclude: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> McpConfig:
        env = dict(os.environ) if env is None else env

        return cls(
            enabled=_env_bool(env, "FASTPLACE_MCP_ENABLED", True),
            api_url=env.get("FASTPLACE_MCP_API_URL", "https://docs.fastplace.dev").strip()
            or "https://docs.fastplace.dev",
            tool_timeout=_env_int(env, "FASTPLACE_MCP_TOOL_TIMEOUT", 180, floor=1),
            tinker=_env_bool(env, "FASTPLACE_MCP_TINKER", False),
            rules=_env_bool(env, "FASTPLACE_MCP_RULES", True),
            browser_logs=_env_bool(env, "FASTPLACE_MCP_BROWSER_LOGS", True),
            tools_exclude=frozenset(
                part.strip()
                for part in env.get("FASTPLACE_MCP_TOOLS_EXCLUDE", "").split(",")
                if part.strip()
            ),
        )
