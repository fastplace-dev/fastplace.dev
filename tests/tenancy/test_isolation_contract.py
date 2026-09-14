"""The tenant-isolation contract — two companies, one code path.

Everything above (scope, create stamping, membership, queue context, cache
prefix) is a layer; this suite is the promise they compose into, on the
same portable matrix the core runs on: bound to company A, company B's rows
do not exist — not through any query entry point, not as job side effects,
not through cache keys. The blueprint calls this the package's own contract
suite layered on the framework matrix; this is it.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def Scoped(backend):
    from fastplace_tenancy.models import CompanyScopedModel

    class Document_(CompanyScopedModel):
        __tablename__ = "contract_documents"

        title: str

    return Document_


@pytest.fixture()
async def world(Scoped):
    from fastplace_tenancy import Company, company_context

    from fastplace.db import db

    await db.create_all()
    acme = await Company.create(name="Acme")
    globex = await Company.create(name="Globex")

    async with company_context(acme.id):
        await Scoped.create(title="acme-doc")
    async with company_context(globex.id):
        await Scoped.create(title="globex-doc")
        await Scoped.create(title="globex-secret")
    return Scoped, acme, globex


async def test_no_entry_point_sees_the_other_company(world):
    Scoped, acme, globex = world
    from fastplace_tenancy import company_context

    async with company_context(acme.id):
        acme_doc = await Scoped.first()
        assert [d.title for d in await Scoped.all()] == ["acme-doc"]
        assert await Scoped.count() == 1
        assert (await Scoped.first()).title == "acme-doc"
        assert (await Scoped.where(Scoped.title == "globex-secret").first()) is None

    async with company_context(globex.id):
        globex_ids = [d.id for d in await Scoped.query().order_by(Scoped.id).get()]

    async with company_context(acme.id):
        # find() by a foreign primary key — the classic IDOR probe — misses.
        for gid in globex_ids:
            assert (await Scoped.find(gid)) is None
        # ...while the tenant's own row still resolves.
        fetched = await Scoped.find(acme_doc.id)
        assert fetched is not None
        assert fetched.title == "acme-doc"


async def test_pagination_stays_inside_the_tenant(world):
    Scoped, acme, globex = world
    from fastplace_tenancy import company_context

    async with company_context(globex.id):
        page = await Scoped.query().order_by(Scoped.id).paginate(per_page=1)
        assert page.total == 2  # only Globex's rows are countable
    async with company_context(acme.id):
        page = await Scoped.query().order_by(Scoped.id).paginate(per_page=1)
        assert page.total == 1


async def test_soft_delete_composes_with_the_company_scope(world):
    Scoped, acme, globex = world
    from fastplace_tenancy import company_context

    async with company_context(acme.id):
        row = await Scoped.first()
        await row.delete()
        assert await Scoped.count() == 0

    async with company_context(globex.id):
        # The delete touched exactly one tenant's row.
        assert await Scoped.count() == 2

    assert len(await Scoped.without_global_scopes().get()) == 3  # soft row kept


async def test_job_side_effects_stay_inside_the_dispatching_company(world):
    """A queued write lands in the dispatching tenant even though the worker
    runs outside any request — the context rides in the job metadata."""
    Scoped, acme, globex = world
    from fastplace_tenancy import company_context
    from fastplace_tenancy.queue import TenantQueue, tenant_job

    from fastplace.queue import Job, MemoryQueue, reset_registry

    @Job(name="contract_doc_writer")
    @tenant_job
    async def write_doc(title: str):
        await Scoped.create(title=title)

    try:
        memory = MemoryQueue()
        async with company_context(acme.id):
            await TenantQueue(memory).dispatch("contract_doc_writer", title="from-job")
        await memory.run_pending()

        async with company_context(acme.id):
            assert [d.title for d in await Scoped.all() if d.title == "from-job"]
        async with company_context(globex.id):
            assert (await Scoped.where(Scoped.title == "from-job").first()) is None
    finally:
        reset_registry()


async def test_cache_keys_never_collide_across_companies(world):
    from fastplace_tenancy import company_context
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    store = CompanyCacheStore(MemoryCache())
    async with company_context(world[1].id):
        await store.put("contract:key", "acme")
    async with company_context(world[2].id):
        await store.put("contract:key", "globex")
        assert await store.get("contract:key") == "globex"
    async with company_context(world[1].id):
        assert await store.get("contract:key") == "acme"
