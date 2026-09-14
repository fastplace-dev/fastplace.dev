"""RLS positive paths — PostgreSQL only (the capability registry's one
``row_level_security = True`` backend).

Caveat baked into PostgreSQL itself: policies do not apply to the table
owner (or roles with BYPASSRLS), and the test/CI user *creates* these
tables — so the suite asserts what is assertable here: the DDL lands, the
policy exists with the company predicate, and the connection setting the
policy reads round-trips. Deployment roles below the owner get the
row filtering; the package README documents that duty.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL not set — native RLS needs PostgreSQL",
)


@pytest.fixture()
def guarded(backend):
    from fastplace.db import db

    if not db.capabilities.supports("row_level_security"):
        pytest.skip("active backend has no native RLS — PostgreSQL only")
    from fastplace_tenancy.models import CompanyScopedModel

    class Secret(CompanyScopedModel):
        __tablename__ = "rls_secrets"

        title: str

    return Secret


async def test_policy_is_created_with_the_company_predicate(guarded):
    from fastplace_tenancy.rls import enable_company_rls, supports_rls

    from fastplace.db import db

    await db.create_all()
    assert supports_rls() is True
    await enable_company_rls(guarded)

    policies = await db.raw(
        "SELECT policyname, cmd FROM pg_policies WHERE tablename = 'rls_secrets'"
    )
    assert [(p["policyname"], p["cmd"]) for p in policies] == [
        ("fastplace_company_isolation", "ALL")
    ]
    # RLS itself is enabled on the table.
    enabled = await db.raw("SELECT relrowsecurity FROM pg_class WHERE relname = 'rls_secrets'")
    assert enabled[0]["relrowsecurity"] is True


async def test_enable_is_idempotent(guarded):
    from fastplace_tenancy.rls import enable_company_rls

    from fastplace.db import db

    await db.create_all()
    await enable_company_rls(guarded)
    await enable_company_rls(guarded)  # DROP POLICY IF EXISTS + CREATE
    policies = await db.raw("SELECT count(*) AS n FROM pg_policies WHERE tablename = 'rls_secrets'")
    assert policies[0]["n"] == 1


async def test_connection_setting_round_trips(guarded):
    from fastplace_tenancy.rls import clear_rls_company, set_rls_company

    from fastplace.db import db

    await db.create_all()
    async with db.connection():
        await set_rls_company(7)
        rows = await db.raw("SELECT current_setting('app.company_id', true) AS value")
        assert rows[0]["value"] == "7"

        await clear_rls_company()
        # missing_ok: unset returns NULL (the policy then matches nothing).
        rows = await db.raw("SELECT current_setting('app.company_id', true) AS value")
        assert rows[0]["value"] == ""


async def test_rls_setting_dies_with_the_transaction(guarded):
    """The setting must be transaction-local (SET LOCAL): a pooled connection
    returned to the pool carries nothing — the next borrower starts closed."""
    from fastplace_tenancy.rls import set_rls_company

    from fastplace.db import db

    await db.create_all()
    async with db.connection():
        await set_rls_company(7)

    # New scope; SQLAlchemy pools connections, so this may well be the SAME
    # physical connection — which is exactly the scenario that must be clean.
    async with db.connection():
        rows = await db.raw("SELECT current_setting('app.company_id', true) AS value")
        assert rows[0]["value"] == ""
