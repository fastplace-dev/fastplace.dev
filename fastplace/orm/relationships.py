"""Relationship declarations riding on SQLAlchemy's relationship machinery.

``has_many`` / ``has_one`` / ``belongs_to`` / ``many_to_many`` work from both
sides: eager loading at the query level (``Project.with_("tasks")``) and lazy
instance-level loading (``await project.relation("tasks")``). Any extra
keyword arguments (``remote_side``, ``primaryjoin``, ``viewonly``, ...) pass
straight through to SQLAlchemy's ``relationship()`` — the sanctioned route for
shapes the four markers do not name.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import relationship


class RelationshipMarker:
    """Declarative marker converted into a ``relationship()`` at mapping time."""

    _kind = "generic"

    def __init__(
        self,
        target: str,
        *,
        backref: str | None = None,
        back_populates: str | None = None,
        secondary: str | None = None,
        cascade: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.target = target
        self.backref = backref
        self.back_populates = back_populates
        self.secondary = secondary
        self.cascade = cascade
        self.extra = extra or {}

    def build(self, **extra: Any) -> Any:
        kwargs: dict[str, Any] = {
            # Attribute access on an unloaded relationship raises instead of
            # silently doing sync IO — load eagerly (`.with_(...)`) or through
            # `await instance.relation(...)`, which issues an explicit SELECT.
            "lazy": "raise",
        }
        if self.backref:
            kwargs["backref"] = self.backref
        if self.back_populates:
            kwargs["back_populates"] = self.back_populates
        if self.secondary:
            kwargs["secondary"] = self.secondary
        if self.cascade:
            kwargs["cascade"] = self.cascade
        kwargs.update(self.extra)
        kwargs.update(extra)
        return relationship(self.target, **kwargs)


class HasMany(RelationshipMarker):
    _kind = "has_many"

    def build(self, **extra: Any) -> Any:
        return super().build(uselist=True, **extra)


class HasOne(RelationshipMarker):
    _kind = "has_one"

    def build(self, **extra: Any) -> Any:
        return super().build(uselist=False, **extra)


class BelongsTo(RelationshipMarker):
    _kind = "belongs_to"

    def build(self, **extra: Any) -> Any:
        return super().build(uselist=False, **extra)


class ManyToMany(RelationshipMarker):
    _kind = "many_to_many"

    def build(self, **extra: Any) -> Any:
        return super().build(uselist=True, **extra)


def has_many(
    target: str,
    *,
    backref: str | None = None,
    back_populates: str | None = None,
    cascade: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return HasMany(
        target,
        backref=backref,
        back_populates=back_populates,
        cascade=cascade,
        extra=kwargs,
    )


def has_one(
    target: str,
    *,
    backref: str | None = None,
    back_populates: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return HasOne(
        target, backref=backref, back_populates=back_populates, extra=kwargs
    )


def belongs_to(
    target: str,
    *,
    backref: str | None = None,
    back_populates: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return BelongsTo(
        target, backref=backref, back_populates=back_populates, extra=kwargs
    )


def many_to_many(
    target: str,
    *,
    secondary: str,
    backref: str | None = None,
    back_populates: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return ManyToMany(
        target,
        secondary=secondary,
        backref=backref,
        back_populates=back_populates,
        extra=kwargs,
    )


def pivot_table(name: str, left_table: str, right_table: str):
    """Define a many-to-many pivot table in the shared metadata.

    Column names follow the convention ``{singular(table)}_id`` for each side.
    """
    from sqlalchemy import Column, ForeignKey, Integer, Table

    from fastplace.orm.model import Model

    return Table(
        name,
        Model.metadata,
        Column(
            f"{_singular(left_table)}_id",
            Integer,
            ForeignKey(f"{left_table}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        Column(
            f"{_singular(right_table)}_id",
            Integer,
            ForeignKey(f"{right_table}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def _singular(table: str) -> str:
    if table.endswith("ies") and len(table) > 3:
        return table[:-3] + "y"
    if table.endswith("s") and not table.endswith("ss"):
        return table[:-1]
    return table
