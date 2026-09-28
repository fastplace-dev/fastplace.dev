"""Search engines — the write/index surface over the query-only default.

``SearchService`` answers queries; a :class:`SearchEngine` is the thing that
KEEPS an index current (``update``/``delete``/``flush``). The framework ships
one engine — ``DatabaseSearchEngine``, query-only over PostgreSQL FTS — and a
process-wide registry so applications install a real engine (Meilisearch,
Typesense, an embedding store) without touching call sites. Records flow as
model instances or already-serialized dicts (``search_record``), so the same
engine object serves in-process calls and queue-worker payloads.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def _search_isolation():
    """Engine/enrollment registry isolation + model-table sweep per test."""
    from fastplace.db import reset_db
    from fastplace.events import reset_listeners
    from fastplace.queue import reset_queue
    from fastplace.search import reset_engine, reset_searchable
    from tests._registry import dispose_all_models, metadata_baseline, sweep_added_tables

    reset_db()
    dispose_all_models()
    baseline = metadata_baseline()
    reset_listeners()
    reset_queue()
    reset_engine()
    reset_searchable()
    yield
    dispose_all_models()
    sweep_added_tables(baseline)


@pytest.fixture()
def db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    return "sqlite+aiosqlite:///:memory:"


class FakeEngine:
    """Duck-typed engine — the shape a test double (or W6 fake) must have."""

    def __init__(self) -> None:
        self.updates: list[list[dict]] = []
        self.deletes: list[list[dict]] = []
        self.flushes: list[type] = []
        self.hits: list[str] = []

    async def update(self, records: list) -> int:
        self.updates.append([dict(r) for r in records])
        return len(records)

    async def delete(self, records: list) -> int:
        self.deletes.append([dict(r) for r in records])
        return len(records)

    async def flush(self, model: type) -> None:
        self.flushes.append(model)

    async def search(self, query: str, *, model: type | None = None, limit: int = 20) -> list:
        self.hits.append(query)
        return ["fake-hit"]


# ---------------------------------------------------------------------------
# the engine contract
# ---------------------------------------------------------------------------


def test_database_engine_satisfies_the_engine_abc():
    from fastplace.search import DatabaseSearchEngine, SearchEngine

    assert isinstance(DatabaseSearchEngine(), SearchEngine)


def test_engine_abc_cannot_be_partially_implemented():
    from fastplace.search import SearchEngine

    class HalfEngine(SearchEngine):
        async def update(self, records: list) -> int:
            return 0

    with pytest.raises(TypeError):
        HalfEngine()  # type: ignore[abstract]


def test_registry_defaults_to_the_database_engine():
    from fastplace.search import DatabaseSearchEngine, get_engine

    assert isinstance(get_engine(), DatabaseSearchEngine)


def test_registry_accepts_duck_typed_engines_and_resets():
    from fastplace.search import get_engine, register_engine, reset_engine

    fake = FakeEngine()
    register_engine(fake)  # type: ignore[arg-type]
    assert get_engine() is fake

    reset_engine()
    assert not isinstance(get_engine(), FakeEngine)


def test_registry_rejects_objects_missing_the_surface():
    from fastplace.search import register_engine

    class Stub:
        async def search(self, query: str, *, model: type | None = None, limit: int = 20) -> list:
            return []

    with pytest.raises(TypeError, match="update"):
        register_engine(Stub())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# the default engine is query-only
# ---------------------------------------------------------------------------


class RecordingModel:
    calls: list[tuple[str, dict]] = []

    @classmethod
    async def full_text_search(cls, query: str, *, limit: int = 20):
        cls.calls.append((query, {"limit": limit}))
        return [f"hit:{query}"]


@pytest.fixture()
def recorded():
    from fastplace.orm.capabilities import Capabilities

    RecordingModel.calls = []
    from fastplace.search import DatabaseSearchEngine

    return DatabaseSearchEngine(capabilities=Capabilities("postgresql"))


async def test_database_engine_search_delegates_to_full_text_search(recorded):
    hits = await recorded.search("embedded database", model=RecordingModel, limit=5)

    assert hits == ["hit:embedded database"]
    assert RecordingModel.calls == [("embedded database", {"limit": 5})]


async def test_database_engine_search_requires_a_model(recorded):
    with pytest.raises(ValueError, match="model"):
        await recorded.search("anything")


async def test_database_engine_search_gates_on_capabilities():
    from fastplace.orm.capabilities import Capabilities
    from fastplace.search import DatabaseSearchEngine, SearchNotSupported

    engine = DatabaseSearchEngine(capabilities=Capabilities("sqlite"))
    with pytest.raises(SearchNotSupported, match="sqlite"):
        await engine.search("anything", model=RecordingModel)


async def test_database_engine_refuses_index_writes(recorded):
    """Query-only is loud, never a silent no-op — a silent default would let
    every searchable model drift out of the index without a word."""
    from fastplace.search import SearchNotSupported

    with pytest.raises(SearchNotSupported, match="query-only"):
        await recorded.update([{"id": 1}])
    with pytest.raises(SearchNotSupported, match="query-only"):
        await recorded.delete([{"id": 1}])
    with pytest.raises(SearchNotSupported, match="query-only"):
        await recorded.flush(RecordingModel)


# ---------------------------------------------------------------------------
# searchable field declaration + record serialization
# ---------------------------------------------------------------------------


@pytest.fixture()
def post_model(db_url):
    from fastplace.orm import Field, Model

    class Post(Model):
        __tablename__ = "eng_posts"
        __searchable__ = ["title", "body"]

        id: int = Field(primary_key=True)
        title: str
        body: str = ""
        rating: float = 0.0

    return Post


def test_searchable_fields_returns_the_declared_columns(post_model):
    from fastplace.search import searchable_fields

    assert searchable_fields(post_model) == ("title", "body")


def test_searchable_fields_requires_the_declaration():
    from fastplace.orm import Field, Model
    from fastplace.search import NotSearchable, searchable_fields

    class Plain(Model):
        __tablename__ = "eng_plain"

        id: int = Field(primary_key=True)

    with pytest.raises(NotSearchable, match="__searchable__"):
        searchable_fields(Plain)


def test_searchable_fields_rejects_non_columns(post_model):
    from fastplace.search import NotSearchable, searchable_fields

    post_model.__searchable__ = ["title", "ghost"]
    with pytest.raises(NotSearchable, match="ghost"):
        searchable_fields(post_model)


async def test_search_record_serializes_pk_plus_declared_fields(db_url, post_model):
    from fastplace.db import db
    from fastplace.search import search_record

    await db.create_all()
    post = await post_model.create(title="hello", body="world", rating=4.5)

    assert search_record(post) == {
        "id": post.id,
        "title": "hello",
        "body": "world",
    }


def test_search_record_makes_values_json_safe(db_url):
    """Queued payloads ride JSON — datetimes (and friends) must not sneak in
    as Python-only objects."""
    from fastplace.orm import Field, Model
    from fastplace.search import search_record

    class Audit(Model):
        __tablename__ = "eng_audits"
        __searchable__ = ["label", "occurred_at"]

        id: int = Field(primary_key=True)
        label: str = ""
        occurred_at: datetime.datetime | None = None

    stamp = datetime.datetime(2026, 9, 28, 12, 0, tzinfo=datetime.UTC)
    audit = Audit()
    audit.id = 7
    audit.label = "login"
    audit.occurred_at = stamp

    assert search_record(audit) == {
        "id": 7,
        "label": "login",
        "occurred_at": "2026-09-28T12:00:00+00:00",
    }


def test_jsonable_covers_queue_payload_types():

    from fastplace.search import _jsonable

    stamp = datetime.datetime(2026, 9, 28, tzinfo=datetime.UTC)
    token = uuid4()
    assert _jsonable(stamp) == "2026-09-28T00:00:00+00:00"
    assert _jsonable(Decimal("1.5")) == "1.5"
    assert _jsonable(token) == str(token)
    assert _jsonable(b"raw") == "raw"
    assert _jsonable(None) is None
    assert _jsonable(3) == 3


def test_search_record_passes_dicts_through():
    from fastplace.search import search_record

    payload = {"id": 3, "title": "queued"}
    assert search_record(payload) == {"id": 3, "title": "queued"}
