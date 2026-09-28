"""Daily-use persistence helpers — locate-or-write, batch upsert, counters, iteration.

The helpers application code reaches for every day: ``first_or_create`` /
``update_or_create`` (race-safe locate-or-write), ``upsert`` (portable batch),
``increment``/``decrement`` (DB-side arithmetic), and the streaming trio
``chunk`` / ``chunk_by_id`` / ``cursor``. The race tests simulate a competing
writer deterministically — hiding the row from exactly one locate-SELECT —
because the sqlite test pool serializes connections (manager.py: pool_size 1),
so two tasks can never interleave for real here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fastplace.db import db
from fastplace.errors import MassAssignmentError
from fastplace.orm import Field, Model


@pytest.fixture()
def User():  # noqa: N802 — fixture named like the class it builds
    class User(Model):
        __tablename__ = "users"

        id: int = Field(primary_key=True)
        name: str
        email: str = Field(unique=True)
        views: int = 0

    return User


@pytest.fixture()
async def created(User, db_url):
    await db.create_all()
    return User


@pytest.fixture()
def Task():  # noqa: N802
    class Task(Model):
        __tablename__ = "tasks"

        id: int = Field(primary_key=True)
        title: str
        priority: int = 0

    return Task


@pytest.fixture()
async def seeded(Task, db_url):
    await db.create_all()
    for i, title in enumerate(["write tests", "design orm", "ship v1", "write docs", "review pr"]):
        await Task.create(title=title, priority=i)
    return Task


def _hide_first_lookup_once(monkeypatch: pytest.MonkeyPatch, model: type) -> None:
    """Make the NEXT locate-SELECT on ``model`` return None.

    Simulates a competing writer committing the row between this task's
    locate and its INSERT — the only way to interleave deterministically on
    the serialized sqlite test pool.
    """
    from fastplace.orm.query import QueryBuilder

    real_first = QueryBuilder.first
    armed = {"active": True}

    async def raced_first(self: Any) -> Any:
        if armed["active"] and self.model is model:
            armed["active"] = False
            return None
        return await real_first(self)

    monkeypatch.setattr(QueryBuilder, "first", raced_first)


# ---------------------------------------------------------------------------
# first_or_create
# ---------------------------------------------------------------------------


async def test_first_or_create_inserts_when_absent(created):
    User = created
    row = await User.first_or_create({"name": "fallback"}, email="a@x.com")
    assert row.id is not None and row.name == "fallback"
    assert await User.count() == 1


async def test_first_or_create_locates_instead_of_duplicating(created):
    User = created
    first = await User.first_or_create({"name": "fallback"}, email="a@x.com")
    second = await User.first_or_create({"name": "other"}, email="a@x.com")
    assert second.id == first.id
    assert await User.count() == 1


async def test_first_or_create_defaults_never_overwrite_found_row(created):
    User = created
    first = await User.first_or_create({"name": "fallback"}, email="a@x.com")
    first.name = "chosen"
    await first.save()

    second = await User.first_or_create({"name": "fallback"}, email="a@x.com")
    assert second.name == "chosen"


async def test_first_or_create_wins_the_race(created, monkeypatch):
    """A writer that committed between our locate and our INSERT is returned.

    The INSERT hits the unique key, rolls back just the savepoint, and the
    row is re-fetched — exactly one row lands and the winner comes back.
    """
    User = created
    winner = await User.create(name="winner", email="race@x.com")
    _hide_first_lookup_once(monkeypatch, User)

    row = await User.first_or_create({"name": "loser"}, email="race@x.com")
    assert row.id == winner.id
    assert row.name == "winner"
    assert await User.count() == 1


async def test_first_or_create_race_inside_transaction(created, monkeypatch):
    """The race savepoint rolls back without sinking the enclosing transaction."""
    User = created
    async with db.transaction():
        first = await User.first_or_create({"name": "tx"}, email="tx@x.com")
        _hide_first_lookup_once(monkeypatch, User)

        again = await User.first_or_create({"name": "second"}, email="tx@x.com")
        assert again.id == first.id
        assert await User.count() == 1

    assert await User.count() == 1


async def test_first_or_create_soft_deleted_unique_row_fails_loudly(created):
    """A tombstoned row still owns its unique key — surface the conflict.

    Resurrecting it silently (or updating it as a side effect of a lookup)
    would hide data loss; the caller decides between restore() and a new key.
    """
    User = created
    user = await User.create(name="gone", email="gone@x.com")
    await user.delete()

    with pytest.raises(IntegrityError):
        await User.first_or_create({"name": "new"}, email="gone@x.com")


async def test_first_or_create_honors_mass_assignment_guard(created):
    User = created
    with pytest.raises(MassAssignmentError):
        await User.first_or_create({"updated_at": "nope"}, email="g@x.com")
    assert await User.count() == 0


# ---------------------------------------------------------------------------
# update_or_create
# ---------------------------------------------------------------------------


async def test_update_or_create_inserts_when_absent(created):
    User = created
    row = await User.update_or_create({"name": "v1"}, email="u@x.com")
    assert row.name == "v1"
    assert await User.count() == 1


async def test_update_or_create_updates_found_row(created):
    User = created
    await User.update_or_create({"name": "v1"}, email="u@x.com")
    again = await User.update_or_create({"name": "v2"}, email="u@x.com")
    assert again.name == "v2"
    assert await User.count() == 1


async def test_update_or_create_wins_the_race(created, monkeypatch):
    """Raced inserts resolve to the concurrent row, then defaults apply."""
    User = created
    winner = await User.create(name="winner", email="race@x.com")
    _hide_first_lookup_once(monkeypatch, User)

    row = await User.update_or_create({"name": "patched"}, email="race@x.com")
    assert row.id == winner.id
    assert row.name == "patched"
    assert await User.count() == 1


async def test_update_or_create_rejects_empty_attrs(created):
    """Empty attrs would match an arbitrary row and rewrite it with defaults.

    first_or_create's degenerate empty-attrs case only ever reads or
    inserts; update_or_create would WRITE — so the destructive variant is
    refused and the caller states what to locate.
    """
    User = created
    await User.create(name="A", email="a@x.com")
    with pytest.raises(ValueError):
        await User.update_or_create({"name": "rewritten"})
    row = await User.query().first()
    assert row.name == "A"


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


async def test_upsert_inserts_new_rows(created):
    User = created
    written = await User.upsert(
        [
            {"name": "A", "email": "a@x.com"},
            {"name": "B", "email": "b@x.com"},
        ],
        unique_by=["email"],
    )
    assert written == 2
    assert await User.count() == 2


async def test_upsert_updates_existing_rows(created):
    User = created
    await User.upsert([{"name": "A", "email": "a@x.com", "views": 1}], unique_by=["email"])

    # every non-unique column updates by default
    await User.upsert([{"name": "B", "email": "a@x.com", "views": 9}], unique_by=["email"])
    row = await User.query().where(User.email == "a@x.com").first()
    assert row.name == "B" and row.views == 9

    # ...and `update=` narrows the written columns
    await User.upsert(
        [{"name": "C", "email": "a@x.com", "views": 10}], unique_by=["email"], update=["views"]
    )
    row = await User.query().where(User.email == "a@x.com").first()
    assert row.name == "B" and row.views == 10


async def test_upsert_mixed_batch_in_one_transaction(created):
    User = created
    await User.create(name="A", email="a@x.com", views=1)

    written = await User.upsert(
        [
            {"name": "A2", "email": "a@x.com", "views": 2},
            {"name": "B", "email": "b@x.com"},
        ],
        unique_by=["email"],
    )
    assert written == 2
    rows = {r.email: r for r in await User.query().get()}
    assert rows["a@x.com"].name == "A2" and rows["a@x.com"].views == 2
    assert rows["b@x.com"].name == "B"


async def test_upsert_batch_is_atomic(created):
    """One bad row rolls the whole batch back — no partial import.

    A duplicate key inside one batch is not a failure (last row wins);
    a guard violation is — and it must sink the rows already flushed
    before it, not just itself.
    """
    User = created
    with pytest.raises(MassAssignmentError):
        await User.upsert(
            [
                {"name": "A", "email": "a@x.com"},
                {"name": "B", "email": "b@x.com", "updated_at": "nope"},
            ],
            unique_by=["email"],
        )
    assert await User.count() == 0


async def test_upsert_empty_batch_is_a_noop(created):
    User = created
    assert await User.upsert([], unique_by=["email"]) == 0


async def test_upsert_row_with_only_unique_keys_matches_existing(created):
    """A row carrying nothing but its unique keys still matches, not re-inserts.

    The confirm-SELECT must run even when there is no update payload — an
    empty-payload row that fell straight to INSERT would die on the unique
    constraint the second time the batch runs.
    """
    User = created
    await User.upsert([{"name": "A", "email": "only@x.com"}], unique_by=["email"])
    written = await User.upsert([{"email": "only@x.com"}], unique_by=["email"])
    assert written == 1
    assert await User.count() == 1


async def test_upsert_same_key_twice_in_one_batch_last_row_wins(created):
    """A later duplicate updates the row an earlier duplicate inserted.

    With autoflush=False the first row's INSERT stays pending until an
    explicit flush — without one, the second row's UPDATE and confirm-SELECT
    both miss it and the batch dies on the unique constraint at final flush.
    Last row wins, MySQL-style.
    """
    User = created
    written = await User.upsert(
        [
            {"name": "A", "email": "dup@x.com", "views": 1},
            {"name": "B", "email": "dup@x.com", "views": 2},
        ],
        unique_by=["email"],
    )
    assert written == 2
    rows = await User.query().get()
    assert len(rows) == 1
    assert rows[0].name == "B" and rows[0].views == 2


async def test_upsert_requires_unique_keys_in_every_row(created):
    User = created
    with pytest.raises(ValueError):
        await User.upsert([{"name": "no key"}], unique_by=["email"])


async def test_upsert_honors_mass_assignment_guard(created):
    User = created
    with pytest.raises(MassAssignmentError):
        await User.upsert([{"id": 1, "name": "x", "email": "x@x.com"}], unique_by=["id"])


async def test_upsert_matches_soft_deleted_rows_physically(created):
    """The tombstone never hides a row from its unique key.

    The UPDATE repairs the physical row but leaves the tombstone — the key
    stays taken and the row stays hidden until restore(); both facts are the
    documented contract, not accidents to "fix" later.
    """
    User = created
    user = await User.create(name="A", email="s@x.com", views=1)
    await user.delete()

    written = await User.upsert(
        [{"name": "A", "email": "s@x.com", "views": 7}], unique_by=["email"]
    )
    assert written == 1
    assert await User.count() == 0  # still tombstoned for default queries

    physical = (await User.with_deleted().get())[0]
    assert physical.views == 7
    assert physical.deleted_at is not None


# ---------------------------------------------------------------------------
# increment / decrement
# ---------------------------------------------------------------------------


async def test_increment_defaults_to_one(created):
    User = created
    user = await User.create(name="A", email="a@x.com", views=0)
    await user.increment("views")
    assert user.views == 1
    fresh = await User.find(user.id)
    assert fresh.views == 1


async def test_decrement_with_explicit_amount(created):
    User = created
    user = await User.create(name="A", email="a@x.com", views=5)
    await user.decrement("views", 3)
    assert user.views == 2


async def test_increment_allows_negative_results(created):
    """Plain arithmetic — counters may go negative, there is no floor."""
    User = created
    user = await User.create(name="A", email="a@x.com", views=1)
    await user.decrement("views", 4)
    assert user.views == -3
    fresh = await User.find(user.id)
    assert fresh.views == -3


async def test_increment_rejects_unpersisted_instance(User, db_url):
    await db.create_all()
    ghost = User(name="G", email="g@x.com")
    with pytest.raises(ValueError):
        await ghost.increment("views")


async def test_increment_unknown_column_raises(created):
    User = created
    user = await User.create(name="A", email="a@x.com")
    with pytest.raises(AttributeError):
        await user.increment("nope")


async def test_increment_rejects_non_numeric_column(created):
    """Text columns silently corrupt under SQL arithmetic.

    SQLite stores the concatenated string, MySQL coerces to 0 — either way
    the column is garbage after the UPDATE. A type mismatch is a caller
    bug, refused before a single byte is written.
    """
    User = created
    user = await User.create(name="A", email="a@x.com")
    with pytest.raises(TypeError):
        await user.increment("name")
    fresh = await User.find(user.id)
    assert fresh.name == "A"  # nothing was written


async def test_increment_joins_ambient_transaction(created):
    User = created
    user = await User.create(name="A", email="a@x.com", views=5)
    with pytest.raises(RuntimeError):
        async with db.transaction():
            await user.increment("views")
            raise RuntimeError("boom")
    fresh = await User.find(user.id)
    assert fresh.views == 5


# ---------------------------------------------------------------------------
# chunk / chunk_by_id / cursor
# ---------------------------------------------------------------------------


async def test_chunk_pages_every_row_once_in_order(seeded):
    Task = seeded
    priorities = [row.priority async for row in Task.query().order_by(Task.priority).chunk(2)]
    assert priorities == [0, 1, 2, 3, 4]

    titles = [row.title async for row in Task.query().order_by(Task.priority.desc()).chunk(2)]
    assert titles[0] == "review pr"
    assert len(titles) == 5


async def test_chunk_on_empty_table_yields_nothing(Task, db_url):
    await db.create_all()
    assert [row async for row in Task.query().chunk(10)] == []


async def test_chunk_rejects_non_positive_size(seeded):
    Task = seeded
    with pytest.raises(ValueError):
        Task.query().chunk(0)


async def test_chunk_by_id_pages_in_primary_key_order(seeded):
    Task = seeded
    ids = [row.id async for row in Task.query().chunk_by_id(2)]
    assert ids == sorted(ids)
    assert len(ids) == 5


async def test_chunk_by_id_requires_single_integer_pk(db_url):
    class StrKey(Model):
        __tablename__ = "str_keys"

        key: str = Field(primary_key=True)

    with pytest.raises(TypeError):
        StrKey.query().chunk_by_id(10)


async def test_chunk_by_id_stable_under_mid_iteration_inserts(seeded):
    """Keyset paging: a row inserted mid-stream appears exactly once, later.

    Offset paging would shift every row across a page boundary here — the
    duplicate/skip problem chunk_by_id exists to prevent.
    """
    Task = seeded
    seen: list[int] = []
    inserted = False
    async for row in Task.query().chunk_by_id(2):
        seen.append(row.id)
        if not inserted and len(seen) == 2:
            await Task.create(title="late arrival", priority=99)
            inserted = True

    assert len(seen) == len(set(seen))
    assert sorted(seen) == sorted(row.id for row in await Task.with_deleted().get())


async def test_cursor_streams_every_row(seeded):
    Task = seeded
    titles = [row.title async for row in Task.query().order_by(Task.id).cursor()]
    expected = [row.title for row in await Task.query().order_by(Task.id).get()]
    assert titles == expected


async def test_cursor_sees_uncommitted_writes_of_ambient_transaction(seeded):
    Task = seeded
    async with db.transaction():
        await Task.create(title="in tx")
        seen = [row.title async for row in Task.query().order_by(Task.id).cursor()]
    assert "in tx" in seen
    assert len(seen) == 6


async def test_cursor_early_break_releases_its_session_across_tasks(seeded, monkeypatch):
    """Breaking out of a standalone cursor must not poison anything.

    The standalone branch once held a bind_session() ContextVar token open
    across the yield: finalizing the generator in a different asyncio task
    (GC may run aclose() in any context) crashed the token reset — and that
    crash skipped session.close(), leaking the connection. A standalone
    cursor owns its session outright and never binds the ambient state.
    """
    Task = seeded

    closed: list[int] = []
    real_close = AsyncSession.close

    async def spy_close(self: Any) -> Any:
        closed.append(1)
        return await real_close(self)

    monkeypatch.setattr(AsyncSession, "close", spy_close)

    agen = Task.query().order_by(Task.id).cursor()
    count = 0
    async for _row in agen:
        count += 1
        if count == 2:
            break

    # Finalize from another task — the GC is free to run aclose() anywhere.
    await asyncio.ensure_future(agen.aclose())
    assert closed == [1]  # the cursor's session was closed, not leaked
    assert await Task.count() == 5  # the ambient context is untouched
