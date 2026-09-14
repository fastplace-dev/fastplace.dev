"""Portable scopes — reusable query filters on every backend.

The blueprint's compatibility-matrix "scopes" category: ``@scope``-declared
class-method filters, argument-bearing scopes, and scope chaining before and
after plain ``where()`` clauses.
"""

from __future__ import annotations

from fastplace.db import db
from fastplace.orm import Field, Model, scope


async def test_scopes_chain_and_take_arguments(backend):
    class Chore(Model):
        __tablename__ = "port_chores"

        id: int = Field(primary_key=True)
        title: str
        done: bool = False
        points: int = 0

        @scope
        def pending(cls, query):
            return query.where(cls.done == False)  # noqa: E712

        @scope
        def worth(cls, query, minimum=5):
            return query.where(cls.points >= minimum)

    await db.create_all()
    rows = [
        {"title": "sweep", "done": True, "points": 9},
        {"title": "plan orm", "done": False, "points": 8},
        {"title": "rest", "done": False, "points": 3},
        {"title": "file docs", "done": True, "points": 2},
        {"title": "review pr", "done": False, "points": 7},
    ]
    for row in rows:
        await Chore.create(**row)

    pending = await Chore.query().pending().get()
    assert sorted(c.title for c in pending) == ["plan orm", "rest", "review pr"]

    chained = await Chore.query().pending().worth().get()
    assert sorted(c.title for c in chained) == ["plan orm", "review pr"]

    argued = await Chore.query().worth(minimum=3).get()
    assert len(argued) == 4

    # Scopes compose with plain where() in either order.
    mixed = await Chore.query().where(Chore.points > 2).pending().get()
    assert sorted(c.title for c in mixed) == ["plan orm", "rest", "review pr"]
