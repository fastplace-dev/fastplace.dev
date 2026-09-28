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


class MorphMany(RelationshipMarker):
    """``morph_many`` — a child table owned by several parent types.

    The join pairs the owner's primary key with the child's ``{x}_id`` column
    *and* matches the child's ``{x}_type`` column against this owner's type
    string (the owner's table name unless ``type_name=`` pins an alias).

    READ-ONLY (``viewonly=True``): the relationship is a query, not a
    collection. Appending to it is silently dropped by SQLAlchemy — the type
    string is part of the join, so an append cannot infer it. To create a
    child, set the pair explicitly::

        await Comment.create(body="…", commentable_type="posts", commentable_id=post.id)
    """

    _kind = "morph_many"
    _uselist = True

    def __init__(
        self,
        target: str,
        *,
        type_field: str,
        id_field: str,
        type_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(target, extra=extra)
        self.type_field = type_field
        self.id_field = id_field
        self.type_name = type_name

    def build(self, **extra: Any) -> Any:
        # Pop every framework-injected extra unconditionally — a leftover key
        # (e.g. when type_name= pins the alias) would leak into the
        # relationship() kwargs and crash mapper configuration.
        owner = extra.pop("_morph_owner", None)
        resolved_type = extra.pop("_morph_type_name", None)
        owner_pk = extra.pop("_morph_pk", "id")
        type_name = self.type_name or resolved_type
        if owner is None or type_name is None:
            raise ValueError(
                "morph relationships are built by the model machinery — "
                "declare them as class attributes, not inline"
            )
        # Owners are not guaranteed an ``id`` primary key — join on the pk the
        # model machinery resolved for this owner.
        join = (
            f"and_({owner}.{owner_pk} == foreign({self.target}.{self.id_field}), "
            f"{self.target}.{self.type_field} == {type_name!r})"
        )
        return super().build(
            primaryjoin=join,
            uselist=self._uselist,
            viewonly=True,
            **extra,
        )


class MorphOne(MorphMany):
    """``morph_one`` — the single-row shape of :class:`MorphMany`."""

    _kind = "morph_one"
    _uselist = False


class HasManyThrough(RelationshipMarker):
    """``has_many_through`` — walk an intermediate table to its far side.

    ``through=`` names the pivot/intermediate TABLE; its two foreign keys
    (one back to this owner, one forward to the target) define the walk, so
    the relationship is a read-only query (``viewonly=True``). Writes go to
    the intermediate model directly.
    """

    _kind = "has_many_through"
    _uselist = True

    def __init__(
        self,
        target: str,
        *,
        through: str,
        backref: str | None = None,
        back_populates: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(target, backref=backref, back_populates=back_populates, extra=extra)
        self.through = through

    def build(self, **extra: Any) -> Any:
        return super().build(uselist=self._uselist, viewonly=True, secondary=self.through, **extra)


class HasOneThrough(HasManyThrough):
    """``has_one_through`` — the single-row shape of :class:`HasManyThrough`.

    The intermediate table is expected to hold exactly one row per owner.
    """

    _kind = "has_one_through"
    _uselist = False


class MorphToMany(RelationshipMarker):
    """``morph_to_many`` — many-to-many across a polymorphic pivot.

    The pivot carries the owner's id (``id_field``), the target's id
    (``foreign_field``), and the owner's type string (``type_field``). The
    primaryjoin pairs the owner's primary key with ``id_field`` *and* pins
    ``type_field`` to the owner's morph type — its table name, or the
    ``type_name=`` alias chosen at declaration (the same string the owner
    side writes into the pivot; register it in ``morph_map`` so the reverse
    lookup resolves).

    READ-ONLY (``viewonly=True``): the type string is part of the join, so
    an append cannot infer it — create pivot rows explicitly.
    """

    _kind = "morph_to_many"

    def __init__(
        self,
        target: str,
        *,
        through: str,
        type_field: str,
        id_field: str,
        foreign_field: str,
        type_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(target, extra=extra)
        self.through = through
        self.type_field = type_field
        self.id_field = id_field
        self.foreign_field = foreign_field
        self.type_name = type_name

    def build(self, **extra: Any) -> Any:
        # Same machinery-injected extras as the other morph shapes (see
        # MorphMany.build) — popped unconditionally so nothing leaks through.
        owner = extra.pop("_morph_owner", None)
        resolved_type = extra.pop("_morph_type_name", None)
        owner_pk = extra.pop("_morph_pk", "id")
        type_name = self.type_name or resolved_type
        if owner is None or type_name is None:
            raise ValueError(
                "morph_to_many is built by the model machinery — "
                "declare it as a class attribute, not inline"
            )
        # The pivot is usually an unmapped Table: string expressions reach
        # its columns through ``<table>.c.<col>`` (the eval namespace for
        # relationship strings resolves Table names from the metadata).
        join = (
            f"and_({owner}.{owner_pk} == foreign({self.through}.c.{self.id_field}), "
            f"{self.through}.c.{self.type_field} == {type_name!r})"
        )
        secondaryjoin = f"{self.through}.c.{self.foreign_field} == foreign({self.target}.{_target_pk(self.target)})"
        # SQLAlchemy 2.1 auto-enables the omit_join selectin optimization
        # when the secondary's FKs cover the parent pk — that path filters
        # by local/remote pairs only and drops the owner-type discriminator
        # above, double-loading members owned by other types. False pins
        # the join-based selectin, which applies the full primaryjoin on
        # every 2.x (on 2.0 this is what the loader already chose).
        return super().build(
            primaryjoin=join,
            secondaryjoin=secondaryjoin,
            secondary=self.through,
            uselist=True,
            viewonly=True,
            omit_join=False,
            **extra,
        )


def _target_pk(target: str) -> str:
    """The target model's primary-key column name, resolved through the
    mapper registry — natural-key targets may not use ``id``. Falls back to
    ``id`` when the target is not (yet) registered, preserving lazy/forward
    references that SQLAlchemy resolves at configure time."""
    from sqlalchemy import exc as sa_exc
    from sqlalchemy import inspect as sa_inspect

    from fastplace.orm.model import Model

    target_cls = Model.registry._class_registry.get(target)
    if isinstance(target_cls, type):
        try:
            return sa_inspect(target_cls).primary_key[0].name
        except sa_exc.ArgumentError:  # NoInspectionAvailable — not yet mapped
            pass
    return "id"


class MorphTo:
    """``morph_to`` — the child side of a polymorphic pair.

    No static relationship exists (the parent type varies per row), so the
    marker records which columns carry the pair; ``await instance.morph_to()``
    resolves the parent through the framework's morph map.
    """

    _kind = "morph_to"

    def __init__(self, type_field: str, id_field: str) -> None:
        self.type_field = type_field
        self.id_field = id_field


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
    return HasOne(target, backref=backref, back_populates=back_populates, extra=kwargs)


def belongs_to(
    target: str,
    *,
    backref: str | None = None,
    back_populates: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return BelongsTo(target, backref=backref, back_populates=back_populates, extra=kwargs)


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


def morph_many(
    target: str,
    *,
    type_field: str,
    id_field: str,
    type_name: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return MorphMany(
        target, type_field=type_field, id_field=id_field, type_name=type_name, extra=kwargs
    )


def morph_one(
    target: str,
    *,
    type_field: str,
    id_field: str,
    type_name: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return MorphOne(
        target, type_field=type_field, id_field=id_field, type_name=type_name, extra=kwargs
    )


def morph_to(type_field: str, id_field: str) -> MorphTo:
    return MorphTo(type_field, id_field)


def has_many_through(
    target: str,
    *,
    through: str,
    backref: str | None = None,
    back_populates: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return HasManyThrough(
        target, through=through, backref=backref, back_populates=back_populates, extra=kwargs
    )


def has_one_through(
    target: str,
    *,
    through: str,
    backref: str | None = None,
    back_populates: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return HasOneThrough(
        target, through=through, backref=backref, back_populates=back_populates, extra=kwargs
    )


def morph_to_many(
    target: str,
    *,
    through: str,
    type_field: str,
    id_field: str,
    foreign_field: str,
    type_name: str | None = None,
    **kwargs: Any,
) -> RelationshipMarker:
    return MorphToMany(
        target,
        through=through,
        type_field=type_field,
        id_field=id_field,
        foreign_field=foreign_field,
        type_name=type_name,
        extra=kwargs,
    )


#: morph type string → model class. Keys must equal the values the owner
#: side writes into the child's type column (the owner's table name, or the
#: ``type_name=`` alias chosen when declaring the morph).
_MORPH_MAP: dict[str, Any] = {}


def morph_map(mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register (or read) the morph type-string → model registry.

    ``morph_map({"projects": Project})`` lets ``await comment.morph_to()``
    resolve a comment whose ``commentable_type == "projects"``. Call with no
    argument to read the current map.
    """
    if mapping is not None:
        _MORPH_MAP.update(mapping)
    return dict(_MORPH_MAP)


def reset_morph_map() -> None:
    """Clear the map — test isolation between model registries."""
    _MORPH_MAP.clear()


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
