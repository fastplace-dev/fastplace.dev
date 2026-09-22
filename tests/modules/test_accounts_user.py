"""User model — hidden password, unique email, fillable surface (spec §5)."""

from __future__ import annotations

import datetime

import pytest


@pytest.fixture()
async def accounts_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    from app.modules.accounts.models.user import User
    from fastplace.db import db

    await db.create_all()
    return User


async def test_create_stores_fillable_fields(accounts_db):
    User = accounts_db
    user = await User.create(
        name="Firoz",
        email="firoz@example.test",
        password_hash="irrelevant-phc",
    )
    assert user.id is not None
    assert user.email_verified_at is None


async def test_password_hash_never_serializes(accounts_db):
    User = accounts_db
    user = await User.create(name="Firoz", email="h@example.test", password_hash="$scrypt$x")
    dumped = user.to_dict()
    assert "password_hash" not in dumped
    assert dumped["email"] == "h@example.test"


async def test_email_is_unique(accounts_db):
    from sqlalchemy.exc import IntegrityError

    User = accounts_db
    await User.create(name="A", email="dup@example.test", password_hash="x")
    with pytest.raises(IntegrityError):
        await User.create(name="B", email="dup@example.test", password_hash="y")


async def test_email_verified_at_roundtrips(accounts_db):
    User = accounts_db
    stamp = datetime.datetime(2026, 9, 22, tzinfo=datetime.UTC)
    user = await User.create(
        name="A", email="v@example.test", password_hash="x", email_verified_at=stamp
    )
    fresh = await User.find(user.id)
    assert fresh is not None
    # Portable contract: datetime columns load as naive UTC (blueprint §8),
    # so compare against the stamp's naive-UTC equivalent.
    assert fresh.email_verified_at == stamp.astimezone(datetime.UTC).replace(tzinfo=None)
