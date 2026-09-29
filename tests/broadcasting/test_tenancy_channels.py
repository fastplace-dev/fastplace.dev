"""Tenancy broadcasting — company-scoped channels + membership authorization.

``fastplace-tenancy`` is the only thing that knows what a company is: it
prefixes channels with the bound company context and vouches (or refuses)
for ``company.{id}.`` subscriptions at subscribe time. Core stays
tenant-agnostic — these tests pin the seam from the tenancy side, over the
same parametrized DB backends the rest of the package uses.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture()
async def sqlite_backend(monkeypatch):
    """A fresh in-memory database — membership rows are real ORM rows.

    The portable backend matrix (tests/tenancy/conftest.py) already covers
    the membership queries per dialect; these channel tests need one honest
    backend, not the matrix.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    from fastplace.db import reset_db

    reset_db()
    yield
    import sys

    from fastplace.db import db
    from tests._registry import dispose_models_under

    await db.drop_all()
    await db.dispose()
    # These tests map fastplace_tenancy's models; the orm suite's autouse
    # registry reset later disposes every mapped class but purges only
    # ``app.*`` from sys.modules. A cached-and-disposed fastplace_tenancy
    # would hand the tenancy suite corpse classes (NoInspectionAvailable),
    # so dispose and drop the modules — later suites re-import and redeclare.
    # Only the tenancy classes: repo app models cached at collection time by
    # other suites must keep their mappers.
    dispose_models_under("fastplace_tenancy")
    for name in [m for m in list(sys.modules) if m.startswith("fastplace_tenancy")]:
        del sys.modules[name]


@pytest.fixture()
async def company_world(sqlite_backend):
    from fastplace_tenancy.models import Company, CompanyMembership

    from fastplace.db import db

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")
    await CompanyMembership.create(company_id=acme.id, user_id=7, role="owner")
    return acme, globex


@pytest.fixture(autouse=True)
def _fresh_broadcasting():
    from fastplace import broadcasting

    broadcasting.reset_broadcasting()
    yield
    broadcasting.reset_broadcasting()


class TestTenantChannel:
    async def test_prefixes_with_the_bound_company(self, company_world):
        from fastplace_tenancy import company_context, tenant_channel

        acme, _ = company_world
        async with company_context(acme.id):
            assert tenant_channel("orders.9") == f"company.{acme.id}.orders.9"
            assert tenant_channel("presence.orders.9") == (f"company.{acme.id}.presence.orders.9")

    async def test_without_context_it_fails_loud(self, company_world):
        from fastplace_tenancy import MissingCompanyContext, tenant_channel

        with pytest.raises(MissingCompanyContext):
            tenant_channel("orders.9")


class TestTenantAuthorizer:
    async def test_member_of_the_company_subscribes(self, company_world):
        from fastplace_tenancy import install_tenant_broadcasting

        from fastplace.broadcasting import authorize_subscribe, parse_channel

        acme, _ = company_world
        install_tenant_broadcasting()
        member = SimpleNamespace(id=7)
        assert await authorize_subscribe(member, parse_channel(f"company.{acme.id}.orders.1"))

    async def test_non_member_of_the_company_is_denied(self, company_world):
        from fastplace_tenancy import install_tenant_broadcasting

        from fastplace.broadcasting import authorize_subscribe, parse_channel

        _, globex = company_world
        install_tenant_broadcasting()
        outsider = SimpleNamespace(id=7)  # member of Acme, not Globex
        assert not await authorize_subscribe(
            outsider, parse_channel(f"company.{globex.id}.orders.1")
        )

    async def test_anonymous_is_denied_on_company_channels(self, company_world):
        from fastplace_tenancy import install_tenant_broadcasting

        from fastplace.broadcasting import authorize_subscribe, parse_channel

        acme, _ = company_world
        install_tenant_broadcasting()
        assert not await authorize_subscribe(None, parse_channel(f"company.{acme.id}.orders.1"))

    async def test_non_company_channels_fall_through_untouched(self, company_world):
        # The authorizer abstains (None) on plain channels — the core's own
        # public/private rules keep deciding those.
        from fastplace_tenancy import install_tenant_broadcasting
        from fastplace_tenancy.broadcasting import tenant_channel_authorizer

        from fastplace.broadcasting import authorize_subscribe, parse_channel

        install_tenant_broadcasting()
        assert (
            await tenant_channel_authorizer(SimpleNamespace(id=7), parse_channel("orders.1"))
            is None
        )
        # A public channel stays public with tenancy installed.
        assert await authorize_subscribe(None, parse_channel("orders.1"))
