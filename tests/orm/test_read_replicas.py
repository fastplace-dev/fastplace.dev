"""Read-replica groundwork — routing config, round-robin, sticky primary.

Blueprint Phase 6: read/write connection routing with read-after-write
consistency — reads that follow a write in the same request stay on the
primary. Two sqlite files stand in for primary/replica: only the primary
ever gets the schema, so mis-routing fails loudly.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import OperationalError


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch, tmp_path):
    from fastplace.db import reset_db
    from fastplace.orm.session import reset_primary_pin

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/primary.db")
    reset_db()
    reset_primary_pin()
    yield
    reset_db()
    reset_primary_pin()


def test_replica_urls_from_env_are_parsed_and_normalized(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_READ_REPLICAS",
        json.dumps(["mysql://u:p@h/replica1", "mysql://u:p@h/replica2"]),
    )
    from fastplace.db import reset_db
    from fastplace.orm.manager import get_manager

    reset_db()
    cfg = get_manager().config_for("default")
    assert cfg["replicas"] == [
        "mysql+asyncmy://u:p@h/replica1",
        "mysql+asyncmy://u:p@h/replica2",
    ]


def test_comma_separated_replica_string_is_accepted(monkeypatch):
    monkeypatch.setenv("DATABASE_READ_REPLICAS", "sqlite:///a.db, sqlite:///b.db")
    from fastplace.db import reset_db
    from fastplace.orm.manager import get_manager

    reset_db()
    cfg = get_manager().config_for("default")
    assert cfg["replicas"] == ["sqlite+aiosqlite:///a.db", "sqlite+aiosqlite:///b.db"]


def test_read_engine_round_robins_across_replicas():
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager(
        {
            "default": {
                "driver": "sqlite",
                "url": "sqlite+aiosqlite:///:memory:",
                "replicas": [
                    "sqlite+aiosqlite:///:memory:",
                    "sqlite+aiosqlite:///:memory:",
                ],
            }
        }
    )
    seen = [manager.read_engine() for _ in range(4)]
    assert len(set(seen)) == 2  # alternates across both replicas
    assert manager.engine() not in seen  # and never the primary


def test_without_replicas_reads_use_the_primary():
    from fastplace.orm.manager import DatabaseManager

    manager = DatabaseManager(
        {"default": {"driver": "sqlite", "url": "sqlite+aiosqlite:///:memory:"}}
    )
    assert manager.read_engine() is manager.engine()


async def test_reads_route_to_replica_until_a_write_pins_the_primary(monkeypatch, tmp_path):
    from fastplace.db import db, reset_db
    from fastplace.orm import Field, Model
    from fastplace.orm.session import reads_pinned_to_primary

    # a second scratch file as the replica — it never receives the schema
    monkeypatch.setenv("DATABASE_READ_REPLICAS", json.dumps([f"sqlite:///{tmp_path}/replica.db"]))
    reset_db()

    class Row(Model):
        __tablename__ = "replica_rows"

        id: int = Field(primary_key=True)
        title: str = ""

    await db.create_all()  # schema lands on the primary file only

    # Before any write: reads route to the replica, whose file has no table.
    assert reads_pinned_to_primary() is False
    with pytest.raises(OperationalError):
        await Row.count()

    # A standalone write pins this context to the primary…
    await Row.create(title="one")
    assert reads_pinned_to_primary() is True

    # …so the read that follows sees the row it just wrote (read-after-write).
    assert await Row.count() == 1
