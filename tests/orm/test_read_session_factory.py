"""read_session() builds one sessionmaker per resolved engine, not per call.

Mirrors session_factory()'s caching: the factory is constructed once per
engine object and reused, instead of a fresh async_sessionmaker on every
call.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import fastplace.orm.manager as manager_module
from fastplace.orm.manager import DatabaseManager


@pytest.fixture()
def built_factories(monkeypatch):
    """Count async_sessionmaker constructions inside fastplace.orm.manager."""
    real = manager_module.async_sessionmaker
    engines: list[object] = []

    def counting(*args, **kwargs):
        engines.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(manager_module, "async_sessionmaker", counting)
    return engines


def _manager(replicas: tuple[str, ...] = ()) -> DatabaseManager:
    connection: dict = {"driver": "sqlite", "url": "sqlite+aiosqlite:///:memory:"}
    if replicas:
        connection["replicas"] = list(replicas)
    return DatabaseManager({"default": connection})


def test_read_session_returns_a_real_session(built_factories):
    assert isinstance(_manager().read_session(), AsyncSession)


def test_repeated_read_sessions_reuse_one_factory(built_factories):
    manager = _manager()
    manager.read_session()
    manager.read_session()
    manager.read_session()
    assert len(built_factories) == 1


def test_replica_rotation_reuses_one_factory_per_engine(built_factories):
    # Round-robin alternates between the two replica engines (the primary is
    # not in the read cycle once replicas exist): three reads touch engine,
    # engine, engine — two engine objects, so two factories, not three.
    manager = _manager(replicas=("sqlite+aiosqlite:///:memory:",) * 2)
    manager.read_session()
    manager.read_session()
    manager.read_session()
    assert len(built_factories) == 2


def test_factory_cache_follows_the_engine_lifetime(built_factories):
    manager = _manager()
    manager.read_session()
    manager.read_session()
    assert len(built_factories) == 1
    manager._dispose_sync()  # engines dropped — the next read starts fresh
    manager.read_session()
    assert len(built_factories) == 2
