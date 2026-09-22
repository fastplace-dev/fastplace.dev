"""session_store factory + kernel integration of the server session stack."""

from __future__ import annotations

import pytest

from fastplace.errors import ConfigurationError


def _cfg(overrides: dict):
    def get(key: str, default=None):
        return overrides.get(key, default)

    return get


def test_explicit_driver_wins_over_environment():
    from fastplace.http.session import session_store
    from fastplace.http.session.database import DatabaseSessionStore

    store = session_store(config_get=_cfg({"SESSION_DRIVER": "database", "APP_ENV": "local"}))
    assert isinstance(store, DatabaseSessionStore)


def test_production_defaults_to_database():
    from fastplace.http.session import session_store
    from fastplace.http.session.database import DatabaseSessionStore

    store = session_store(config_get=_cfg({"APP_ENV": "production"}))
    assert isinstance(store, DatabaseSessionStore)


def test_local_defaults_to_memory():
    from fastplace.http.session import MemorySessionStore, session_store

    store = session_store(config_get=_cfg({"APP_ENV": "local"}))
    assert isinstance(store, MemorySessionStore)


def test_redis_driver_builds():
    from fastplace.http.session import RedisSessionStore, session_store

    store = session_store(config_get=_cfg({"SESSION_DRIVER": "redis"}))
    assert isinstance(store, RedisSessionStore)


def test_unknown_driver_is_a_configuration_error():
    from fastplace.http.session import session_store

    with pytest.raises(ConfigurationError):
        session_store(config_get=_cfg({"SESSION_DRIVER": "carrier-pigeon"}))


async def test_get_app_sessions_roundtrip_through_the_store():
    from fastplace.http.kernel import get_app
    from fastplace.http.router import Router

    async def handler(request):
        count = request.session.get("hits", 0) + 1
        request.session["hits"] = count
        from fastplace.http.response import Json

        return Json({"hits": count})

    router = Router()
    router.get("/count", handler)
    app = get_app(routes=router, config={"APP_ENV": "local", "APP_KEY": "test-key"})

    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/count")
        second = await client.get("/count", cookies=first.cookies)
    assert first.json() == {"hits": 1}
    assert second.json() == {"hits": 2}  # cookie -> store -> hydrate works end to end
