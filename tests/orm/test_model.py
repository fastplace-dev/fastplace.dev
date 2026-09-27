"""Model declaration + CRUD tests — the Fastplace ORM public API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from fastplace.db import db
from fastplace.orm import Field, Model


@pytest.fixture()
def User():  # noqa: N802 — fixture named like the class it builds
    class User(Model):
        __tablename__ = "users"

        id: int = Field(primary_key=True)
        name: str
        email: str = Field(unique=True)
        active: bool = True
        balance: Decimal = Field(default=Decimal("0.00"), precision=15, scale=2)
        meta: dict = Field(default_factory=dict)
        uid: uuid.UUID = Field(default_factory=uuid.uuid4)

    return User


@pytest.fixture()
async def created(User, db_url):
    await db.create_all()
    return User


async def test_tablename_convention(db_url):
    class InvoiceLine(Model):
        id: int = Field(primary_key=True)

    assert InvoiceLine.__tablename__ == "invoice_lines"


def test_redeclaring_a_tablename_replaces_the_declaration(db_url):
    """Re-executed model modules (in-process project boots, importer
    evictions) redeclare declarative classes whose tables are still
    registered. The superseded declaration is disposed — its table leaves
    the metadata, its mapper and string-lookup entry are released — and the
    new definition owns the name outright; a stale class lingering in the
    registry surfaces later as ``Multiple classes found for path``."""
    tag = uuid.uuid4().hex[:8]
    namespace = {
        "__tablename__": f"redeclared_posts_{tag}",
        "__annotations__": {"id": int, "title": str},
        "id": Field(primary_key=True),
    }
    first = type(f"Redeclared{tag}A", (Model,), dict(namespace))

    second = type(f"Redeclared{tag}B", (Model,), dict(namespace))

    assert Model.metadata.tables[second.__tablename__] is second.__table__
    # the superseded declaration is left fully unmapped — nothing behind it
    # to resolve ambiguously in the declarative registry
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.exc import NoInspectionAvailable

    with pytest.raises(NoInspectionAvailable):
        sa_inspect(first)
    Model.metadata.remove(second.__table__)


async def test_create_and_find_roundtrip(created):
    User = created
    user = await User.create(name="Firoz", email="firoz@example.com")
    assert user.id is not None

    found = await User.find(user.id)
    assert found is not None
    assert found.name == "Firoz"
    assert found.active is True
    assert found.balance == Decimal("0.00")
    assert isinstance(found.uid, uuid.UUID)


async def test_timestamps_auto_managed(created):
    user = await created.create(name="A", email="a@x.com")
    assert isinstance(user.created_at, datetime)
    assert isinstance(user.updated_at, datetime)


async def test_save_updates(created):
    User = created
    user = await User.create(name="A", email="a@x.com")
    user.name = "Updated"
    await user.save()

    fresh = await User.find(user.id)
    assert fresh.name == "Updated"


async def test_update_helper(created):
    user = await created.create(name="A", email="a@x.com")
    await user.update(name="B")
    assert user.name == "B"
    assert (await created.find(user.id)).name == "B"


async def test_delete_is_soft_by_default(created):
    user = await created.create(name="A", email="a@x.com")
    await user.delete()

    assert await created.find(user.id) is None
    assert await created.count() == 0
    # ...but the row is still there
    assert await created.with_deleted().count() == 1
    assert await created.only_deleted().count() == 1


async def test_restore_soft_deleted(created):
    User = created
    user = await User.create(name="A", email="a@x.com")
    await user.delete()

    deleted = (await User.only_deleted().get())[0]
    await deleted.restore()
    assert await User.count() == 1
    assert deleted.deleted_at is None


async def test_force_delete_removes_row(created):
    user = await created.create(name="A", email="a@x.com")
    await user.force_delete()
    assert await created.with_deleted().count() == 0


async def test_where_classmethod_first(created):
    User = created
    await User.create(name="A", email="a@x.com")
    await User.create(name="B", email="b@x.com")

    found = await User.where(User.email == "b@x.com").first()
    assert found is not None and found.name == "B"


async def test_unique_constraint_enforced(created):
    from sqlalchemy.exc import IntegrityError

    await created.create(name="A", email="dup@x.com")
    with pytest.raises(IntegrityError):
        await created.create(name="B", email="dup@x.com")


async def test_to_dict_json_safe(created):
    user = await created.create(name="A", email="a@x.com", meta={"k": 1})
    data = user.to_dict()
    assert data["name"] == "A"
    assert data["meta"] == {"k": 1}
    assert data["uid"] == str(user.uid)


async def test_refresh_reloads_from_database(created):
    user = await created.create(name="A", email="a@x.com")
    other = await created.find(user.id)
    other.name = "Changed"
    await other.save()

    user.name = "Stale"
    await user.refresh()
    assert user.name == "Changed"


async def test_utc_timestamps(created):
    before = datetime.now(UTC)
    user = await created.create(name="A", email="a@x.com")
    assert user.created_at.tzinfo is not None
    assert user.created_at >= before
