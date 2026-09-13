"""Portable column types — UUID everywhere, capability-gated vector storage."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import CHAR, JSON, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID — CHAR(36) storage, uuid.UUID objects in Python."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(sqlalchemy_uuid())
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        if not isinstance(value, uuid.UUID):
            return str(uuid.UUID(value))
        return str(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return value
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def sqlalchemy_uuid() -> Any:
    """Native UUID on PostgreSQL."""
    from sqlalchemy.dialects.postgresql import UUID as PGUUID

    return PGUUID(as_uuid=True)


class PortableJSON(TypeDecorator):
    """JSON storage that upgrades to JSONB on PostgreSQL."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class VectorJSON(TypeDecorator):
    """Fallback vector storage as JSON for non-pgvector backends.

    Storage only — similarity search stays capability-gated (never emulated);
    ``Model.vector_search`` raises ``SearchCapabilityMissing`` without pgvector.
    """

    impl = JSON
    cache_ok = True


def pgvector_type(dimensions: int) -> Any:
    """pgvector Vector column type (PostgreSQL only)."""
    from pgvector.sqlalchemy import Vector

    return Vector(dimensions)


def vector_type_for(dimensions: int, url: str | None) -> Any:
    """Choose the vector column type for the configured backend."""
    if url and url.startswith(("postgresql", "postgres")):
        return pgvector_type(dimensions)
    return VectorJSON()
