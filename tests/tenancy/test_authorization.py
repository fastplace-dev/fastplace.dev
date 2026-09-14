"""Membership authorization — the service-layer tenant check.

Controllers stay thin; services call ``require_membership`` before touching
company data. Errors are FastplaceErrors, so the kernel answers 403 JSON.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
async def membership_world(backend):
    from fastplace_tenancy.models import Company, CompanyMembership

    from fastplace.db import db

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")
    await CompanyMembership.create(company_id=acme.id, user_id=7, role="owner")
    await CompanyMembership.create(company_id=acme.id, user_id=8, role="member")
    return acme, globex


async def test_member_passes_and_gets_the_row_back(membership_world):
    from fastplace_tenancy import require_membership

    acme, _ = membership_world
    row = await require_membership(7, acme.id)
    assert row.role == "owner"


async def test_non_member_is_rejected(membership_world):
    from fastplace_tenancy import NotCompanyMember, require_membership

    acme, _ = membership_world
    with pytest.raises(NotCompanyMember):
        await require_membership(999, acme.id)


async def test_role_gate_admits_and_rejects(membership_world):
    from fastplace_tenancy import CompanyRoleRequired, require_membership

    acme, _ = membership_world
    assert (await require_membership(7, acme.id, roles={"owner"})).role == "owner"
    with pytest.raises(CompanyRoleRequired):
        await require_membership(8, acme.id, roles={"owner", "admin"})


async def test_membership_in_one_company_does_not_grant_another(membership_world):
    from fastplace_tenancy import NotCompanyMember, require_membership

    _, globex = membership_world
    with pytest.raises(NotCompanyMember):
        await require_membership(7, globex.id)


async def test_company_defaults_to_the_bound_context(membership_world):
    from fastplace_tenancy import company_context, require_membership
    from fastplace_tenancy.context import MissingCompanyContext

    acme, _ = membership_world
    async with company_context(acme.id):
        assert (await require_membership(7)).company_id == acme.id
    with pytest.raises(MissingCompanyContext):
        await require_membership(7)
