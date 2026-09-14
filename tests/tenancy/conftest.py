"""Tenant-isolation fixtures — the portable matrix, scoped to tenancy.

Relational legs mirror ``tests/orm/portable/conftest.py`` (SQLite always;
PostgreSQL / MySQL when the ``TEST_*_URL`` env vars are set). Every test
starts with a clean company context so a leaked contextvar from a previous
test cannot forge tenancy.
"""

from __future__ import annotations

import os

import pytest

BACKENDS: dict[str, str] = {"sqlite": "sqlite+aiosqlite:///:memory:"}
if os.environ.get("TEST_MYSQL_URL"):
    BACKENDS["mysql"] = os.environ["TEST_MYSQL_URL"]
if os.environ.get("TEST_POSTGRES_URL"):
    BACKENDS["postgresql"] = os.environ["TEST_POSTGRES_URL"]


@pytest.fixture(params=sorted(BACKENDS))
async def backend(request, monkeypatch):
    url = BACKENDS[request.param]
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", request.param)

    from fastplace.db import reset_db

    reset_db()
    yield request.param

    from fastplace.db import db

    await db.drop_all()
    await db.dispose()


@pytest.fixture(autouse=True)
def clean_company_context():
    """No test inherits (or leaks) a company binding."""
    from fastplace_tenancy.context import reset_company_context

    reset_company_context()
    yield
    reset_company_context()


@pytest.fixture(autouse=True)
def reset_model_registry():
    """Fresh metadata/mappers per test (mirrors tests/orm/conftest.py).

    The package's own model-bearing modules are evicted with the registry —
    ``context`` stays cached so every re-import shares one ContextVar.
    """
    import sys

    yield
    from sqlalchemy.orm import clear_mappers

    from fastplace.orm import Model

    Model.metadata.clear()
    clear_mappers()
    for name in [
        m
        for m in list(sys.modules)
        if m == "fastplace_tenancy" or m.startswith("fastplace_tenancy.")
    ]:
        if name != "fastplace_tenancy.context":
            del sys.modules[name]
