"""The Fastplace pytest plugin — safe databases and app fixtures, zero config.

Loaded automatically once Fastplace is installed (pytest11 entry point).
Safety first: unless configured otherwise, every test session runs against
a throwaway sqlite database — a test can never write the developer's real
database, whether or not it boots the app. Configure with the
``fastplace_test_database`` ini value (``auto`` | ``off`` | a database URL)
or the ``FASTPLACE_TEST_DATABASE_URL`` environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from fastplace.testing.database import (
    INI_OPTION,
    begin_transactional,
    end_transactional,
    import_app_models,
    pin_environment,
    resolve_test_database,
    unpin_environment,
    wipe_data,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        INI_OPTION,
        help="database tests run against: auto (throwaway sqlite), off, or a URL",
        default="auto",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "fastplace_models(*modules): model modules the isolation fixtures "
        "(transactional_db, clean_db) must import before building the schema "
        "— for apps whose models live outside app/models.",
    )


def _import_marked_models(request: pytest.FixtureRequest) -> None:
    """Import every module named by the test's ``fastplace_models`` markers.

    Runs at fixture setup, before the schema is built, so tables declared
    in nonstandard modules (scaffolded apps keep them under
    ``app/modules/<name>/models/``) join the metadata in time.
    """
    import importlib

    for marker in request.node.iter_markers("fastplace_models"):
        for module_name in marker.args:
            importlib.import_module(str(module_name))


@pytest.fixture(scope="session")
def _fastplace_test_db(request: pytest.FixtureRequest, tmp_path_factory: Any):
    """Session-wide test database; yields the pinned URL or ``None`` (off)."""
    url = resolve_test_database(request.config, tmp_path_factory)
    if url is None:
        yield None
        return
    previous = pin_environment(url)
    yield url
    unpin_environment(previous)


@pytest.fixture(autouse=True)
def _fastplace_db_guard(_fastplace_test_db):
    """Drop cached managers/metadata around every pinned test."""
    if _fastplace_test_db is None:
        yield
        return
    from fastplace.db import reset_db

    reset_db()
    yield
    reset_db()


@pytest.fixture()
async def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, request: pytest.FixtureRequest):
    """The real application over the test database (minimal-project shape).

    Imports ``routes/web.py`` (required) and ``routes/{auth,api,ai}.py``
    (optional), plus ``app/models`` when present. Scaffolded apps bring
    their own richer ``app``/``client`` fixtures in ``tests/conftest.py`` —
    those override these by name.
    """
    import importlib.util

    from fastplace.orm.capabilities import driver_from_url

    root = request.config.rootpath
    monkeypatch.syspath_prepend(str(root))
    url = os.environ.get("DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", driver_from_url(url))
    from fastplace.db import db, reset_db

    reset_db()

    if importlib.util.find_spec("routes.web") is None:
        raise RuntimeError(
            "the app fixture imports routes/web.py — create it (or use the "
            "conftest `fastplace new` emits, which defines its own fixtures)"
        )
    web_router = importlib.import_module("routes.web").router
    auth_router = _optional_router("routes.auth")
    api_router = _optional_router("routes.api")
    ai_router = _optional_router("routes.ai")

    import_app_models()
    await db.create_all()

    from fastplace.http import get_app

    middleware = None
    if (root / "config").is_dir():
        from fastplace.http.kernel import middleware_from_config

        middleware = middleware_from_config(root)
    return get_app(
        routes=web_router,
        auth_routes=auth_router,
        api_routes=api_router,
        ai_routes=ai_router,
        middleware=middleware,
        config={"APP_ENV": "local"},
        project_root=root,
    )


def _optional_router(module_name: str) -> Any:
    import importlib.util

    if importlib.util.find_spec(module_name) is None:
        return None
    return importlib.import_module(module_name).router


@pytest.fixture()
async def client(app):
    """Async HTTP client over ``app``; responses are fluent ``TestResponse``s."""
    import httpx

    from fastplace.testing.client import TestClient

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with TestClient(transport=transport, base_url="http://test") as async_client:
        token: list[str | None] = [None]

        async def attach_csrf(request: httpx.Request) -> None:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and token[0]:
                request.headers.setdefault("X-Fastplace-CSRF-Token", token[0])

        async def capture_csrf(response: httpx.Response) -> None:
            fresh = response.headers.get("X-Fastplace-CSRF-Token")
            if fresh:
                token[0] = fresh

        async_client.event_hooks["request"].append(attach_csrf)
        async_client.event_hooks["response"].append(capture_csrf)
        yield async_client


@pytest.fixture()
def mail(monkeypatch: pytest.MonkeyPatch):
    """Memory-driver mail with assertions (``assert_sent``, ``assert_sent_to``)."""
    from fastplace.mail.transports import clear_mail_outbox
    from fastplace.testing.mail import FakeMail

    monkeypatch.setenv("MAIL_DRIVER", "memory")
    clear_mail_outbox()
    yield FakeMail()
    clear_mail_outbox()


@pytest.fixture()
def queue_fake():
    """The in-memory queue plus push/run assertions, installed process-wide."""
    from fastplace.queue import reset_queue, set_queue
    from fastplace.testing.queue import FakeQueue

    fake = FakeQueue()
    set_queue(fake)
    yield fake
    reset_queue()


@pytest.fixture()
def events_fake(monkeypatch: pytest.MonkeyPatch):
    """Dispatches are recorded instead of delivered (listeners/queue skipped)."""
    import fastplace.events as events_module
    from fastplace.testing.events import FakeEvents

    fake = FakeEvents()
    monkeypatch.setattr(events_module, "dispatch", fake.record)
    yield fake


@pytest.fixture()
def notifications():
    """Notification fan-out is recorded per channel leg; nothing is delivered."""
    from fastplace.testing.notifications import FakeNotifications

    fake = FakeNotifications()
    fake.install()
    yield fake
    fake.restore()


@pytest.fixture()
def storage_fake(monkeypatch: pytest.MonkeyPatch):
    """The process-wide ``disk()`` becomes a dict-backed FakeStorage."""
    import fastplace.storage as storage_module
    from fastplace.testing.storage import FakeStorage

    fake = FakeStorage()

    class _FakeRegistry(storage_module.Storage):
        def disk(self, name: str | None = None) -> Any:
            return fake

    monkeypatch.setattr(storage_module, "_default_storage", _FakeRegistry(disks={}, default="fake"))
    yield fake


@pytest.fixture()
def http_fake():
    """An injectable fake HTTP client: stub with ``respond``, assert on requests."""
    from fastplace.testing.http import FakeHttp

    yield FakeHttp()


@pytest.fixture()
def clock():
    """Freeze and travel through time (``pip install 'fastplace[testing]'``)."""
    try:
        from freezegun import freeze_time
    except ImportError as exc:  # pragma: no cover - exercised via hint text
        from fastplace.errors import ConfigurationError

        raise ConfigurationError(
            "the clock fixture needs freezegun — install it with: pip install 'fastplace[testing]'"
        ) from exc
    from fastplace.testing.clock import Clock

    handle = Clock(freeze_time)
    yield handle
    handle.stop()


@pytest.fixture()
async def transactional_db(request: pytest.FixtureRequest, _fastplace_test_db):
    """Every ORM write joins one transaction, rolled back after the test.

    Repository/service/model tests get full speed and perfect isolation.
    Not for the ``app`` fixture — the held connection owns the pool.
    Models outside ``app/models`` join the schema via the
    ``fastplace_models`` marker.
    """
    from fastplace.orm import manager as manager_module

    import_app_models()
    _import_marked_models(request)
    base = manager_module.get_manager()
    isolated, held = await begin_transactional(base)
    yield isolated
    await end_transactional(isolated, held)


@pytest.fixture()
async def clean_db(request: pytest.FixtureRequest, _fastplace_test_db):
    """A fresh schema whose data is wiped around every test.

    Models outside ``app/models`` join the schema via the
    ``fastplace_models`` marker.
    """
    from fastplace.db import db

    import_app_models()
    _import_marked_models(request)
    await db.create_all()
    await wipe_data()
    yield
    await wipe_data()
