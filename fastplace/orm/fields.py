"""Field declarations and annotation→column mapping for the Fastplace ORM."""

from __future__ import annotations

import datetime
import decimal
import types as pytypes
import typing
from typing import Any, get_args, get_origin

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
)

from fastplace.orm.types import GUID, PortableDateTime, PortableJSON, vector_type_for

_MISSING = object()


class Field:  # noqa: A001 — deliberate public name (blueprint API)
    """Declarative column metadata for Fastplace models.

    ``Field(primary_key=True)``, ``Field(default=...)``, ``Field(unique=True)``,
    ``Field(foreign_key="users.id")`` — follows conventional schema vocabulary.
    """

    def __init__(
        self,
        *,
        primary_key: bool = False,
        default: Any = _MISSING,
        default_factory: Any = _MISSING,
        index: bool = False,
        unique: bool = False,
        nullable: bool | None = None,
        foreign_key: str | None = None,
        type: Any = None,  # noqa: A002 — blueprint API name
        dimensions: int | None = None,
        precision: int = 15,
        scale: int = 2,
        text: bool = False,
        server_default: Any = None,
        autoincrement: bool | None = None,
        ann: str | None = None,
        distance: str = "cosine",
        lists: int = 100,
        m: int = 16,
        ef_construction: int = 64,
    ) -> None:
        self.primary_key = primary_key
        self.default = default
        self.default_factory = default_factory
        self.index = index
        self.unique = unique
        self.nullable = nullable
        self.foreign_key = foreign_key
        self.type = type
        self.dimensions = dimensions
        self.precision = precision
        self.scale = scale
        self.text = text
        self.server_default = server_default
        self.autoincrement = autoincrement
        # Vector-only knobs (consumed when ``ann`` marks an ANN index).
        self.ann = ann
        self.distance = distance
        self.lists = lists
        self.m = m
        self.ef_construction = ef_construction

    def has_default(self) -> bool:
        return self.default is not _MISSING or self.default_factory is not _MISSING


_VECTOR_DISTANCES = ("cosine", "l2", "inner_product")


def VectorField(
    dimensions: int,
    *,
    index: bool | str = False,
    distance: str = "cosine",
    lists: int = 100,
    m: int = 16,
    ef_construction: int = 64,
) -> Field:
    """High-level vector column (blueprint §9).

    ``embedding: list[float] = VectorField(dimensions=1536)``. ``index=True``
    emits the default ANN index (HNSW, cosine distance); pass ``"ivfflat"``
    to choose IVF instead. ``distance`` must match the query metric —
    "cosine" (default), "l2", or "inner_product" — it picks the index
    opclass, and a mismatched opclass makes the index unusable for the
    query. On backends without native vectors the JSON fallback column
    gets no index at all (a btree over a JSON blob is dead weight).
    """
    if distance not in _VECTOR_DISTANCES:
        raise ValueError(
            f"unknown vector distance {distance!r} — use one of: cosine, l2, inner_product"
        )
    field = Field(
        type="vector",
        dimensions=dimensions,
        distance=distance,
        lists=lists,
        m=m,
        ef_construction=ef_construction,
    )
    if index:
        algorithm = "hnsw" if index is True else str(index)
        if algorithm not in ("hnsw", "ivfflat"):
            raise ValueError(f"unknown vector index algorithm {index!r} — use 'hnsw' or 'ivfflat'")
        field.ann = algorithm
    return field


_SIMPLE_TYPE_MAP: dict[Any, Any] = {
    int: Integer,
    str: String,
    bool: Boolean,
    float: Float,
    datetime.datetime: DateTime,
    datetime.date: Date,
    datetime.time: DateTime,  # rare; mapped conservatively
    decimal.Decimal: Numeric,
    bytes: LargeBinary,
}


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Return (base_type, is_optional) for ``X | None`` / Optional[X] annotations."""
    if typing.get_origin(annotation) is pytypes.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    origin = get_origin(annotation)
    if origin is typing.Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def resolve_annotation(annotation: Any, globalns: dict | None) -> Any:
    """Evaluate string annotations (``from __future__ import annotations`` modules).

    Security note: the eval'd string is the developer's own type annotation from
    their own module — identical trust level to Python importing that module,
    and the same resolution SQLAlchemy performs internally. It never touches
    runtime request data.
    """
    if isinstance(annotation, str):
        try:
            return eval(annotation, globalns or {})  # noqa: S307 — trusted module annotations
        except NameError:
            return annotation  # forward ref to a later-defined model — leave as string
    return annotation


def is_mapped_annotation(annotation: Any) -> bool:
    """True when the developer already used ``Mapped[...]`` explicitly."""
    if not isinstance(annotation, str):
        origin = typing.get_origin(annotation)
        return getattr(origin, "__name__", "") == "Mapped"
    # String annotations from future-import modules: check the textual prefix.
    return annotation.split("[", 1)[0].strip().endswith("Mapped")


def python_type_to_sa(annotation: Any, field: Field, url: str | None):
    """Map a Python annotation to a SQLAlchemy column type."""
    import uuid as _uuid

    base, _optional = _unwrap_optional(annotation)

    # explicit overrides
    if field.type is not None:
        if field.type == "vector":
            return vector_type_for(field.dimensions or 1536)
        if field.type == "text" or field.text:
            return Text()
        if field.type == "json":
            return PortableJSON()
        if isinstance(field.type, str):
            raise ValueError(f"Unknown Field(type='{field.type}')")
        return field.type  # already a SQLAlchemy type instance/class

    if base in _SIMPLE_TYPE_MAP:
        sa_type = _SIMPLE_TYPE_MAP[base]
        if base is str:
            if field.text:
                return Text()
            return String(255)
        if base is decimal.Decimal:
            return Numeric(field.precision, field.scale)
        if base is datetime.datetime:
            return PortableDateTime()
        if base is datetime.time:
            from sqlalchemy import Time as SATime

            return SATime()
        return sa_type()

    if base is _uuid.UUID:
        return GUID()

    origin = get_origin(base)
    if base is dict or origin in (dict, dict):
        return PortableJSON()
    if base in (list, set, tuple) or origin in (
        list,
        list,
        set,
        set,
        tuple,
        tuple,
    ):
        return PortableJSON()

    raise TypeError(
        f"Cannot map annotation {annotation!r} to a database column type; "
        "pass Field(type=...) with an explicit SQLAlchemy type."
    )
