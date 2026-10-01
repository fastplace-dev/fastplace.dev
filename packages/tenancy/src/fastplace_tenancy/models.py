"""Company-scoped models — the ORM layer of tenant isolation.

``Company`` and ``CompanyMembership`` are the tenant entities;
``CompanyScopedModel`` is the abstract base company-owned models extend.
The company filter is a global ORM scope (blueprint §8 Query Scopes) — the
same engine soft delete rides — so every query entry point is covered
without a single hand-written ``where``.

Fail-closed: with no company bound, reads raise :class:`MissingCompanyContext`
rather than returning every tenant's rows. Escape explicitly with
``without_global_scope("company")`` (cross-company admin work) — exactly the
contract the core engine documents for ``soft_delete``.

Both tenant columns are plain ``int`` foreign keys by default. An application
can redeclare them with other key types (own annotations win over the base's,
same as any column inheritance), but that affects the ORM layer only: storage
paths (``company_<id>``) validate positive integers, broadcasting parses
``company.{id}.`` channel ids as ``int``, and the RLS helpers cast the
connection setting to ``int`` — keep tenant ids integer.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, UniqueConstraint

from fastplace.orm import Field, Model
from fastplace.orm.scopes import GlobalScope
from fastplace_tenancy.context import require_company_context


class CompanyScope(GlobalScope):
    """``WHERE company_id = <bound company>`` on every query."""

    def criteria(self, model: Any) -> list[Any]:
        company_id = require_company_context()
        return [model.company_id == company_id]


class Company(Model):
    """The tenant entity itself — accounts own companies, companies own rows."""

    __tablename__ = "companies"

    name: str


class CompanyMembership(Model):
    """Who belongs to which company, in which role.

    The unique pair keeps a user from holding two memberships in one company
    (role confusion); roles are free-form strings the application defines.
    """

    __tablename__ = "company_memberships"

    # The metaclass consumes these annotations into Mapped[...] columns, so
    # mypy sees a Field landing on an int — that is the framework idiom.
    company_id: int = Field(foreign_key="companies.id", index=True)  # type: ignore[assignment]
    user_id: int = Field(index=True)  # type: ignore[assignment]
    role: str = "member"

    __table_args__ = (UniqueConstraint("company_id", "user_id"),)


class CompanyScopedModel(Model):
    """Base for company-owned models — automatic scoping, guarded tenant column."""

    __abstract__ = True

    #: One registry entry, exactly like ``soft_delete`` on the core base —
    #: resolved through the same MRO merge, escapable with the same API.
    __global_scopes__: dict[str, Any] = {"company": CompanyScope()}

    #: The tenant column is structural — never mass-assignable. Payloads
    #: cannot move a row into another company; ``create()`` stamps it from
    #: the bound context instead.
    __guarded__ = ("company_id",)

    company_id: int = Field(foreign_key="companies.id", index=True)  # type: ignore[assignment]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # The tenant column's guard cannot be declared away: a subclass's own
        # __guarded__ (or a future __fillable__) narrows what IT added, never
        # the base's tenant protection — _mass_assignable reads plain class
        # attributes, so the merge has to happen here, at class definition.
        own = cls.__dict__.get("__guarded__", ())
        cls.__guarded__ = ("company_id", *(g for g in own if g != "company_id"))
        # Conventions first: they must land in __table_args__ before the
        # declarative machinery (Model.__init_subclass__) builds the table.
        if not cls.__dict__.get("__abstract__", False):
            _apply_tenant_conventions(cls)
        super().__init_subclass__(**kwargs)

    @classmethod
    def _prepare_write_values(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Stamp ``company_id`` from the bound context into every write.

        The single seam the core routes all writes through — ``create``,
        ``first_or_create``/``update_or_create``, and ``upsert`` each prepare
        their payloads here, so every entry point lands inside the bound
        company. An explicit ``company_id`` still hits the standard guard
        (:class:`MassAssignmentError` — a caller who can name another company
        must not be able to write into it); a missing binding fails closed
        with :class:`MissingCompanyContext` at the call, not later.
        """
        if "company_id" in values:
            # Guarded column — the base filter raises the framework's
            # standard MassAssignmentError, not a package variant.
            return super()._prepare_write_values(values)
        prepared = dict(super()._prepare_write_values(values))
        # Framework stamp, not mass assignment — applied after the guard.
        prepared["company_id"] = require_company_context()
        return prepared

    @classmethod
    async def upsert(
        cls,
        rows: list[dict[str, Any]],
        *,
        unique_by: list[str],
        update: list[str] | None = None,
    ) -> int:
        """Tenant-safe upsert — the company column joins the match keys.

        Core ``upsert`` matches on physical unique keys with global scopes
        off the table, so an unstamped match could land on (or create a
        duplicate against) another company's row. Forcing ``company_id`` to
        lead ``unique_by`` confines both the match and the insert to the
        bound company: the per-company unique conventions
        (``__unique_per_company__``) build exactly this composite, and the
        values the base prepares carry the context stamp.
        """
        keys = ["company_id", *(k for k in unique_by if k != "company_id")]
        return await super().upsert(rows, unique_by=keys, update=update)


def _nearest_declaration(cls: type, name: str) -> Any:
    """The nearest MRO declaration of a convention — leaf overrides base,
    mirroring how ``__global_scopes__`` and column inheritance resolve."""
    for klass in cls.__mro__:
        if name in klass.__dict__:
            return klass.__dict__[name]
    return None


def _apply_tenant_conventions(cls: type) -> None:
    """Translate the tenant index/uniqueness conventions into table args.

    ``__unique_per_company__ = (("slug",),)`` → ``UNIQUE(company_id, slug)``;
    ``__index_per_company__ = (("status",),)`` → ``INDEX(company_id, status)``.
    The tenant column always leads — that is what keeps a per-company lookup
    an index seek instead of a scan shared across tenants. Declarations on
    abstract bases cover every concrete model under them (nearest wins).
    """
    uniques: tuple[tuple[str, ...], ...] = _nearest_declaration(cls, "__unique_per_company__") or ()
    per_company_indexes: tuple[tuple[str, ...], ...] = (
        _nearest_declaration(cls, "__index_per_company__") or ()
    )
    if not uniques and not per_company_indexes:
        return

    existing = cls.__dict__.get("__table_args__", ())
    # SQLAlchemy accepts a bare options dict or (constraints..., {options});
    # both forms survive the rebuild.
    if isinstance(existing, dict):
        base: tuple[Any, ...] = ()
        table_kwargs: dict[str, Any] | None = dict(existing)
    elif isinstance(existing, tuple) and existing and isinstance(existing[-1], dict):
        base = existing[:-1]
        table_kwargs = dict(existing[-1])
    else:
        base = existing or ()
        table_kwargs = None
    # __tablename__ is only resolved later by the declarative machinery —
    # name indexes off the class (stable per model) instead.
    stem = (cls.__dict__.get("__tablename__") or cls.__name__.lower()).lower()
    constraints: tuple[Any, ...] = tuple(
        UniqueConstraint("company_id", *columns) for columns in uniques
    )
    composite_indexes = tuple(
        Index(f"ix_{stem}_company_{'_'.join(columns)}", "company_id", *columns)
        for columns in per_company_indexes
    )
    combined = base + constraints + composite_indexes
    if table_kwargs is not None:
        combined = (*combined, table_kwargs)  # type: ignore[assignment]
    # mypy cannot know the declarative machinery reads this back on the model.
    cls.__table_args__ = combined  # type: ignore[attr-defined]
