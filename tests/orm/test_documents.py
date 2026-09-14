"""Document adapter unit behavior — naming, filters, defaults, wiring (T6.3).

Everything here runs without a MongoDB server: pure query-builder and
convention logic. Server round trips live in the env-gated contract suite
(``tests/orm/mongodb/``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pymongo", reason="document adapter lives behind the pymongo extra")


class FakeCollection:
    """Records writes — stands in for an AsyncCollection without a server."""

    def __init__(self):
        self.inserted: list[dict] = []
        self.updated_filter: dict | None = None
        self.update_result = None
        self.deleted_filter: dict | None = None
        self.deleted_count = 0

    async def insert_one(self, payload):
        self.inserted.append(payload)
        return SimpleNamespace(inserted_id=f"oid{len(self.inserted)}")

    async def find_one_and_update(self, filter, update, **kwargs):
        self.updated_filter = dict(filter)
        return self.update_result

    async def delete_one(self, filter):
        self.deleted_filter = dict(filter)
        result = SimpleNamespace(deleted_count=self.deleted_count)
        self.deleted_count = 0
        return result


@pytest.fixture()
def fake_collection(monkeypatch):
    from fastplace.orm.documents import Document

    fake = FakeCollection()

    class Article(Document):
        title: str = ""
        views: int = 0
        tags: list = list

    monkeypatch.setattr(Article, "_mongo_collection", classmethod(lambda cls: fake))
    return Article, fake


@pytest.fixture(autouse=True)
def _clean_documents_singleton():
    from fastplace.orm.documents import reset_documents

    reset_documents()
    yield
    reset_documents()


def test_collection_name_defaults_to_pluralized_snake_case():
    from fastplace.orm.documents import Document

    class Article(Document):
        pass

    class Category(Document):
        pass

    class Analytics(Document):
        pass

    assert Article.__collection__ == "articles"
    assert Category.__collection__ == "categories"
    assert Analytics.__collection__ == "analytics"


def test_explicit_collection_name_wins():
    from fastplace.orm.documents import Document

    class Person(Document):
        __collection__ = "people"

    assert Person.__collection__ == "people"


def test_where_merges_equality_kwargs_and_raw_filters():
    from fastplace.orm.documents import Document, DocumentQuery

    class Article(Document):
        pass

    query = Article.where(title="hello")
    assert isinstance(query, DocumentQuery)
    assert query._filter == {"title": "hello"}

    merged = query.where({"views": {"$gte": 3}})
    assert merged._filter == {"title": "hello", "views": {"$gte": 3}}
    # immutable chain — the original query is untouched
    assert query._filter == {"title": "hello"}

    # a None equality matches stored nulls (Mongo semantics), not "ignore me"
    assert Article.where(views=None)._filter == {"views": None}


def test_query_builder_accumulates_sort_skip_limit_immutably():
    from fastplace.orm.documents import Document

    class Article(Document):
        pass

    base = Article.where(title="x")
    paged = base.sort("views", -1).skip(10).limit(5)

    assert paged._sort == [("views", -1)]
    assert paged._skip == 10
    assert paged._limit == 5
    assert base._sort == [] and base._skip == 0 and base._limit is None

    # chained sort keys compose into one ordering spec (major → minor)
    multi = base.sort("views", -1).sort("title", 1)
    assert multi._sort == [("views", -1), ("title", 1)]


def test_annotation_defaults_fill_missing_keys_on_create_data():
    from fastplace.orm.documents import Document, _document_payload

    class Article(Document):
        title: str = ""
        views: int = 0
        tags: list = list

    payload = _document_payload(Article, {"title": "hi"})
    assert payload == {"title": "hi", "views": 0, "tags": []}

    # provided values are never clobbered by defaults
    full = _document_payload(Article, {"title": "hi", "views": 9, "tags": ["a"]})
    assert full == {"title": "hi", "views": 9, "tags": ["a"]}


def test_client_url_and_database_resolution(monkeypatch):
    from fastplace.orm.documents import (
        _database_name_for,
        get_documents_client,
        reset_documents,
    )

    # database named in the URL path wins
    assert _database_name_for("mongodb://localhost:27017/appdb", None) == "appdb"
    # …falling back to config, then the framework default
    assert _database_name_for("mongodb://localhost:27017", "configured") == "configured"
    assert _database_name_for("mongodb://localhost:27017", None) == "fastplace"

    monkeypatch.setenv("MONGODB_URL", "mongodb://localhost:27017/appdb")
    reset_documents()
    client = get_documents_client()
    try:
        assert client is get_documents_client()  # singleton
    finally:
        reset_documents()
    assert get_documents_client() is not client  # reset builds a fresh one


def test_document_instances_expose_stored_fields_and_id_alias():
    from fastplace.orm.documents import Document

    class Article(Document):
        pass

    doc = Article._from_mongo({"_id": "abc", "title": "hello", "views": 3})
    assert doc._id == "abc"
    assert doc.id == "abc"
    assert doc.title == "hello"
    assert doc.views == 3
    assert doc.to_dict() == {"_id": "abc", "title": "hello", "views": 3}


def test_reset_documents_clears_the_singleton():
    from fastplace.orm import documents

    documents.reset_documents()
    assert documents._client is None


async def test_aclose_documents_is_awaitable_and_clears_state():
    from fastplace.orm import documents
    from fastplace.orm.documents import aclose_documents

    await aclose_documents()
    assert documents._client is None


def test_acronym_class_names_snake_case_cleanly():
    from fastplace.orm.documents import Document

    class APIKey(Document):
        pass

    class HTTPRequest(Document):
        pass

    class URL(Document):
        pass

    assert APIKey.__collection__ == "api_keys"
    assert HTTPRequest.__collection__ == "http_requests"
    assert URL.__collection__ == "urls"


async def test_create_returns_instance_matching_the_persisted_document(fake_collection):
    Article, fake = fake_collection

    doc = await Article.create(title="hello")

    # exactly one write, and it carries the annotation defaults…
    assert fake.inserted == [{"title": "hello", "views": 0, "tags": []}]
    # …and the returned instance mirrors what was persisted
    assert doc.to_dict() == {"title": "hello", "views": 0, "tags": [], "_id": "oid1"}
    assert doc.tags == []  # the factory resolved, not the `list` type itself


def test_subclass_defaults_override_base_defaults():
    from fastplace.orm.documents import Document, _document_payload

    class Base(Document):
        title: str = "base-default"
        tags: list = list

    class Sub(Base):
        title: str = "sub-default"
        tags: list = ["fixed"]

    class AnnotatedOnly(Base):
        views: int  # annotation without a default — no payload entry of its own

    assert _document_payload(Sub, {}) == {"title": "sub-default", "tags": ["fixed"]}
    # annotation-only subclass still falls back to the base default
    assert _document_payload(AnnotatedOnly, {}) == {"title": "base-default", "tags": []}


async def test_instance_update_and_delete_guard_unsaved_instances(fake_collection):
    Article, _ = fake_collection

    unsaved = Article(title="x")
    with pytest.raises(LookupError, match="no _id"):
        await unsaved.update(views=1)
    with pytest.raises(LookupError, match="no _id"):
        await unsaved.delete()


async def test_instance_update_raises_when_document_vanished(fake_collection):
    Article, fake = fake_collection

    stale = Article(_id="ghost", title="old")
    fake.update_result = None  # find_one_and_update matched nothing

    with pytest.raises(LookupError, match="vanish"):
        await stale.update(title="new")
    assert fake.updated_filter == {"_id": "ghost"}


async def test_instance_delete_raises_when_document_vanished(fake_collection):
    Article, fake = fake_collection

    stale = Article(_id="ghost", title="old")
    fake.deleted_count = 0  # delete_one removed nothing

    with pytest.raises(LookupError, match="vanish"):
        await stale.delete()
    assert fake.deleted_filter == {"_id": "ghost"}


async def test_query_update_and_delete_reject_skip_limit():
    from fastplace.orm.documents import Document

    class Article(Document):
        pass

    with pytest.raises(ValueError, match="skip/limit"):
        await Article.where({}).limit(5).update({"$set": {"views": 1}})
    with pytest.raises(ValueError, match="skip/limit"):
        await Article.where({}).skip(2).delete()


def test_where_collisions_intersect_via_and():
    from fastplace.orm.documents import Document

    class Article(Document):
        pass

    ranged = Article.where(views={"$gte": 1}).where(views={"$lte": 9})
    assert ranged._filter == {"$and": [{"views": {"$gte": 1}}, {"views": {"$lte": 9}}]}

    # identical constraints stay flat instead of wrapping a redundant $and
    same = Article.where(title="a").where(title="a")
    assert same._filter == {"title": "a"}


def test_mass_assignment_guard_mirrors_the_relational_model():
    from fastplace.errors import MassAssignmentError
    from fastplace.orm.documents import Document

    class Comment(Document):
        __fillable__ = ("title", "body")

        title: str = ""
        body: str = ""
        author: str = ""

    # non-fillable keys are ignored (allowlist semantics)
    assert Comment._mass_assignable({"title": "t", "author": "a", "rogue": 1}) == {"title": "t"}

    class Strict(Document):
        __guarded__ = ("role",)

        role: str = "user"

    with pytest.raises(MassAssignmentError, match="role"):
        Strict._mass_assignable({"role": "admin"})

    # _id is the document primary key: silently ignored outside __fillable__
    # (allowlist semantics), assignable only through an explicit opt-in
    assert Comment._mass_assignable({"_id": "forged"}) == {}
    Comment.__fillable__ = ("_id", "title")
    assert Comment._mass_assignable({"_id": "natural-key"}) == {"_id": "natural-key"}


async def test_create_routes_through_the_mass_assignment_guard(fake_collection):
    Article, _ = fake_collection

    with pytest.raises(Exception, match="_id"):
        await Article.create(_id="forged", title="x")


# ---------------------------------------------------------------------------
# __query_class__ hook + public payload builder
# ---------------------------------------------------------------------------
def test_query_class_hook_lets_subclasses_intercept_where():
    """A subclass (fastplace-tenancy's CompanyDocument) needs its own query
    type to guard update()/delete() — where() must build through the hook,
    not a hard-coded DocumentQuery."""
    from fastplace.orm.documents import Document, DocumentQuery

    class Probe(Document):
        title: str = ""

    class SpyQuery(DocumentQuery):
        pass

    Probe.__query_class__ = SpyQuery  # type: ignore[assignment]
    query = Probe.where(title="x")
    assert type(query) is SpyQuery


def test_build_payload_is_a_public_classmethod():
    """Third-party Document bases need the defaults-filling payload builder
    without reaching for the module-private _document_payload."""
    from fastplace.orm.documents import Document

    class Note(Document):
        title: str = "untitled"
        views: int = 0

    payload = Note._build_payload({"views": 3})
    assert payload == {"views": 3, "title": "untitled"}
