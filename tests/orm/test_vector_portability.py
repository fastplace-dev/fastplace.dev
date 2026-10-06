"""Vector columns must survive backend switches in one process.

The portable test lanes (and any process that configures the ORM twice)
define model classes under one ``DATABASE_URL`` and later ``create_all``
under another. Resolving the pgvector type at class-definition time bakes
``VECTOR(n)`` into the shared ``Model.metadata`` permanently — a MySQL or
SQLite ``create_all`` then dies on the dialect-foreign DDL. The column
type must resolve per-dialect at compile time instead.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
async def cross_backend():
    """Define a vector model while PostgreSQL is configured, then yield."""
    os.environ["DATABASE_URL"] = os.environ["TEST_POSTGRES_URL"]
    os.environ["DATABASE_DRIVER"] = "postgresql"
    from fastplace.db import reset_db

    reset_db()
    from fastplace.orm import Field, Model, VectorField

    class Probe(Model):
        __tablename__ = "vector_portability_probe"

        id: int = Field(primary_key=True)
        embedding: list[float] | None = VectorField(dimensions=3)

    yield Probe

    from fastplace.db import db

    await db.drop_all()
    await db.dispose()


@pytest.mark.parametrize(
    ("driver", "url_env"),
    [
        pytest.param(
            "mysql",
            "TEST_MYSQL_URL",
            marks=pytest.mark.skipif(
                not os.environ.get("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not exported"
            ),
        ),
        pytest.param("sqlite", None, id="sqlite"),
    ],
)
async def test_model_defined_on_postgres_creates_on_other_backends(
    cross_backend, monkeypatch, driver, url_env
):
    url = os.environ[url_env] if url_env else "sqlite+aiosqlite:///:memory:"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", driver)
    from fastplace.db import db, reset_db

    reset_db()
    # The leak, verbatim: shared Model.metadata still carries the Probe
    # table typed against the PostgreSQL configuration above.
    await db.create_all()

    created = await cross_backend.create(embedding=[0.1, 0.2, 0.3])
    fetched = await cross_backend.find(created.id)
    assert fetched is not None
    assert [round(v, 6) for v in fetched.embedding] == [0.1, 0.2, 0.3]
