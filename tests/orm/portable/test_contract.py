"""Relational portability contract — identical behavior on every backend.

The ``backend`` fixture (see conftest) parametrizes over in-memory SQLite
plus every server backend CI exports (blueprint §8: "portable is a test
property, not a hope"). Every public-API behavior a repository might rely
on is asserted here per backend.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


@pytest.fixture()
def Article():
    from fastplace.orm import Field, Model

    class Article(Model):
        __tablename__ = "contract_articles"

        id: int = Field(primary_key=True)
        title: str = Field(unique=True)
        body: str = ""
        views: int = 0
        price: Decimal = Field(default=Decimal("0.00"), precision=12, scale=2)
        meta: dict = Field(default_factory=dict)

    return Article


async def test_crud_round_trip(backend, Article):
    from fastplace.db import db

    await db.create_all()

    created = await Article.create(title="hello", body="world", views=3)
    assert created.id is not None

    fetched = await Article.find(created.id)
    assert fetched is not None
    assert fetched.title == "hello"

    await fetched.update(views=9)
    await fetched.refresh()
    assert fetched.views == 9

    assert await Article.count() == 1


async def test_soft_delete_hides_rows_but_keeps_them(backend, Article):
    from fastplace.db import db

    await db.create_all()
    article = await Article.create(title="gone soon")

    await article.delete()

    assert await Article.count() == 0
    revived = await Article.with_deleted().first()
    assert revived is not None and revived.id == article.id


async def test_unique_constraint_rejects_duplicates(backend, Article):
    from sqlalchemy.exc import IntegrityError

    from fastplace.db import db

    await db.create_all()
    await Article.create(title="unique-title")

    with pytest.raises(IntegrityError):
        await Article.create(title="unique-title")


async def test_transaction_rollback_leaves_nothing_behind(backend, Article):
    from fastplace.db import db

    await db.create_all()

    with pytest.raises(RuntimeError, match="boom"):
        async with db.transaction():
            await Article.create(title="rolled back")
            raise RuntimeError("boom")

    assert await Article.count() == 0


async def test_filtering_sorting_and_pagination(backend, Article):
    from fastplace.db import db

    await db.create_all()
    for index in range(10):
        await Article.create(title=f"article-{index:02d}", views=index)

    page = (
        await Article.where(Article.views >= 3)
        .order_by(Article.views.desc())
        .limit(3)
        .offset(1)
        .get()
    )
    assert [article.views for article in page] == [8, 7, 6]


async def test_json_and_decimal_columns_round_trip(backend, Article):
    from fastplace.db import db

    await db.create_all()
    await Article.create(
        title="typed",
        price=Decimal("19.99"),
        meta={"tags": ["a", "b"], "nested": {"ok": True}},
    )

    fetched = await Article.first()
    assert fetched.price == Decimal("19.99")
    assert fetched.meta["tags"] == ["a", "b"]
    assert fetched.meta["nested"]["ok"] is True
