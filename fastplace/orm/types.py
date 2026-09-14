"""Portable column types — UUID everywhere, capability-gated vector storage."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import CHAR, JSON, DateTime, TypeDecorator


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


class PortableDateTime(TypeDecorator):
    """Naive-UTC datetimes in Python, real instants in the database.

    The portable contract is naive UTC in / naive UTC out (blueprint:
    "UTC internally"). PostgreSQL's ``timestamptz`` alone needs help on
    both ends: it labels a naive bind value with the *session's* timezone
    (silently shifting 12:34 to a different wall time), and it returns
    tz-aware values. Label naive binds as UTC and fold aware loads back
    to naive UTC; SQLite and MySQL are naive end to end already, so the
    processors are no-ops there.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if (
            dialect.name == "postgresql"
            and isinstance(value, datetime.datetime)
            and value.tzinfo is None
        ):
            return value.replace(tzinfo=datetime.UTC)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if isinstance(value, datetime.datetime) and value.tzinfo is not None:
            return value.astimezone(datetime.UTC).replace(tzinfo=None)
        return value


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
