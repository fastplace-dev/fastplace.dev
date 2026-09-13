"""Query builder tests — filtering, ordering, pagination, scopes."""

from __future__ import annotations

import pytest

from fastplace.db import db
from fastplace.orm import Field, Model, scope


@pytest.fixture()
def Task():  # noqa: N802
    class Task(Model):
        __tablename__ = "tasks"

        id: int = Field(primary_key=True)
        title: str
        completed: bool = False
        priority: int = 0

        @scope
        def in_progress(cls, query):
            return query.where(cls.completed == False)  # noqa: E712

        @scope
        def high_priority(cls, query, minimum=5):
            return query.where(cls.priority >= minimum)

    return Task


@pytest.fixture()
async def seeded(Task, db_url):
    await db.create_all()
    rows = [
        {"title": "write tests", "completed": True, "priority": 9},
        {"title": "design orm", "completed": False, "priority": 8},
        {"title": "ship v1", "completed": False, "priority": 3},
        {"title": "write docs", "completed": True, "priority": 2},
        {"title": "review pr", "completed": False, "priority": 7},
    ]
    for row in rows:
        await Task.create(**row)
    return Task


async def test_get_returns_matching_rows(seeded):
    rows = await seeded.query().where(seeded.completed == True).get()  # noqa: E712
    assert len(rows) == 2
    assert all(r.completed for r in rows)


async def test_multiple_where_clauses_and(seeded):
    Task = seeded
    rows = (
        await Task.query()
        .where(Task.completed == False)  # noqa: E712
        .where(Task.priority >= 7)
        .get()
    )
    assert sorted(r.title for r in rows) == ["design orm", "review pr"]


async def test_order_by_asc_desc(seeded):
    Task = seeded
    asc = [r.priority for r in await Task.query().order_by(Task.priority).get()]
    assert asc == [2, 3, 7, 8, 9]
    desc = [r.priority for r in await Task.query().order_by(Task.priority.desc()).get()]
    assert desc == [9, 8, 7, 3, 2]


async def test_limit_offset(seeded):
    Task = seeded
    rows = await Task.query().order_by(Task.id).limit(2).offset(1).get()
    assert [r.title for r in rows] == ["design orm", "ship v1"]


async def test_first_and_all(seeded):
    Task = seeded
    first = await Task.query().order_by(Task.id).first()
    assert first.title == "write tests"
    assert len(await Task.all()) == 5


async def test_count(seeded):
    Task = seeded
    assert await Task.count() == 5
    assert await Task.query().where(Task.completed == True).count() == 2  # noqa: E712


async def test_exists(seeded):
    Task = seeded
    assert await Task.query().where(Task.title == "ship v1").exists()
    assert not await Task.query().where(Task.title == "nope").exists()


async def test_paginate_shape(seeded):
    Task = seeded
    page = await Task.query().order_by(Task.id).paginate(per_page=2, page=2)
    assert page.current_page == 2
    assert page.per_page == 2
    assert page.total == 5
    assert page.last_page == 3
    assert page.has_more is True
    assert len(page.items) == 2
    assert page.on_first_page is False
    assert page.on_last_page is False

    as_dict = page.to_dict()
    assert as_dict["total"] == 5
    assert "items" in as_dict


async def test_paginate_first_and_last_page_edges(seeded):
    Task = seeded
    p1 = await Task.query().order_by(Task.id).paginate(per_page=2, page=1)
    assert p1.on_first_page is True and p1.has_more is True
    p3 = await Task.query().order_by(Task.id).paginate(per_page=2, page=3)
    assert p3.on_last_page is True
    assert p3.has_more is False
    assert len(p3.items) == 1


async def test_custom_scopes(seeded):
    Task = seeded
    rows = await Task.query().in_progress().get()
    assert all(not r.completed for r in rows) and len(rows) == 3

    rows = await Task.query().in_progress().high_priority().get()
    assert sorted(r.title for r in rows) == ["design orm", "review pr"]

    rows = await Task.query().high_priority(minimum=3).get()
    assert len(rows) == 4


async def test_scope_chain_after_where(seeded):
    Task = seeded
    rows = await Task.query().where(Task.priority > 2).in_progress().get()
    assert sorted(r.title for r in rows) == ["design orm", "review pr", "ship v1"]
