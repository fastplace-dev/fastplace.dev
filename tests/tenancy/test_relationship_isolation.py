"""Relationship loading under the company scope — the seam the contract
suite must cover (adversarial review: relation()/with_() used to bypass the
scope entirely, cross-company FK rows included)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def family(backend):
    from fastplace_tenancy.models import CompanyScopedModel

    from fastplace.orm import Field, belongs_to, has_many

    class Parent_(CompanyScopedModel):
        __tablename__ = "iso_parents"

        name: str

        children: list = has_many("Child", back_populates="parent")

    class Child(CompanyScopedModel):
        __tablename__ = "iso_children"

        label: str

        parent_id: int = Field(foreign_key="iso_parents.id")  # type: ignore[assignment]

        parent: Parent_ = belongs_to("Parent_", back_populates="children")  # type: ignore[assignment]

    return Parent_, Child


@pytest.fixture()
async def world(family):
    from fastplace_tenancy import Company, company_context

    from fastplace.db import db

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")
    Parent_, Child = family

    async with company_context(acme.id):
        parent = await Parent_.create(name="p")
        await Child.create(label="acme-child", parent_id=parent.id)
    async with company_context(globex.id):
        # A row nothing forbids: a Globex child pointing at Acme's parent
        # through the bare FK. It must not load through any door.
        await Child.create(label="globex-secret", parent_id=parent.id)
    return Parent_, Child, parent, acme, globex


async def test_relation_excludes_foreign_children(world):
    Parent_, Child, parent, acme, globex = world
    from fastplace_tenancy import company_context

    async with company_context(acme.id):
        loaded = await parent.relation("children")
        assert [c.label for c in loaded] == ["acme-child"]


async def test_relation_without_a_company_fails_closed(world):
    Parent_, Child, parent, *_ = world
    from fastplace_tenancy import MissingCompanyContext

    with pytest.raises(MissingCompanyContext):
        await parent.relation("children")


async def test_eager_loading_excludes_foreign_children(world):
    Parent_, Child, parent, acme, globex = world
    from fastplace_tenancy import company_context

    async with company_context(acme.id):
        rows = await Parent_.query().with_("children").get()
        assert len(rows) == 1
        assert [c.label for c in rows[0].children] == ["acme-child"]


async def test_relation_composes_with_soft_delete(world):
    Parent_, Child, parent, acme, _globex = world
    from fastplace_tenancy import company_context

    async with company_context(acme.id):
        victim = await Child.where(Child.label == "acme-child").first()
        await victim.delete()
        loaded = await parent.relation("children")
        assert loaded == []
