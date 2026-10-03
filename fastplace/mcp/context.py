"""Shared context handed to every MCP tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastplace.mcp.config import McpConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class McpContext:
    """Project root plus runtime switches; tools receive this, never globals.

    Database access goes through ``resolve_engine`` (default: the framework's
    manager) so tests can inject engines and callers can pin a connection.
    """

    root: Path
    config: McpConfig

    async def resolve_engine(self, name: str | None = None) -> AsyncEngine:
        from fastplace.db import db

        return db.manager.engine(name or "default")
