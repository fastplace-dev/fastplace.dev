"""Portable-suite fixtures — the backend matrix (blueprint §8).

Every module in ``tests/orm/portable/`` runs against in-memory SQLite
always, and against PostgreSQL / MySQL when ``TEST_POSTGRES_URL`` /
``TEST_MYSQL_URL`` are exported (CI sets them via service containers).
Dialect-specific behavior lives in the sibling ``postgresql/`` / ``mysql/``
/ ``sqlite/`` directories and never here.
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

    # Server backends persist between runs — leave the scratch database as
    # empty as the in-memory sqlite each test starts from.
    from fastplace.db import db

    await db.drop_all()
    await db.dispose()
