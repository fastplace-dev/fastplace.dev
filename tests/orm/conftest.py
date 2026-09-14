"""Shared ORM test fixtures — in-memory SQLite per test."""

from __future__ import annotations

import pytest

from fastplace.orm import Model


@pytest.fixture()
def db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = "sqlite+aiosqlite:///:memory:"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    return url


@pytest.fixture(autouse=True)
def reset_db_singleton():
    """Isolate the db facade between tests."""
    from fastplace.db import reset_db

    reset_db()
    yield
    reset_db()


@pytest.fixture(autouse=True)
def reset_model_registry():
    """Give every test a clean metadata/mapper registry.

    Models declared inside tests register on the shared Model metadata; we
    dispose mappers after each test so re-declared test models never clash.
    Cached project modules (app.*, seeder/config loaders from tmp projects)
    are dropped too — each test imports from its own project tree.

    sys.path is restored as well: the CLI's model discovery inserts the
    (tmp) project root at import time and never removes it — fine for a
    real process that exits, a leak in this long-lived pytest process.
    Left on, a later suite's fresh ``import app`` (the entries above were
    dropped) resolves the LAST tmp project instead of the repo and fails
    with ``ModuleNotFoundError: No module named 'app.modules.knowledge'``.
    """
    import sys

    saved_path = list(sys.path)
    yield
    Model.metadata.clear()
    from sqlalchemy.orm import clear_mappers

    clear_mappers()
    for name in [
        m
        for m in list(sys.modules)
        if m == "app" or m.startswith(("app.", "_fastplace_seeder_", "_fastplace_config_"))
    ]:
        del sys.modules[name]
    sys.path[:] = saved_path
