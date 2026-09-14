"""MongoDB document adapter contract — real server round trips (T6.3).

Env-gated: exports ``TEST_MONGODB_URL=mongodb://host:port`` (scratch
server — every test drops its collection on teardown). Without the
variable the whole module skips.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_MONGODB_URL"),
    reason="TEST_MONGODB_URL not set — MongoDB contract suite is opt-in",
)


@pytest.fixture(autouse=True)
async def mongo(monkeypatch):
    monkeypatch.setenv("MONGODB_URL", os.environ["TEST_MONGODB_URL"])

    from fastplace.orm.documents import reset_documents

    reset_documents()
    yield

    from fastplace.orm.documents import Document, reset_documents

    # Server state persists — leave the scratch database as empty as we
    # found it (collections this suite created, at least).
    for doc_cls in Document.__subclasses__():
        await doc_cls._mongo_collection().drop()
    reset_documents()


@pytest.fixture()
def Article():
    from fastplace.orm.documents import Document

    class Article(Document):
        title: str = ""
        views: int = 0
        tags: list = list

    return Article


async def test_create_returns_instance_with_generated_id(Article):
    doc = await Article.create(title="hello", views=3)

    assert doc.id is not None
    assert doc.title == "hello"
    assert doc.views == 3

    fetched = await Article.first(title="hello")
    assert fetched is not None and fetched.id == doc.id


async def test_annotation_defaults_apply_on_insert(Article):
    await Article.create(title="defaults")

    fetched = await Article.first(title="defaults")
    assert fetched.views == 0
    assert fetched.tags == []


async def test_insert_many_returns_ids_and_find_paginates(Article):
    ids = await Article.insert(
        [{"title": f"article-{index}", "views": index} for index in range(10)]
    )
    assert len(ids) == 10

    page = await Article.find({"views": {"$gte": 3}}, sort=[("views", -1)], skip=1, limit=3)
    assert [doc.views for doc in page] == [8, 7, 6]


async def test_where_chains_filters_and_sorts(Article):
    await Article.create(title="a", views=1, tags=["x"])
    await Article.create(title="b", views=5, tags=["x", "y"])
    await Article.create(title="c", views=9, tags=[])

    hits = await Article.where(tags="x").sort("views", -1).get()
    assert [doc.title for doc in hits] == ["b", "a"]


async def test_instance_update_persists_and_refreshes(Article):
    doc = await Article.create(title="before", views=1)

    await doc.update(title="after", views=2)
    assert doc.title == "after" and doc.views == 2

    fetched = await Article.first({"_id": doc.id})
    assert fetched is not None and fetched.title == "after"


async def test_query_update_and_delete_by_filter(Article):
    await Article.insert([{"title": "keep", "views": 1}, {"title": "drop", "views": 9}])

    matched = await Article.where(views=9).update({"$set": {"views": 10}})
    assert matched == 1

    deleted = await Article.where({"views": {"$gte": 10}}).delete()
    assert deleted == 1
    assert await Article.count() == 1


async def test_instance_delete_removes_the_document(Article):
    doc = await Article.create(title="gone")

    await doc.delete()

    assert await Article.count() == 0


async def test_nested_documents_round_trip(Article):
    await Article.create(
        title="nested",
        tags=["a", "b"],
    )
    # nested structures arrive intact through the raw payload path
    await Article.insert([{"title": "raw", "tags": [{"k": [1, 2, {"deep": True}]}]}])

    fetched = await Article.first(title="raw")
    assert fetched.tags == [{"k": [1, 2, {"deep": True}]}]


async def test_reload_refreshes_from_the_server(Article):
    doc = await Article.create(title="stale")
    await Article.where(title="stale").update({"$set": {"views": 42}})

    await doc.reload()

    assert doc.views == 42
