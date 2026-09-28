"""Cursor pagination — keyset pages with an opaque ``next_cursor``.

``cursor_paginate(after=..., per_page=...)`` walks ``(sort, id)`` keyset
conditions instead of OFFSET: no COUNT query, stable under concurrent
inserts, no duplicates or gaps across pages.
"""

from __future__ import annotations

import datetime

import pytest

from fastplace.db import db
from fastplace.errors import ConfigurationError
from fastplace.orm import Field, Model


@pytest.fixture()
def Task():  # noqa: N802
    class Task(Model):
        __tablename__ = "cursor_tasks"

        id: int = Field(primary_key=True)
        title: str
        priority: int = 0

    return Task


@pytest.fixture()
async def seeded(Task, db_url):
    await db.create_all()
    priorities = [9, 8, 3, 2, 7, 5, 1, 6, 4, 0]
    for index, priority in enumerate(priorities):
        await Task.create(title=f"task-{index}", priority=priority)
    return Task


async def test_first_page_shape(seeded):
    page = (
        await seeded.query().order_by(seeded.priority.desc(), seeded.id).cursor_paginate(per_page=4)
    )
    assert len(page.items) == 4
    assert page.has_more is True
    assert page.next_cursor
    payload = page.to_dict()
    assert set(payload) == {"items", "next_cursor", "has_more"}


async def test_cursor_walks_the_set_without_duplicates_or_gaps(seeded):
    seen = []
    cursor = None
    while True:
        query = seeded.query().order_by(seeded.priority.desc(), seeded.id)
        page = await query.cursor_paginate(per_page=3, after=cursor)
        seen.extend(page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(seen) == 10
    assert len({row.id for row in seen}) == 10
    priorities = [row.priority for row in seen]
    assert priorities == sorted(priorities, reverse=True)


async def test_default_order_is_the_primary_key(seeded):
    page = await seeded.query().cursor_paginate(per_page=3)
    ids = [row.id for row in page.items]
    assert ids == sorted(row.id for row in await seeded.query().get())[:3]


async def test_malformed_cursor_is_rejected(seeded):
    with pytest.raises(ConfigurationError):
        await seeded.query().order_by(seeded.id).cursor_paginate(per_page=2, after="not-a-cursor")


async def test_inserts_before_the_page_cause_no_repeats(seeded):
    """The OFFSET failure mode: a new top row between pages must not shift
    every keyset page — page 2 continues after page 1's last row."""
    first = (
        await seeded.query().order_by(seeded.priority.desc(), seeded.id).cursor_paginate(per_page=5)
    )
    await seeded.create(title="urgent-new", priority=100)
    second = (
        await seeded.query()
        .order_by(seeded.priority.desc(), seeded.id)
        .cursor_paginate(per_page=5, after=first.next_cursor)
    )
    first_ids = {row.id for row in first.items}
    second_ids = {row.id for row in second.items}
    assert first_ids.isdisjoint(second_ids)
    assert all(row.priority < 9 for row in second.items)


async def test_ascending_order_walks_forward(seeded):
    seen = []
    cursor = None
    while True:
        query = seeded.query().order_by(seeded.priority, seeded.id.desc())
        page = await query.cursor_paginate(per_page=4, after=cursor)
        seen.extend(page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    priorities = [row.priority for row in seen]
    assert len(seen) == 10
    assert priorities == sorted(priorities)


async def test_default_order_compiles_an_order_by(seeded, monkeypatch):
    """Keyset pagination without an explicit order_by must still emit a
    deterministic ORDER BY (the derived pk sort) — otherwise every page is
    an arbitrary row subset and cursors skip/duplicate rows."""
    captured: list[object] = []

    class _Boom(Exception):
        pass

    async def fake_run_read(stmt):
        captured.append(stmt)
        raise _Boom()

    monkeypatch.setattr("fastplace.orm.query.run_read", fake_run_read)
    with pytest.raises(_Boom):
        await seeded.query().cursor_paginate(per_page=4)
    assert captured, "statement never executed"
    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "ORDER BY" in compiled


async def test_explicit_order_survives_the_default_tiebreaker(seeded, monkeypatch):
    """An explicit order_by keeps its criteria; the pk still joins so rows
    with equal sort values can never interleave across pages."""
    captured: list[object] = []

    class _Boom(Exception):
        pass

    async def fake_run_read(stmt):
        captured.append(stmt)
        raise _Boom()

    monkeypatch.setattr("fastplace.orm.query.run_read", fake_run_read)
    with pytest.raises(_Boom):
        await seeded.query().order_by(seeded.priority.desc()).cursor_paginate(per_page=4)
    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    order_at = compiled.index("ORDER BY")
    assert compiled.index("priority", order_at) < compiled.index("id", order_at)


def test_coerce_keyset_value_roundtrips_non_native_types():
    """Cursor JSON stores datetime/UUID/Decimal as str (json default=str);
    decoding must coerce back to the column's python_type or page 2's SQL
    compares a str against a typed value (asyncpg rejects it outright)."""
    import datetime as dt
    import uuid
    from decimal import Decimal

    from fastplace.orm.query import _coerce_keyset_value

    class _SaType:
        """Stand-in for a column type — .python_type, like real SA types."""

        def __init__(self, python_type):
            self.python_type = python_type

    assert _coerce_keyset_value(_SaType(dt.datetime), "2026-09-28T10:30:00") == dt.datetime(
        2026, 9, 28, 10, 30
    )
    assert _coerce_keyset_value(_SaType(dt.date), "2026-09-28") == dt.date(2026, 9, 28)
    assert _coerce_keyset_value(_SaType(uuid.UUID), str(uuid.UUID(int=42))) == uuid.UUID(int=42)
    assert _coerce_keyset_value(_SaType(Decimal), "12.5") == Decimal("12.5")
    assert _coerce_keyset_value(_SaType(int), 7) == 7
    assert _coerce_keyset_value(_SaType(str), "abc") == "abc"
    assert _coerce_keyset_value(_SaType(dt.datetime), None) is None
    with pytest.raises(ConfigurationError):
        _coerce_keyset_value(_SaType(dt.datetime), "not-a-date")


async def test_datetime_sorted_walk_pages_through_every_row(Task, db_url):
    """A datetime sort column walks the full keyset without gaps — the
    cursor round-trips through its str form and comes back a datetime."""
    Timed = type(
        "Timed",
        (Model,),
        {
            "__tablename__": "cursor_timed",
            "__annotations__": {
                "id": int,
                "title": str,
                "created_at": datetime.datetime,
            },
            "id": Field(primary_key=True),
            "title": Field(),
            "created_at": Field(),
            "__module__": __name__,
        },
    )

    await db.create_all()
    base = datetime.datetime(2026, 9, 28, 12, 0, 0)
    for index in range(7):
        row = Timed(title=f"t-{index}")
        row.created_at = base + datetime.timedelta(minutes=index)
        await row.save()

    seen = []
    cursor = None
    while True:
        page = (
            await Timed.query()
            .order_by(Timed.created_at.desc(), Timed.id)
            .cursor_paginate(per_page=3, after=cursor)
        )
        seen.extend(page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert len(seen) == 7
    assert len({row.id for row in seen}) == 7
    assert [row.title for row in seen] == [f"t-{i}" for i in range(6, -1, -1)]
