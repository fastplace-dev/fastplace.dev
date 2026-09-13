"""Transactions, model events, capabilities, escape hatches."""

from __future__ import annotations

import pytest

from fastplace.db import db
from fastplace.errors import SearchCapabilityMissing
from fastplace.orm import Field, Model


@pytest.fixture()
def Account(db_url):  # noqa: N802 — fixture named like the class it builds
    class Account(Model):
        __tablename__ = "accounts"

        id: int = Field(primary_key=True)
        name: str
        balance: int = 100

    return Account


@pytest.fixture()
async def schema(Account):
    await db.create_all()
    return Account


async def test_transaction_commits_on_success(schema):
    Account = schema
    async with db.transaction():
        await Account.create(name="a1", balance=10)
        await Account.create(name="a2", balance=20)
    assert await Account.count() == 2


async def test_transaction_rolls_back_on_error(schema):
    Account = schema
    with pytest.raises(RuntimeError):
        async with db.transaction():
            await Account.create(name="a1")
            raise RuntimeError("fail")
    assert await Account.count() == 0


async def test_nested_transaction_savepoint(schema):
    Account = schema
    async with db.transaction():
        await Account.create(name="outer")
        with pytest.raises(ValueError):
            async with db.transaction():
                await Account.create(name="inner-bad")
                raise ValueError("inner fail")
        await Account.create(name="inner-ok")
    names = sorted(a.name for a in await Account.all())
    assert names == ["inner-ok", "outer"]


async def test_model_lifecycle_events(schema):
    Account = schema
    calls: list[str] = []

    Account.on("creating", lambda m: calls.append("creating"))
    Account.on("created", lambda m: calls.append("created"))
    Account.on("updating", lambda m: calls.append("updating"))
    Account.on("updated", lambda m: calls.append("updated"))
    Account.on("deleting", lambda m: calls.append("deleting"))
    Account.on("deleted", lambda m: calls.append("deleted"))
    Account.on("restored", lambda m: calls.append("restored"))

    account = await Account.create(name="e")
    await account.update(name="e2")
    await account.delete()
    deleted = (await Account.only_deleted().get())[0]
    await deleted.restore()

    assert calls == [
        "creating",
        "created",
        "updating",
        "updated",
        "deleting",
        "deleted",
        "restored",
    ]


async def test_async_event_handlers_supported(schema):
    Account = schema
    seen: list[str] = []

    async def on_created(model):
        seen.append(model.name)

    Account.on("created", on_created)
    await Account.create(name="async-handler")
    assert seen == ["async-handler"]


async def test_event_exception_aborts_operation(schema):
    Account = schema

    def bail(model):
        raise ValueError("nope")

    Account.on("creating", bail)
    with pytest.raises(ValueError):
        await Account.create(name="blocked")
    assert await Account.count() == 0


# ---------------------------------------------------------------------------
# Capabilities + escape hatches
# ---------------------------------------------------------------------------


async def test_sqlite_capability_flags(db_url):
    assert db.capabilities.supports_transactions is True
    assert db.capabilities.supports_json is True
    assert db.capabilities.supports_vector is False
    assert db.capabilities.supports_full_text is False
    assert db.capabilities.supports_rls is False


async def test_vector_search_raises_on_sqlite(db_url):
    class Doc(Model):
        __tablename__ = "docs"

        id: int = Field(primary_key=True)
        title: str
        embedding: list[float] = Field(type="vector", dimensions=4)

    await db.create_all()
    await Doc.create(title="t", embedding=[0.1, 0.2, 0.3, 0.4])
    with pytest.raises(SearchCapabilityMissing):
        await Doc.vector_search([0.1, 0.2, 0.3, 0.4])


async def test_full_text_search_raises_on_sqlite(db_url):
    class Doc2(Model):
        __tablename__ = "docs2"

        id: int = Field(primary_key=True)
        title: str

    await db.create_all()
    await Doc2.create(title="hello world")
    with pytest.raises(SearchCapabilityMissing):
        await Doc2.full_text_search("hello")


async def test_sa_model_escape_hatch(Account):
    assert Account.sa_model is not None
    assert hasattr(Account.sa_model, "__table__")


async def test_db_raw(schema):
    Account = schema
    await Account.create(name="raw-target")
    rows = await db.raw("SELECT name FROM accounts")
    assert rows == [{"name": "raw-target"}]


async def test_named_connection_config():
    from fastplace.orm.manager import DatabaseManager

    mgr = DatabaseManager({"default": {"url": "sqlite+aiosqlite:///:memory:"}})
    engine = mgr.engine("default")
    assert engine is not None
    assert mgr.engine("default") is engine  # cached
    await mgr.dispose()
