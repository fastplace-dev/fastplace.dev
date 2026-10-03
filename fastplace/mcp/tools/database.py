"""Database-facing tools: ``database-connections``, ``database-schema``,
``database-query``.

Read-only enforcement lives in :mod:`fastplace.mcp.safety.sql_readonly`; the
tools only wire schemas, formatting, and engine resolution together.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastplace.mcp.context import McpContext
from fastplace.mcp.safety.sql_readonly import ReadOnlyViolation, run_read_only_select

EngineResolver = Callable[[str | None], Any]


async def _resolve(resolver: EngineResolver, name: str | None) -> Any:
    engine = resolver(name)
    if _isawaitable(engine):
        engine = await engine
    return engine


def _isawaitable(value: Any) -> bool:
    import inspect

    return inspect.isawaitable(value)


def _default_resolver(ctx: McpContext) -> EngineResolver:
    async def resolve(name: str | None) -> Any:
        return await ctx.resolve_engine(name)

    return resolve


def build_database_connections(
    ctx: McpContext, *, manager: Any = None
) -> Callable[[], Awaitable[str]]:
    """List configured connection names and the default one."""

    async def database_connections() -> str:
        if manager is None:
            from fastplace.db import db

            resolved = db.manager
        else:
            resolved = manager
        return json.dumps(
            {
                "default_connection": ("default" if "default" in resolved.connections else None),
                "connections": sorted(resolved.connections),
            },
            indent=2,
        )

    return database_connections


def _format_table_detail(detail: Any) -> str:
    lines = [
        f"## {detail.name}",
        "",
        "| column | type | nullable | pk | fk |",
        "|---|---|---|---|---|",
    ]
    for column in detail.columns:
        lines.append(
            f"| {column.name} | {column.type} "
            f"| {'yes' if column.nullable else 'no'} "
            f"| {'yes' if column.primary_key else 'no'} "
            f"| {', '.join(column.foreign_keys) or '—'} |"
        )
    if detail.indexes:
        lines += ["", "### indexes"]
        for index in detail.indexes:
            uniq = " UNIQUE" if index.unique else ""
            lines.append(f"- {index.name}{uniq} ({', '.join(index.columns)})")
    return "\n".join(lines)


def build_database_schema(
    ctx: McpContext,
    *,
    engine_resolver: EngineResolver | None = None,
) -> Callable[..., Awaitable[str]]:
    """Schema overview, or one table's columns/indexes when ``table`` given."""
    resolve = engine_resolver or _default_resolver(ctx)

    async def database_schema(table: str | None = None) -> str:
        engine = await _resolve(resolve, None)
        if table:
            detail = await _describe_on(engine, table)
            if detail is None:
                return f"Table '{table}' not found."
            return _format_table_detail(detail)

        overview = await _overview_on(engine)
        lines = [
            f"driver: {overview['driver']}",
            f"database: {overview['database'] or '(memory)'}",
            "",
            "| table |",
            "|---|",
        ]
        lines += [f"| {name} |" for name in overview["tables"]]
        return "\n".join(lines)

    return database_schema


async def _overview_on(engine: Any) -> dict[str, Any]:
    from sqlalchemy import inspect

    def _collect(sync_conn):
        inspector = inspect(sync_conn)
        return {
            "driver": sync_conn.dialect.name,
            "database": sync_conn.engine.url.database,
            "tables": sorted(inspector.get_table_names()),
        }

    conn = await engine.connect()
    try:
        return await conn.run_sync(_collect)
    finally:
        await conn.close()


async def _describe_on(engine: Any, table: str) -> Any:

    # describe_table reads through the process manager; run the same
    # inspector logic on the injected engine instead.
    from sqlalchemy import inspect

    def _describe(sync_conn):
        inspector = inspect(sync_conn)
        if table not in inspector.get_table_names():
            return None
        from fastplace.db_inspection import ColumnDetail, IndexDetail, TableDetail

        pk = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
        references: dict[str, set[str]] = {}
        for fk in inspector.get_foreign_keys(table):
            referred = str(fk.get("referred_table") or "")
            for column in fk.get("constrained_columns") or []:
                references.setdefault(str(column), set()).add(referred)
        columns = [
            ColumnDetail(
                name=str(column["name"]),
                type=str(column["type"]),
                nullable=bool(column.get("nullable", True)),
                primary_key=str(column["name"]) in pk,
                foreign_keys=tuple(sorted(references.get(str(column["name"]), ()))),
            )
            for column in inspector.get_columns(table)
        ]
        indexes = [
            IndexDetail(
                name=str(index.get("name") or "(unnamed)"),
                columns=tuple(str(c) for c in index.get("column_names") or ()),
                unique=bool(index.get("unique")),
            )
            for index in inspector.get_indexes(table)
        ]
        return TableDetail(name=table, columns=columns, indexes=indexes)

    conn = await engine.connect()
    try:
        return await conn.run_sync(_describe)
    finally:
        await conn.close()


def build_database_query(
    ctx: McpContext,
    *,
    engine_resolver: EngineResolver | None = None,
) -> Callable[..., Awaitable[str]]:
    """Run a read-only SQL query through both guard layers."""
    resolve = engine_resolver or _default_resolver(ctx)

    async def database_query(query: str, database: str | None = None) -> str:
        engine = await _resolve(resolve, database)
        try:
            rows = await run_read_only_select(engine, query)
        except ReadOnlyViolation as exc:
            raise ValueError(str(exc)) from exc

        if not rows:
            return "Query executed successfully — 0 rows."

        header = " | ".join(rows[0].keys())
        separator = " | ".join("-" * len(str(k)) for k in rows[0].keys())
        body = "\n".join(" | ".join(_cell(row.get(key)) for key in rows[0].keys()) for row in rows)
        return f"{header}\n{separator}\n{body}\n\n({len(rows)} rows)"

    return database_query


def _cell(value: Any) -> str:
    if value is None:
        return "NULL"
    return str(value)
