"""CompanyScopedModel — the ORM layer of tenant isolation (blueprint §8).

The contract: with a company bound, every read is filtered and every create
stamped; without one, reads fail closed; the tenant column itself is guarded
from mass assignment; uniqueness can be scoped per company.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def models():
    from fastplace_tenancy.models import Company, CompanyMembership, CompanyScopedModel

    class Invoice(CompanyScopedModel):
        __tablename__ = "tenancy_invoices"

        title: str
        amount: int = 0

    return Company, CompanyMembership, CompanyScopedModel, Invoice


@pytest.fixture()
async def two_companies(models, backend):
    from fastplace.db import db

    Company, *_ = models[:1]
    await db.create_all()
    from fastplace_tenancy.context import company_context

    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")
    return acme, globex, company_context


async def test_scope_filters_reads_to_the_bound_company(models, two_companies):
    *_, Invoice = models
    acme, globex, company_context = two_companies

    async with company_context(acme.id):
        await Invoice.create(title="acme-1")
    async with company_context(globex.id):
        await Invoice.create(title="globex-1")
        assert [i.title for i in await Invoice.all()] == ["globex-1"]
        assert await Invoice.count() == 1


async def test_find_is_scoped_too(models, two_companies):
    """find(pk) is the classic IDOR vector — the scope must hold there."""
    *_, Invoice = models
    acme, globex, company_context = two_companies

    async with company_context(acme.id):
        acme_invoice = await Invoice.create(title="acme-only")
    async with company_context(globex.id):
        assert (await Invoice.find(acme_invoice.id)) is None


async def test_reads_without_a_company_context_fail_closed(models, two_companies):
    *_, Invoice = models
    acme, _, company_context = two_companies

    async with company_context(acme.id):
        await Invoice.create(title="acme-1")

    from fastplace_tenancy.context import MissingCompanyContext

    with pytest.raises(MissingCompanyContext):
        await Invoice.all()


async def test_create_stamps_company_from_context_and_requires_one(models, two_companies):
    *_, Invoice = models
    acme, _, company_context = two_companies

    from fastplace_tenancy.context import MissingCompanyContext

    with pytest.raises(MissingCompanyContext):
        await Invoice.create(title="nowhere")

    async with company_context(acme.id):
        row = await Invoice.create(title="stamped")
        assert row.company_id == acme.id


async def test_company_id_is_guarded_from_mass_assignment(models, two_companies):
    """A payload must not move a row into another company (IDOR at write time)."""
    *_, Invoice = models
    acme, globex, company_context = two_companies

    from fastplace.errors import MassAssignmentError

    async with company_context(acme.id):
        with pytest.raises(MassAssignmentError):
            await Invoice.create(title="forged", company_id=globex.id)


async def test_escape_hatch_is_explicit(models, two_companies):
    *_, Invoice = models
    acme, globex, company_context = two_companies

    async with company_context(acme.id):
        await Invoice.create(title="acme-1")
    async with company_context(globex.id):
        await Invoice.create(title="globex-1")

    # The package scope is an ordinary global scope — escape per query.
    everything = await Invoice.without_global_scope("company").order_by(Invoice.id).get()
    assert [i.title for i in everything] == ["acme-1", "globex-1"]
    # Soft delete still applies after escaping the company scope only.
    async with company_context(acme.id):
        victim = await Invoice.first()
        await victim.delete()
    remaining = await Invoice.without_global_scope("company").get()
    assert [i.title for i in remaining] == ["globex-1"]


async def test_membership_model_round_trips(models, two_companies):
    Company, CompanyMembership, _, _ = models
    acme, _, company_context = two_companies

    user_id = 4242  # int, matching the package column — asyncpg is strict
    await CompanyMembership.create(company_id=acme.id, user_id=user_id, role="owner")
    row = await CompanyMembership.where(CompanyMembership.user_id == user_id).first()
    assert row is not None and row.role == "owner"


def test_unique_per_company_builds_composite_constraint(backend):
    """``__unique_per_company__`` becomes UNIQUE(company_id, …) on the table."""
    from fastplace_tenancy.models import CompanyScopedModel

    class Ledger(CompanyScopedModel):
        __abstract__ = True

    class Account(Ledger):
        __tablename__ = "tenancy_accounts"

        title: str
        slug: str
        email: str

        __unique_per_company__ = (("slug",), ("email",))

    uniques = [
        tuple(col.name for col in constraint.columns)
        for constraint in Account.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("company_id", "slug") in uniques
    assert ("company_id", "email") in uniques


def test_index_per_company_builds_composite_index(backend):
    from fastplace_tenancy.models import CompanyScopedModel

    class Board(CompanyScopedModel):
        __abstract__ = True

    class Card(Board):
        __tablename__ = "tenancy_cards"

        title: str
        status: str

        __index_per_company__ = (("status",), ("created_at",))

    indexes = {
        index.name: [col.name for col in index.columns]
        for index in Board.metadata.tables["tenancy_cards"].indexes
    }
    composite = [cols for cols in indexes.values() if cols[0] == "company_id"]
    assert ["company_id", "status"] in composite
    assert ["company_id", "created_at"] in composite


async def test_unique_per_company_enforced_across_companies(models, backend):
    """The same natural key may exist twice — once per company."""
    from fastplace_tenancy.models import Company, CompanyScopedModel

    from fastplace.db import db

    class Invoice(CompanyScopedModel):
        __tablename__ = "tenancy_invoices2"

        title: str
        slug: str

        __unique_per_company__ = (("slug",),)

    from fastplace_tenancy.context import company_context

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")

    async with company_context(acme.id):
        await Invoice.create(title="a", slug="Q1")
    async with company_context(globex.id):
        await Invoice.create(title="g", slug="Q1")  # same slug, other company: fine

    from sqlalchemy.exc import IntegrityError

    async with company_context(acme.id):
        with pytest.raises(IntegrityError):
            await Invoice.create(title="dup", slug="Q1")


# ---------------------------------------------------------------------------
# Guard + convention robustness (adversarial review: subclass declarations
# used to silently drop the tenant guard or lose inherited conventions)
# ---------------------------------------------------------------------------
def test_tenant_guard_survives_subclass_overrides(backend):
    """A subclass's own __guarded__ narrows nothing it did not declare —
    company_id stays blocked no matter what the subclass says."""
    from fastplace_tenancy.models import CompanyScopedModel

    from fastplace.errors import MassAssignmentError

    class Row(CompanyScopedModel):
        __tablename__ = "guard_rows"

        title: str
        internal_note: str = ""

        __guarded__ = ("internal_note",)

    with pytest.raises(MassAssignmentError):
        Row._mass_assignable({"company_id": 9, "title": "x"})


def test_document_guard_survives_subclass_overrides():
    from fastplace_tenancy.documents import CompanyDocument

    from fastplace.errors import MassAssignmentError

    class Note(CompanyDocument):
        title: str = ""

        __guarded__ = ("secret",)

    with pytest.raises(MassAssignmentError):
        Note._mass_assignable({"company_id": 9})


def test_conventions_inherit_through_abstract_bases(backend):
    """An app-wide abstract base declaring tenant conventions covers every
    concrete model under it — nearest declaration wins, nothing is lost."""
    from fastplace_tenancy.models import CompanyScopedModel

    class Vault(CompanyScopedModel):
        __abstract__ = True

        __unique_per_company__ = (("slug",),)

    class Ledger(Vault):
        __tablename__ = "conv_ledgers"

        title: str
        slug: str

    uniques = [
        tuple(col.name for col in constraint.columns)
        for constraint in Ledger.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("company_id", "slug") in uniques


def test_conventions_preserve_table_args_forms(backend):
    """Both SQLAlchemy __table_args__ forms — a bare dict of table options
    and (constraints..., {options}) — survive the convention builder."""
    from fastplace_tenancy.models import CompanyScopedModel
    from sqlalchemy import UniqueConstraint

    class Card(CompanyScopedModel):
        __tablename__ = "conv_cards"

        title: str
        slug: str

        __index_per_company__ = (("slug",),)
        __table_args__ = {"mysql_engine": "InnoDB"}

    class Board(CompanyScopedModel):
        __tablename__ = "conv_boards"

        title: str
        slug: str

        __unique_per_company__ = (("slug",),)
        __table_args__ = (UniqueConstraint("ref"), {"mysql_engine": "InnoDB"})

        ref: str = ""

    args = Card.__table_args__
    assert isinstance(args, tuple) and isinstance(args[-1], dict)
    assert args[-1]["mysql_engine"] == "InnoDB"
    assert any(getattr(item, "name", "").startswith("ix_conv_cards_company") for item in args[:-1])

    board_args = Board.__table_args__
    assert isinstance(board_args[-1], dict) and board_args[-1]["mysql_engine"] == "InnoDB"
    names = [type(item).__name__ for item in board_args[:-1]]
    assert names.count("UniqueConstraint") >= 2  # the declared one + company pair
