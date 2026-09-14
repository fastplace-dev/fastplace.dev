"""MySQL dialect specifics — utf8mb4, JSON columns, capability limits (T6.1).

Env-gated: exports ``TEST_MYSQL_URL=mysql://user:pass@host/db`` (scratch
database — the suite drops its tables on teardown). Without the variable
the whole module skips.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_MYSQL_URL"),
    reason="TEST_MYSQL_URL not set — MySQL dialect suite is opt-in",
)


@pytest.fixture()
async def my(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_MYSQL_URL"])
    monkeypatch.setenv("DATABASE_DRIVER", "mysql")

    from fastplace.db import reset_db

    reset_db()
    yield

    from fastplace.db import db

    await db.drop_all()
    await db.dispose()


@pytest.fixture()
def Row(my):
    from fastplace.orm import Field, Model

    class Row(Model):
        __tablename__ = "dialect_rows"

        id: int = Field(primary_key=True)
        title: str = ""
        payload: dict = Field(default_factory=dict)
        amount: Decimal = Field(default=Decimal("0.00"), precision=12, scale=2)

    return Row


async def test_utf8mb4_stores_emoji_and_bengali(my, Row):
    from fastplace.db import db

    await db.create_all()
    await Row.create(title="বাংলা 🚀 fastplace 🎉", payload={"note": "üñïçødé ✓"})

    fetched = await Row.first()
    assert fetched.title == "বাংলা 🚀 fastplace 🎉"
    assert fetched.payload["note"] == "üñïçødé ✓"


async def test_json_columns_round_trip_nested_structures(my, Row):
    from fastplace.db import db

    await db.create_all()
    await Row.create(title="json", payload={"tags": ["a", "b"], "nested": {"ok": True}})

    fetched = await Row.first()
    assert fetched.payload["tags"] == ["a", "b"]
    assert fetched.payload["nested"]["ok"] is True


async def test_decimal_columns_keep_precision(my, Row):
    from fastplace.db import db

    await db.create_all()
    await Row.create(title="money", amount=Decimal("12345678.90"))

    fetched = await Row.first()
    assert fetched.amount == Decimal("12345678.90")


async def test_mysql_capabilities_declare_no_returning_or_fts(my):
    from fastplace.db import db

    assert db.capabilities.driver == "mysql"
    assert db.capabilities.supports("returning") is False
    assert db.capabilities.supports("full_text") is False
    assert db.capabilities.supports("row_level_security") is False


async def test_last_insert_id_survives_without_returning(my, Row):
    # MySQL has no RETURNING clause — the ORM must fall back to
    # cursor.lastrowid so create() still yields a populated primary key.
    from fastplace.db import db

    await db.create_all()
    created = await Row.create(title="identity")
    assert created.id is not None

    fetched = await Row.find(created.id)
    assert fetched is not None and fetched.title == "identity"
