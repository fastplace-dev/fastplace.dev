"""Dialect URL binding — bare schemes map to the async driver of record.

Developers write ``DATABASE_URL=mysql://user:pass@host/db``; the manager
normalizes it to the async dialect (``mysql+asyncmy``) so engine creation
works without spelling the driver by hand (T6.1/T6.2).
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("bare", "bound"),
    [
        ("mysql://u:p@localhost/app", "mysql+asyncmy://u:p@localhost/app"),
        ("postgresql://u:p@localhost/app", "postgresql+asyncpg://u:p@localhost/app"),
        ("postgres://u:p@localhost/app", "postgresql+asyncpg://u:p@localhost/app"),
        ("sqlite:///./db.sqlite3", "sqlite+aiosqlite:///./db.sqlite3"),
    ],
)
def test_bare_schemes_bind_to_the_async_driver(bare: str, bound: str):
    from fastplace.orm.manager import normalize_database_url

    assert normalize_database_url(bare) == bound


def test_explicit_driver_suffixes_are_left_alone():
    from fastplace.orm.manager import normalize_database_url

    assert (
        normalize_database_url("mysql+asyncmy://u:p@localhost/app")
        == "mysql+asyncmy://u:p@localhost/app"
    )
    assert (
        normalize_database_url("postgresql+asyncpg://u@localhost/app")
        == "postgresql+asyncpg://u@localhost/app"
    )


def test_unknown_schemes_pass_through_untouched():
    from fastplace.orm.manager import normalize_database_url

    # mongodb:// lives behind the document adapter, not the relational
    # manager — normalization must not guess at a dialect for it
    assert (
        normalize_database_url("mongodb://localhost:27017/app") == "mongodb://localhost:27017/app"
    )


@pytest.mark.parametrize(
    ("mixed", "bound"),
    [
        ("MySQL://u:p@localhost/app", "mysql+asyncmy://u:p@localhost/app"),
        ("PostgreSQL://u:p@localhost/app", "postgresql+asyncpg://u:p@localhost/app"),
        ("SQLite:///./db.sqlite3", "sqlite+aiosqlite:///./db.sqlite3"),
    ],
)
def test_mixed_case_schemes_still_bind(mixed: str, bound: str):
    from fastplace.orm.manager import normalize_database_url

    # developers paste URLs from dashboards — casing must not opt them out
    # of driver binding
    assert normalize_database_url(mixed) == bound


def test_capabilities_resolve_mixed_case_schemes():
    from fastplace.orm.capabilities import driver_from_url

    assert driver_from_url("MySQL://u:p@localhost/app") == "mysql"
    assert driver_from_url("Postgres://u:p@localhost/app") == "postgresql"


def test_manager_config_normalizes_the_url_and_driver():
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager({"default": {"driver": "mysql", "url": "mysql://u:p@localhost/app"}})
    assert manager.config_for("default")["url"] == "mysql+asyncmy://u:p@localhost/app"
    assert manager.capabilities().driver == "mysql"
    assert manager.capabilities().supports("returning") is False


def test_mysql_engine_is_created_with_connection_liveness_pings(monkeypatch):
    # MySQL kills idle connections after wait_timeout; the engine must ping
    # pooled connections on checkout instead of handing out dead sockets.
    pytest.importorskip("asyncmy", reason="mysql driver lives in an optional extra")
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager(
        {"default": {"driver": "mysql", "url": "mysql+asyncmy://u:p@localhost/app"}}
    )
    engine = manager.engine("default")
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]


def test_sqlite_memory_keeps_single_connection_pool(monkeypatch):
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager(
        {"default": {"driver": "sqlite", "url": "sqlite+aiosqlite:///:memory:"}}
    )
    engine = manager.engine("default")
    assert engine.pool.size() == 1
    assert engine.pool._max_overflow == 0  # type: ignore[attr-defined]


def test_postgresql_engine_honors_pool_tuning():
    """Pool sizing applies to every server backend, not MySQL alone.

    The charset commit nested the pool-key loop inside the mysql-only
    branch — a PostgreSQL deployment then silently fell back to
    SQLAlchemy defaults (5+10) whatever DATABASE_POOL_SIZE said.
    """
    pytest.importorskip("asyncpg", reason="postgresql driver lives in an optional extra")
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager(
        {
            "default": {
                "driver": "postgresql",
                "url": "postgresql+asyncpg://u:p@localhost/app",
                "pool_size": 7,
                "max_overflow": 4,
                "pool_timeout": 11,
                "pool_recycle": 600,
            }
        }
    )
    engine = manager.engine("default")
    assert engine.pool.size() == 7
    assert engine.pool._max_overflow == 4  # type: ignore[attr-defined]
    assert engine.pool._timeout == 11  # type: ignore[attr-defined]
    assert engine.pool._recycle == 600  # type: ignore[attr-defined]
