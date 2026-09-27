"""ANN index emission for vector columns (pgvector HNSW / IVF).

A vector column declaring ``index=True`` must never take the generic
btree — on PostgreSQL it emits a real ANN index (opclass + build
options); on backends without native vectors the JSON fallback column
gets no index at all instead of a useless one.
"""

from __future__ import annotations

import pytest

# Autouse fixtures: per-test db facade + model registry (tests/orm/conftest.py).


def _pg_url(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/ffw2_pg")
    monkeypatch.setenv("DATABASE_DRIVER", "postgresql")


def _indexes(table):
    return {index.name: index for index in table.indexes}


def _pg_options(index) -> dict:
    return index.dialect_options["postgresql"]


def test_vector_index_true_emits_default_hnsw_cosine(monkeypatch):
    from fastplace.orm import Model, VectorField

    _pg_url(monkeypatch)

    class Chunk(Model):
        __tablename__ = "w2_chunks_default"

        embedding: list[float] = VectorField(dimensions=3, index=True)

    index = _indexes(Chunk.__table__)["ix_w2_chunks_default_embedding"]
    assert _pg_options(index)["using"] == "hnsw"
    assert _pg_options(index)["ops"] == {"embedding": "vector_cosine_ops"}
    assert _pg_options(index)["with"] == {"m": 16, "ef_construction": 64}


def test_vector_index_ivfflat_with_metric_and_lists(monkeypatch):
    from fastplace.orm import Model, VectorField

    _pg_url(monkeypatch)

    class Chunk(Model):
        __tablename__ = "w2_chunks_ivf"

        embedding: list[float] = VectorField(dimensions=3, index="ivfflat", distance="l2", lists=32)

    index = _indexes(Chunk.__table__)["ix_w2_chunks_ivf_embedding"]
    assert _pg_options(index)["using"] == "ivfflat"
    assert _pg_options(index)["ops"] == {"embedding": "vector_l2_ops"}
    assert _pg_options(index)["with"] == {"lists": 32}


def test_inner_product_metric_maps_to_ip_opclass(monkeypatch):
    from fastplace.orm import Model, VectorField

    _pg_url(monkeypatch)

    class Chunk(Model):
        __tablename__ = "w2_chunks_ip"

        embedding: list[float] = VectorField(dimensions=3, index="hnsw", distance="inner_product")

    index = _indexes(Chunk.__table__)["ix_w2_chunks_ip_embedding"]
    assert _pg_options(index)["ops"] == {"embedding": "vector_ip_ops"}


def test_no_btree_ever_lands_on_a_vector_column(monkeypatch):
    """The bug: `index=True` used to emit a useless generic btree."""
    from fastplace.orm import Model, VectorField

    _pg_url(monkeypatch)

    class Chunk(Model):
        __tablename__ = "w2_chunks_nobtree"

        embedding: list[float] = VectorField(dimensions=3, index=True)

    table = Chunk.__table__
    vector_indexes = [i for i in table.indexes]
    assert vector_indexes, "the ANN index must exist"
    for index in vector_indexes:
        assert _pg_options(index).get("using") in ("hnsw", "ivfflat")


def test_non_postgres_backend_gets_no_index_at_all(monkeypatch):
    """The JSON fallback column must not carry a useless index."""
    from fastplace.orm import Model, VectorField

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    class Chunk(Model):
        __tablename__ = "w2_chunks_sqlite"

        embedding: list[float] = VectorField(dimensions=3, index=True)

    assert list(Chunk.__table__.indexes) == []


def test_no_index_declared_none_emitted(monkeypatch):
    from fastplace.orm import Model, VectorField

    _pg_url(monkeypatch)

    class Chunk(Model):
        __tablename__ = "w2_chunks_plain"

        embedding: list[float] = VectorField(dimensions=3)

    assert list(Chunk.__table__.indexes) == []


def test_unknown_algorithm_rejected_early(monkeypatch):
    from fastplace.orm import VectorField

    with pytest.raises(ValueError, match="hnsw|ivfflat"):
        VectorField(dimensions=3, index="annoy")


def test_unknown_distance_rejected_early(monkeypatch):
    from fastplace.orm import VectorField

    with pytest.raises(ValueError, match="cosine|l2|inner_product"):
        VectorField(dimensions=3, index=True, distance="manhattan")


def test_plain_btree_index_still_works_for_normal_columns(monkeypatch):
    """The vector carve-out must not change ordinary column behavior."""
    from fastplace.orm import Field, Model

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    class Post(Model):
        __tablename__ = "w2_posts_btree"

        title: str = Field(index=True)

    index = _indexes(Post.__table__)["ix_w2_posts_btree_title"]
    assert index is not None


# -- vector_search options (wire-free validation; ordering is live-tested
# -- in tests/orm/postgresql/test_postgresql_dialect.py) -----------------------


async def test_vector_search_rejects_unknown_metric(monkeypatch):
    """Bad metric is a caller bug, not an environment issue — reported even
    on a non-vector backend (validation precedes the capability gate)."""
    from fastplace.orm import Model, VectorField

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    class Chunk(Model):
        __tablename__ = "w2_chunks_metric"

        embedding: list[float] = VectorField(dimensions=3)

    with pytest.raises(ValueError, match="cosine|l2|inner_product"):
        await Chunk.vector_search([0.1, 0.2, 0.3], metric="manhattan")


async def test_vector_search_capability_gate_still_first_for_valid_metric(monkeypatch):
    from fastplace.orm import Model, VectorField

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    class Chunk(Model):
        __tablename__ = "w2_chunks_capgate"

        embedding: list[float] = VectorField(dimensions=3)

    from fastplace.errors import SearchCapabilityMissing

    with pytest.raises(SearchCapabilityMissing):
        await Chunk.vector_search([0.1, 0.2, 0.3], metric="l2")
