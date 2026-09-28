"""Database safety and isolation for app test suites (audit supp-1-G3/G4).

Two layers:

* the plugin's session-wide pin binds ``DATABASE_URL`` to a throwaway
  database before any test runs, so *no* test — app fixture or not — can
  write the developer's real database;
* opt-in per-test modes: ``transactional_db`` (each test runs inside a
  transaction that is rolled back) and ``clean_db`` (fresh schema, wiped
  data).
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

#: Environment variable that overrides the automatic throwaway choice.
ENV_OVERRIDE = "FASTPLACE_TEST_DATABASE_URL"

#: ini option name — "auto" (default) | "off" | an explicit database URL.
INI_OPTION = "fastplace_test_database"


def resolve_test_database(config: Any, tmp_factory: Any) -> str | None:
    """Decide the session's database URL; ``None`` = the plugin steps aside.

    Precedence: ``FASTPLACE_TEST_DATABASE_URL`` env var, then the ini value
    (a URL pins it; ``off`` disables; ``auto`` mints a throwaway sqlite file
    under pytest's tmp tree).
    """
    env_url = os.environ.get(ENV_OVERRIDE)
    if env_url:
        return env_url
    ini = str(config.getini(INI_OPTION) or "auto").strip()
    if ini == "off":
        return None
    if ini and ini != "auto":
        return ini
    directory = tmp_factory.mktemp("fastplace-test-db")
    return f"sqlite+aiosqlite:///{directory / 'test.sqlite3'}"


def pin_environment(url: str) -> tuple[str | None, str | None]:
    """Point the framework at ``url``; returns the previous (url, driver)."""
    # Imported lazily: the plugin loads for every pytest run, and the orm
    # package (sqlalchemy) must not pay that cost until a fixture runs.
    from fastplace.db import reset_db
    from fastplace.orm.capabilities import driver_from_url

    previous = (os.environ.get("DATABASE_URL"), os.environ.get("DATABASE_DRIVER"))
    os.environ["DATABASE_URL"] = url
    os.environ["DATABASE_DRIVER"] = driver_from_url(url)
    reset_db()
    return previous


def unpin_environment(previous: tuple[str | None, str | None]) -> None:
    from fastplace.db import reset_db

    reset_db()
    url, driver = previous
    if url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = url
    if driver is None:
        os.environ.pop("DATABASE_DRIVER", None)
    else:
        os.environ["DATABASE_DRIVER"] = driver


def import_app_models() -> None:
    """Import ``app.models`` when present so its tables join the metadata.

    Apps with models elsewhere should import them from their conftest —
    table registration is an import side effect.
    """
    if importlib.util.find_spec("app") is None:
        return
    spec = importlib.util.find_spec("app.models")
    if spec is not None:
        importlib.import_module("app.models")


async def wipe_data() -> None:
    """DELETE every row, children first (FK-safe, dialect-portable)."""
    from fastplace.db import db
    from fastplace.orm import Model

    for table in reversed(Model.metadata.sorted_tables):
        await db.raw(f'DELETE FROM "{table.name}"')


class _IsolatedBehavior:
    """Transactional-test behavior mixed onto ``DatabaseManager``.

    Mixed in per call (see :func:`begin_transactional`) so this module never
    imports sqlalchemy at plugin-load time. Every session — read and write —
    is bound to one held connection, so the public ``db`` facade and every
    repository/service/model call keep working, now inside the transaction.
    """

    def __init__(self, base: Any) -> None:
        from typing import cast

        from fastplace.orm.manager import DatabaseManager

        # cast: the dynamic subclass in _make_isolated_manager IS a
        # DatabaseManager (mixin + base), but this module's stub self type
        # is the mixin alone.
        DatabaseManager.__init__(
            cast("DatabaseManager", self),
            {name: dict(cfg) for name, cfg in base.connections.items()},
        )
        self._conn: Any = None
        self._isolated_factories: dict[str, Any] = {}

    def bind(self, conn: Any) -> None:
        """Route every session (read and write) through ``conn``."""
        self._conn = conn
        self._isolated_factories.clear()

    def _isolated_factory(self, name: str = "default") -> Any:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        if name not in self._isolated_factories:
            self._isolated_factories[name] = async_sessionmaker(
                bind=self._conn,
                class_=AsyncSession,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
                autoflush=False,
            )
        return self._isolated_factories[name]

    def session(self, name: str = "default") -> Any:
        return self._isolated_factory(name)()

    def read_session(self, name: str = "default") -> Any:
        # No replicas under transactional isolation: reads join the same
        # transaction so a test sees its own writes.
        return self._isolated_factory(name)()


def _make_isolated_manager(base: Any) -> Any:
    """Build the ``DatabaseManager`` subclass carrying the isolated behavior.

    The mixin comes FIRST in the MRO so its ``session``/``read_session``
    shadow the base's engine-bound factories; ``engine`` and friends still
    resolve from :class:`DatabaseManager`.
    """
    from fastplace.orm.manager import DatabaseManager

    return type("_IsolatedManager", (_IsolatedBehavior, DatabaseManager), {})(base)


async def begin_transactional(base: Any) -> tuple[Any, Any]:
    """Swap in a manager whose every session shares one open transaction.

    Returns ``(manager, held)`` — ``held`` is ``(conn, transaction, engine,
    previous)`` for :func:`end_transactional`. The schema is created inside
    the transaction too, so a rollback leaves the database file exactly as
    it was.
    """
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from fastplace.orm import Model
    from fastplace.orm import manager as manager_module

    isolated = _make_isolated_manager(base)
    # A dedicated NullPool engine owns the held connection: the isolated
    # manager's pooled engine stays free for other uses, and nothing can
    # deadlock on the single pooled slot the held connection would occupy.
    url = str(base.engine().url)
    kwargs: dict[str, Any] = {"poolclass": NullPool}
    if url.startswith("sqlite"):
        # Python's sqlite3 (and aiosqlite) auto-commit before DDL and
        # RELEASE the outermost savepoint as a full COMMIT — both would
        # leak data out of the held transaction. The documented recipe:
        # hand the driver explicit control and let SQLAlchemy emit BEGIN
        # itself (aiosqlite's sync-adapted connection takes exec_driver_sql).
        kwargs["connect_args"] = {"isolation_level": None}
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "begin")
        def _emit_begin(conn: Any) -> None:
            conn.exec_driver_sql("BEGIN")

    conn = await engine.connect()
    transaction = await conn.begin()

    def _create_all_sync(sync_conn: Any) -> None:
        Model.metadata.create_all(sync_conn)

    await conn.run_sync(_create_all_sync)
    isolated.bind(conn)

    previous = manager_module._manager
    manager_module._manager = isolated  # noqa: SLF001 — the swap IS the isolation
    return isolated, (conn, transaction, engine, previous)


async def end_transactional(isolated: Any, held: tuple[Any, ...]) -> None:
    from fastplace.orm import manager as manager_module

    conn, transaction, engine, previous = held
    await transaction.rollback()
    await conn.close()
    manager_module._manager = previous  # noqa: SLF001
    await isolated.dispose()
    await engine.dispose()


__all__ = [
    "ENV_OVERRIDE",
    "INI_OPTION",
    "begin_transactional",
    "end_transactional",
    "import_app_models",
    "pin_environment",
    "resolve_test_database",
    "unpin_environment",
    "wipe_data",
]
