"""CompanyScopedModel write paths — the tenant stamp on every write entry
point, the TenantQueue stamp on every dispatch entry point, and the
CompanyCacheStore protocol surface.

create() stamped the tenant column from the start; first_or_create /
update_or_create / upsert used to bypass that stamp (unscoped INSERT, or a
physical-key UPDATE able to land on another company's row). This suite pins
the whole write surface: whatever the entry point, a row is written inside
the bound company and nowhere else, an explicit ``company_id`` is refused
through the standard guard, and a missing binding fails closed at the call —
not later, at the worker.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def Scoped(backend):
    from fastplace_tenancy.models import CompanyScopedModel

    from fastplace.orm import Field

    class Invoice(CompanyScopedModel):
        __tablename__ = "write_path_invoices"

        number: str
        amount: int = Field(default=0)
        #: same number may exist in different companies — never across rows
        #: of one company (the per-company unique convention).
        __unique_per_company__ = (("number",),)

    return Invoice


@pytest.fixture()
async def companies(Scoped):
    from fastplace_tenancy import Company

    from fastplace.db import db

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")
    return acme, globex


# ---------------------------------------------------------------------------
# first_or_create / update_or_create — locate-or-write inside the tenant
# ---------------------------------------------------------------------------
async def test_first_or_create_stamps_and_locates_within_the_company(Scoped, companies):
    from fastplace_tenancy import company_context

    acme, globex = companies
    async with company_context(globex.id):
        globex_row = await Scoped.first_or_create(number="INV-1")
        assert globex_row.company_id == globex.id

    async with company_context(acme.id):
        # Globex's INV-1 is invisible to the locate — a NEW acme row is
        # stamped and inserted, never a cross-tenant find.
        acme_row = await Scoped.first_or_create(number="INV-1")
        assert acme_row.company_id == acme.id
        assert acme_row.id != globex_row.id
        # Found path: the same call locates its own row.
        again = await Scoped.first_or_create(number="INV-1")
        assert again.id == acme_row.id


async def test_update_or_create_stamps_and_updates_within_the_company(Scoped, companies):
    from fastplace_tenancy import company_context

    acme, globex = companies
    async with company_context(acme.id):
        row = await Scoped.update_or_create(number="INV-2", defaults={"amount": 10})
        assert row.company_id == acme.id
        updated = await Scoped.update_or_create(number="INV-2", defaults={"amount": 20})
        assert updated.id == row.id
        assert updated.amount == 20

    async with company_context(globex.id):
        globex_row = await Scoped.update_or_create(number="INV-2", defaults={"amount": 99})
        assert globex_row.id != row.id
        assert globex_row.company_id == globex.id

    async with company_context(acme.id):
        refetch = await Scoped.find(row.id)
        assert refetch.amount == 20  # globex's write never touched acme's row


async def test_locate_or_write_refuses_an_explicit_company_id(Scoped, companies):
    from fastplace_tenancy import company_context

    from fastplace.errors import MassAssignmentError

    acme, _ = companies
    async with company_context(acme.id):
        with pytest.raises(MassAssignmentError):
            await Scoped.first_or_create(company_id=acme.id, number="INV-3")
        with pytest.raises(MassAssignmentError):
            await Scoped.update_or_create(company_id=acme.id, number="INV-3")


async def test_locate_or_write_without_a_company_fails_closed(Scoped, companies):
    from fastplace_tenancy import MissingCompanyContext

    with pytest.raises(MissingCompanyContext):
        await Scoped.first_or_create(number="INV-4")
    with pytest.raises(MissingCompanyContext):
        await Scoped.update_or_create(number="INV-4", defaults={"amount": 1})


# ---------------------------------------------------------------------------
# upsert — batch writes match and write inside the tenant
# ---------------------------------------------------------------------------
async def test_upsert_is_tenant_safe_on_scoped_models(Scoped, companies):
    from fastplace_tenancy import company_context

    acme, globex = companies
    async with company_context(acme.id):
        assert await Scoped.upsert([{"number": "INV-9", "amount": 1}], unique_by=["number"]) == 1

    async with company_context(globex.id):
        # Same natural key, other company: a NEW globex row — the physical
        # match is confined to the bound company, so acme's row is untouchable.
        assert await Scoped.upsert([{"number": "INV-9", "amount": 5}], unique_by=["number"]) == 1
        assert [r.company_id for r in await Scoped.all()] == [globex.id]

    async with company_context(acme.id):
        rows = await Scoped.all()
        assert len(rows) == 1
        assert rows[0].amount == 1  # acme's row survived globex's batch
        # A repeat upsert updates acme's OWN row.
        assert await Scoped.upsert([{"number": "INV-9", "amount": 7}], unique_by=["number"]) == 1
        assert (await Scoped.first()).amount == 7


async def test_upsert_repairs_the_own_companys_soft_deleted_row(Scoped, companies):
    """Core upsert matches soft-deleted rows by physical key; on a scoped
    model that match stays inside the tenant — the tombstone is repaired,
    never freed for another company to claim."""
    from fastplace_tenancy import company_context

    acme, _ = companies
    async with company_context(acme.id):
        row = await Scoped.create(number="INV-5", amount=3)
        await row.delete()
        assert await Scoped.upsert([{"number": "INV-5", "amount": 9}], unique_by=["number"]) == 1
        repaired = await Scoped.with_deleted().where(Scoped.number == "INV-5").first()
        assert repaired is not None
        assert repaired.id == row.id
        assert repaired.amount == 9
        assert repaired.deleted_at is not None  # tombstone stays, core semantics


async def test_upsert_refuses_an_explicit_company_id(Scoped, companies):
    from fastplace_tenancy import company_context

    from fastplace.errors import MassAssignmentError

    acme, _ = companies
    async with company_context(acme.id):
        with pytest.raises(MassAssignmentError):
            await Scoped.upsert([{"number": "INV-8", "company_id": acme.id}], unique_by=["number"])


async def test_upsert_without_a_company_fails_closed(Scoped, companies):
    from fastplace_tenancy import MissingCompanyContext

    with pytest.raises(MissingCompanyContext):
        await Scoped.upsert([{"number": "INV-7"}], unique_by=["number"])


async def test_upsert_update_narrowing_still_applies(Scoped, companies):
    from fastplace_tenancy import company_context

    acme, _ = companies
    async with company_context(acme.id):
        await Scoped.create(number="INV-6", amount=3)
        await Scoped.upsert(
            [{"number": "INV-6", "amount": 50}], unique_by=["number"], update=["amount"]
        )
        assert (await Scoped.first()).amount == 50


# ---------------------------------------------------------------------------
# TenantQueue — every dispatch entry point stamps, not just dispatch()
# ---------------------------------------------------------------------------
async def test_job_builder_dispatch_carries_the_company():
    """The core reliability builder (``queue().job(...).dispatch(...)``) must
    flow the tenant stamp — the wrapper is the process-wide queue in the
    documented install (``set_queue(TenantQueue(driver))``)."""
    from fastplace_tenancy import MissingCompanyContext, company_context
    from fastplace_tenancy.queue import TenantQueue, tenant_job

    from fastplace.queue import Job, MemoryQueue, queue, reset_queue, reset_registry, set_queue

    seen: dict = {}

    @Job(name="wp_probe")
    @tenant_job
    async def probe(x: int):
        from fastplace_tenancy import current_company_id

        seen["company"] = current_company_id()
        seen["x"] = x

    set_queue(TenantQueue(MemoryQueue()))
    try:
        async with company_context(7):
            await queue().job("wp_probe", retries=2).dispatch(x=1)
        memory = queue()._inner  # noqa: SLF001 — asserting the stamped payload
        assert memory.pending[0].kwargs["_company_id"] == 7
        assert memory.pending[0].kwargs["x"] == 1

        await memory.run_pending()
        assert seen == {"company": 7, "x": 1}

        # No bound company: the builder path fails closed at dispatch time.
        with pytest.raises(MissingCompanyContext):
            await queue().job("wp_probe").dispatch(x=2)
    finally:
        reset_queue()
        reset_registry()


async def test_dispatch_many_stamps_every_payload():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.queue import TenantQueue, tenant_job

    from fastplace.queue import Job, MemoryQueue, reset_registry

    seen: list[int] = []

    @Job(name="wp_many")
    @tenant_job
    async def many(x: int):
        from fastplace_tenancy import current_company_id

        seen.append(current_company_id())

    memory = MemoryQueue()
    tenant_queue = TenantQueue(memory)
    try:
        async with company_context(7):
            await tenant_queue.dispatch_many([("wp_many", {"x": 1}), ("wp_many", {"x": 2})])
        assert [p.kwargs["_company_id"] for p in memory.pending] == [7, 7]

        await memory.run_pending()
        assert seen == [7, 7]
    finally:
        reset_registry()


# ---------------------------------------------------------------------------
# CompanyCacheStore — the full CacheStore protocol, prefixed
# ---------------------------------------------------------------------------
async def test_cache_store_satisfies_the_protocol():
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import CacheStore, MemoryCache

    assert isinstance(CompanyCacheStore(MemoryCache()), CacheStore)


async def test_cache_increment_and_ttl_are_company_prefixed():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    inner = MemoryCache()
    store = CompanyCacheStore(inner)

    async with company_context(7):
        await store.put("session", {"u": 1}, ttl=60)
        remaining = await store.ttl("session")
        assert remaining is not None and 0 < remaining <= 60
        assert await store.increment("hits") == 1
        assert await store.increment("hits") == 2
        assert await inner.get("company:7:hits") == 2  # the prefixed key

    async with company_context(9):
        # Another tenant's counter and keys are invisible — separate names.
        assert await store.increment("hits") == 1
        assert await store.ttl("session") is None


async def test_cache_locks_are_company_namespaced():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    inner = MemoryCache()
    store = CompanyCacheStore(inner)

    async with company_context(7):
        holder7 = store.lock("report", ttl=30)
        assert holder7.name == "company:7:report"
        assert await holder7.acquire() is True

    async with company_context(9):
        # Same logical name, other company: a DIFFERENT physical lock —
        # tenant 9 builds its report while tenant 7's build still holds.
        holder9 = store.lock("report", ttl=30)
        assert holder9.name == "company:9:report"
        assert await holder9.acquire() is True
        assert await holder9.release() is True

    async with company_context(7):
        assert await holder7.release() is True
