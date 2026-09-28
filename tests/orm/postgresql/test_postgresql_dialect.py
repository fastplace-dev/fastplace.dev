"""PostgreSQL dialect specifics — JSONB, native UUID, FTS, pgvector (T6.2).

Env-gated: exports ``TEST_POSTGRES_URL=postgresql://user:pass@host/db``
(the database is created/dropped by the suite's table lifecycle — use a
scratch database). Without the variable the whole module skips.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL not set — PostgreSQL dialect suite is opt-in",
)


@pytest.fixture()
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    monkeypatch.setenv("DATABASE_DRIVER", "postgresql")

    from fastplace.db import reset_db

    reset_db()
    yield

    from fastplace.db import db

    await db.drop_all()
    await db.dispose()


@pytest.fixture()
def Note(pg):
    from fastplace.orm import Field, Model

    class Note(Model):
        __tablename__ = "dialect_notes"

        id: int = Field(primary_key=True)
        title: str = ""
        body: str = ""
        uid: uuid.UUID = Field(default_factory=uuid.uuid4)
        meta: dict = Field(default_factory=dict)

    return Note


async def test_jsonb_columns_support_path_queries(pg, Note):
    from fastplace.db import db

    await db.create_all()
    await Note.create(title="a", meta={"kind": "tutorial", "lang": "en"})
    await Note.create(title="b", meta={"kind": "release", "lang": "bn"})

    hits = await Note.where(Note.meta["kind"].as_string() == "tutorial").get()
    assert [note.title for note in hits] == ["a"]


async def test_guid_stays_a_native_uuid(pg, Note):
    from fastplace.db import db

    await db.create_all()
    note = await Note.create(title="uid")

    fetched = await Note.find(note.id)
    assert isinstance(fetched.uid, uuid.UUID)
    assert fetched.uid == note.uid


async def test_full_text_search_matches_only_relevant_rows(pg, Note):
    from fastplace.db import db

    await db.create_all()
    await Note.create(title="sqlite basics", body="the zero-config embedded database")
    await Note.create(title="vector search", body="pgvector similarity for embeddings")

    hits = await Note.full_text_search("embedded database", limit=5)
    assert [note.title for note in hits] == ["sqlite basics"]


async def test_vector_search_orders_by_cosine_distance(pg):
    from fastplace.db import db
    from fastplace.orm import Field, Model

    class Embedding(Model):
        __tablename__ = "dialect_embeddings"

        id: int = Field(primary_key=True)
        label: str = ""
        embedding: list[float] = Field(type="vector", dimensions=3)

    await db.create_all()
    await Embedding.create(label="close", embedding=[0.1, 0.2, 0.3])
    await Embedding.create(label="far", embedding=[0.9, 0.8, 0.7])

    hits = await Embedding.vector_search([0.1, 0.2, 0.3], limit=1)
    assert [row.label for row in hits] == ["close"]


async def test_row_level_security_is_a_declared_postgres_capability(pg):
    from fastplace.db import db

    assert db.capabilities.supports_row_level_security is True
    assert db.capabilities.driver == "postgresql"


async def test_ann_index_created_with_opclass_and_serves_queries(pg):
    """VectorField(index=True) must produce a real HNSW index — not a btree."""
    from fastplace.db import db
    from fastplace.orm import Model, VectorField

    class Chunk(Model):
        __tablename__ = "dialect_ann_chunks"

        label: str = ""
        embedding: list[float] = VectorField(dimensions=3, index=True)

    await db.create_all()
    await Chunk.create(label="near", embedding=[0.1, 0.2, 0.3])
    await Chunk.create(label="far", embedding=[0.9, 0.8, 0.7])

    from sqlalchemy import text

    async with db.manager.engine().begin() as conn:
        definition = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'dialect_ann_chunks' "
                "AND indexname = 'ix_dialect_ann_chunks_embedding'"
            )
        )
        indexdef = definition.scalar_one()
        assert "USING hnsw" in indexdef, indexdef
        assert "vector_cosine_ops" in indexdef, indexdef

        # Force the planner's hand: with seqscan off, the ANN index must
        # serve the distance-ordered query (opclass matches cosine).
        await conn.exec_driver_sql("SET LOCAL enable_seqscan = off")
        plan = await conn.execute(
            text(
                "EXPLAIN SELECT id FROM dialect_ann_chunks "
                "ORDER BY embedding <=> '[0.1, 0.2, 0.3]'::vector LIMIT 5"
            )
        )
        plan_text = " ".join(row[0] for row in plan)
        assert "Index Scan using ix_dialect_ann_chunks_embedding" in plan_text, plan_text


async def test_vector_search_metric_threshold_and_distances(pg):
    from fastplace.db import db
    from fastplace.orm import Field, Model

    class Emb(Model):
        __tablename__ = "dialect_metric_emb"

        id: int = Field(primary_key=True)
        label: str = ""
        embedding: list[float] = Field(type="vector", dimensions=3)

    await db.create_all()
    await Emb.create(label="tenthm", embedding=[0.1, 0.0, 0.0])
    await Emb.create(label="ninetenths", embedding=[0.9, 0.0, 0.0])

    # l2 metric, distances returned ascending.
    pairs = await Emb.vector_search([0.0, 0.0, 0.0], metric="l2", return_distances=True)
    assert [row.label for row, _ in pairs] == ["tenthm", "ninetenths"]
    assert pairs[0][1] == pytest.approx(0.1, abs=1e-5)
    assert pairs[1][1] == pytest.approx(0.9, abs=1e-5)

    # Threshold filters the far hit out entirely.
    thresholded = await Emb.vector_search(
        [0.0, 0.0, 0.0], metric="l2", max_distance=0.5, return_distances=True
    )
    assert [row.label for row, _ in thresholded] == ["tenthm"]

    # inner_product orders by highest similarity (distance = -inner product).
    await Emb.create(label="aligned", embedding=[2.0, 0.0, 0.0])
    ip_pairs = await Emb.vector_search(
        [1.0, 0.0, 0.0], metric="inner_product", return_distances=True
    )
    assert [row.label for row, _ in ip_pairs][0] == "aligned"
    assert ip_pairs[0][1] == pytest.approx(-2.0, abs=1e-5)
