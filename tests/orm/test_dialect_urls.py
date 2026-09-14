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


def test_manager_config_normalizes_the_url_and_driver():
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager({"default": {"driver": "mysql", "url": "mysql://u:p@localhost/app"}})
    assert manager.config_for("default")["url"] == "mysql+asyncmy://u:p@localhost/app"
    assert manager.capabilities().driver == "mysql"
    assert manager.capabilities().supports("returning") is False


def test_mysql_engine_is_created_with_connection_liveness_pings(monkeypatch):
    # MySQL kills idle connections after wait_timeout; the engine must ping
    # pooled connections on checkout instead of handing out dead sockets.
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
