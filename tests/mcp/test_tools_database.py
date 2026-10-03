"""Database-facing MCP tools: connections list, schema, read-only query.

Engines are injected so tests run hermetically against a temp SQLite file —
no global manager or config state is touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.tools.database import (
    build_database_connections,
    build_database_query,
    build_database_schema,
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    return tmp_path


@pytest.fixture()
def ctx(project: Path) -> McpContext:
    return McpContext(root=project, config=McpConfig())


@pytest.fixture()
async def engine(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"))
        await conn.execute(text("INSERT INTO items (name) VALUES ('a'), ('b')"))
    yield engine
    await engine.dispose()


def _manager(monkeypatch, tmp_path: Path):
    from fastplace.orm.manager import DatabaseManager

    connections: dict[str, dict[str, Any]] = {
        "default": {"driver": "sqlite", "url": f"sqlite+aiosqlite:///{tmp_path}/t.db"},
        "analytics": {"driver": "sqlite", "url": f"sqlite+aiosqlite:///{tmp_path}/a.db"},
    }
    return DatabaseManager(connections)


async def test_database_connections_lists_named_connections(ctx, monkeypatch, tmp_path):
    manager = _manager(monkeypatch, tmp_path)

    handle = build_database_connections(ctx, manager=manager)
    result = json.loads(await handle())

    assert result["default_connection"] == "default"
    assert set(result["connections"]) == {"default", "analytics"}


async def test_database_schema_overview(ctx, engine):
    handle = build_database_schema(ctx, engine_resolver=lambda name: engine)
    result = await handle()

    assert "items" in result
    assert "driver: sqlite" in result


async def test_database_schema_single_table(ctx, engine):
    handle = build_database_schema(ctx, engine_resolver=lambda name: engine)

    detail = await handle(table="items")
    assert "name" in detail
    assert "TEXT" in detail.upper()

    missing = await handle(table="nope")
    assert "nope" in missing.lower()


async def test_database_query_runs_select(ctx, engine):
    handle = build_database_query(ctx, engine_resolver=lambda name: engine)

    out = await handle(query="SELECT * FROM items ORDER BY id")

    assert "a" in out and "b" in out


async def test_database_query_rejects_writes(ctx, engine):
    handle = build_database_query(ctx, engine_resolver=lambda name: engine)

    with pytest.raises(ValueError, match="read-only"):
        await handle(query="DELETE FROM items")


async def test_database_query_names_connection(ctx, engine, monkeypatch, tmp_path):
    seen: list[str | None] = []

    def resolver(name: str | None):
        seen.append(name)
        return engine

    handle = build_database_query(ctx, engine_resolver=resolver)
    await handle(query="SELECT 1", database="analytics")

    assert seen == ["analytics"]
