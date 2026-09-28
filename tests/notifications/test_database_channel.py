"""The database built-in — to_database() rows in the framework-owned table.

Real sqlite file per test (the cache-store pattern): the channel is
Core-only and must stay portable across SQLite/PG/MySQL.
"""

from __future__ import annotations

from typing import Any

from fastplace.notifications import (
    Notification,
    NotificationStore,
    notifiable_key,
    reset_channels,
    send,
)


class Account:
    def __init__(self, id: int) -> None:
        self.id = id


class OrderShipped(Notification):
    def via(self, notifiable: Any) -> list[str]:
        return ["database"]

    def to_database(self, notifiable: Any) -> dict:
        return {"title": "Order shipped", "order_id": str(notifiable.id)}


async def test_database_channel_records_a_row():
    record = (await send(Account(7), OrderShipped()))[0]
    assert record["notifiable_type"] == "Account"
    assert record["notifiable_id"] == "7"
    assert record["payload"] == {"title": "Order shipped", "order_id": "7"}
    assert record["read_at"] is None
    assert record["id"]
    assert record["created_at"]


async def test_mark_read_and_read_helpers():
    first = (await send(Account(1), OrderShipped()))[0]
    await send(Account(1), OrderShipped())
    store = NotificationStore()

    unread = await store.read(notifiable_type="Account", notifiable_id="1")
    assert len(unread) == 2
    assert all(row["read_at"] is None for row in unread)

    assert await store.mark_read(first["id"]) is True
    # Idempotent: re-marking an already-read row flips nothing.
    assert await store.mark_read(first["id"]) is False

    still_unread = await store.read(notifiable_type="Account", notifiable_id="1")
    assert len(still_unread) == 1
    assert still_unread[0]["id"] != first["id"]
    everything = await store.read(notifiable_type="Account", notifiable_id="1", unread_only=False)
    assert len(everything) == 2
    assert {row["read_at"] is None for row in everything} == {True, False}  # one read, one not


async def test_mark_read_scopes_to_the_notifiable_when_given():
    mine = (await send(Account(1), OrderShipped()))[0]
    theirs = (await send(Account(2), OrderShipped()))[0]
    store = NotificationStore()

    # Scoped to Account/1, a client-supplied id for Account/2 flips nothing.
    assert (
        await store.mark_read(theirs["id"], notifiable_type="Account", notifiable_id="1") is False
    )
    assert len(await store.read(notifiable_type="Account", notifiable_id="2")) == 1

    # The owning notifiable's scope still flips its row.
    assert await store.mark_read(mine["id"], notifiable_type="Account", notifiable_id="1") is True

    # Back-compat: the unscoped single-argument form still flips any row.
    other = (await send(Account(3), OrderShipped()))[0]
    assert await store.mark_read(other["id"]) is True


async def test_notifiables_are_isolated_by_type_and_id():
    class Invoice:
        def __init__(self, id: int) -> None:
            self.id = id

    await send(Account(1), OrderShipped())
    await send(Invoice(1), OrderShipped())
    store = NotificationStore()
    assert len(await store.read(notifiable_type="Account", notifiable_id="1")) == 1
    assert len(await store.read(notifiable_type="Invoice", notifiable_id="1")) == 1
    assert await store.read(notifiable_type="Account", notifiable_id="2") == []


async def test_table_create_is_idempotent_across_stores():
    """checkfirst=True: a second store (or a reset registry) never re-creates."""
    await send(Account(1), OrderShipped())
    await NotificationStore().record(
        notifiable_type="Account", notifiable_id="1", payload={"title": "manual"}
    )
    reset_channels()  # fresh built-ins => fresh store on the same database
    record = (await send(Account(1), OrderShipped()))[0]
    store = NotificationStore()
    rows = await store.read(notifiable_type="Account", notifiable_id="1", unread_only=False)
    assert len(rows) == 3
    assert {row["payload"]["title"] for row in rows} == {
        "Order shipped",
        "manual",
    }
    assert record["payload"]["title"] == "Order shipped"


async def test_payload_is_json_safe_through_storage():
    record = await NotificationStore().record(
        notifiable_type="Account",
        notifiable_id="9",
        payload={"title": "Digest", "meta": {"count": 3, "nested": {"ok": True}}},
    )
    store = NotificationStore()
    rows = await store.read(notifiable_type="Account", notifiable_id="9")
    assert rows[0]["payload"] == record["payload"]


def test_notifiable_key_coerces_to_strings():
    assert notifiable_key(Account(12)) == ("Account", "12")
    assert notifiable_key(object()) == ("object", "")
