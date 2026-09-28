"""Cursor pagination — keyset pages with an opaque ``next_cursor``.

``cursor_paginate(after=..., per_page=...)`` walks ``(sort, id)`` keyset
conditions instead of OFFSET: no COUNT query, stable under concurrent
inserts, no duplicates or gaps across pages.
"""

from __future__ import annotations

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
