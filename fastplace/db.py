"""``db`` — the database facade (``from fastplace.db import db``).

Transaction scopes, capability registry, raw SQL, and named connections in
one object.
"""

from __future__ import annotations

from fastplace.orm.capabilities import Capabilities
from fastplace.orm.manager import DatabaseManager, reset_manager
from fastplace.orm.transactions import transaction


class Database:
    """Facade over the DatabaseManager — the developer-facing ``db`` object."""

    @property
    def manager(self) -> DatabaseManager:
        # Always resolve through the process singleton so facade objects
        # imported before a ``reset_db()`` stay coherent with fresh managers.
        from fastplace.orm.manager import get_manager

        return get_manager()

    def transaction(self, name: str = "default"):
        """``async with db.transaction():`` — commit or rollback as a unit."""
        return transaction(name)

    @property
    def capabilities(self) -> Capabilities:
        return self.manager.capabilities()

    async def create_all(self, name: str = "default") -> None:
        """Create every table currently registered on Model.metadata.

        Dev/test convenience (``db:reset``, first boot); Alembic remains the
        source of truth for schema changes in real projects.
        """
        from fastplace.orm.model import Model

        async with self.manager.engine(name).begin() as conn:
            await conn.run_sync(Model.metadata.create_all)

    async def drop_all(self, name: str = "default") -> None:
        """Drop every registered table (``db:reset``). Destructive — dev only."""
        from fastplace.orm.model import Model

        async with self.manager.engine(name).begin() as conn:
            await conn.run_sync(Model.metadata.drop_all)

    async def raw(
        self, sql: str, params: dict | None = None, name: str = "default"
    ) -> list[dict]:
        """Execute raw SQL (dialect-coupled escape hatch); returns row dicts.

        SELECT returns one dict per row; DML/DDL return ``[]`` and persist —
        standalone writes commit immediately, writes inside ``db.transaction()``
        commit with the scope.
        """
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession

        from fastplace.orm.session import ambient, session_scope

        statement = text(sql).bindparams(**(params or {}))

        async def _execute(session: AsyncSession) -> tuple[list[dict], bool]:
            result = await session.execute(statement)
            # CursorResult only carries returns_rows; Result's static type does not.
            returns = getattr(result, "returns_rows", False)
            rows = [dict(row._mapping) for row in result] if returns else []
            return rows, returns

        state = ambient()
        if state is not None:
            # The ambient scope is authoritative (same rule as ORM operations);
            # `name` only selects the connection when no scope is open.
            rows, returns = await _execute(state.session)
            if not returns and not state.owns_commit:
                await state.session.commit()
            return rows

        async with session_scope(name) as session:
            rows, returns = await _execute(session)
            if not returns and session.in_transaction():
                await session.commit()
            return rows

    def connection(self, name: str = "default"):
        """Context manager yielding an AsyncSession on a named connection.

        Unit-of-work scope: ORM writes inside commit (per write and on clean
        exit); an exception rolls back. Prefer ``db.transaction()`` when a
        block of work must be atomic.
        """
        from fastplace.orm.session import session_scope

        return session_scope(name)

    async def dispose(self) -> None:
        await self.manager.dispose()
        reset_manager()


db = Database()


def reset_db() -> None:
    """Reset the facade + manager singletons (tests, config reloads)."""
    global db
    db = Database()
    reset_manager()
