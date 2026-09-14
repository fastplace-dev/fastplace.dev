"""Per-layer isolation — cache, queue, storage, search, vector, documents, RLS.

Each layer gets the same treatment: the bound company is the only source of
truth, a missing binding fails closed, and cross-tenant data never crosses
the boundary (dropped loudly, never served).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------
async def test_cache_keys_are_company_prefixed():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    inner = MemoryCache()
    store = CompanyCacheStore(inner)

    async with company_context(7):
        await store.put("dashboards:main", {"rows": 1})
        assert await inner.get("company:7:dashboards:main") == {"rows": 1}
        assert await store.get("dashboards:main") == {"rows": 1}

    async with company_context(9):
        assert await store.get("dashboards:main") is None  # other tenant: miss
        await store.put("dashboards:main", {"rows": 2})
    async with company_context(7):
        assert await store.get("dashboards:main") == {"rows": 1}  # untouched


async def test_cache_requires_a_company():
    from fastplace_tenancy import MissingCompanyContext
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    store = CompanyCacheStore(MemoryCache())
    with pytest.raises(MissingCompanyContext):
        await store.get("anything")


async def test_cache_flush_is_refused():
    """One tenant must not clear every tenant's entries."""
    from fastplace_tenancy import company_context
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    store = CompanyCacheStore(MemoryCache())
    async with company_context(7):
        with pytest.raises(NotImplementedError, match="every tenant"):
            await store.flush()


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------
async def test_dispatch_carries_the_company_and_job_rebinds_it():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.queue import TenantQueue, tenant_job

    from fastplace.queue import Job, MemoryQueue, reset_registry

    seen: dict = {}

    @Job(name="tenancy_probe")
    @tenant_job
    async def probe(project_id: int):
        from fastplace_tenancy import current_company_id

        seen["company"] = current_company_id()
        seen["project_id"] = project_id

    try:
        memory = MemoryQueue()
        tenant_queue = TenantQueue(memory)
        async with company_context(7):
            await tenant_queue.dispatch("tenancy_probe", project_id=3)
        # Metadata rode along as an underscore kwarg, not a handler argument.
        assert memory.pending[0].kwargs["_company_id"] == 7
        assert "company" not in seen  # nothing ran yet

        await memory.run_pending()
        assert seen == {"company": 7, "project_id": 3}
    finally:
        reset_registry()


async def test_dispatch_without_a_company_fails_closed():
    from fastplace_tenancy import MissingCompanyContext
    from fastplace_tenancy.queue import TenantQueue, tenant_job

    from fastplace.queue import Job, MemoryQueue, reset_registry

    @Job(name="tenancy_closed")
    @tenant_job
    async def closed():
        return None

    try:
        with pytest.raises(MissingCompanyContext):
            await TenantQueue(MemoryQueue()).dispatch("tenancy_closed")
    finally:
        reset_registry()


async def test_undecorated_handler_names_the_problem():
    """A plain handler receiving a tenant dispatch gets a clear error, not a
    silent unbound run."""
    from fastplace_tenancy import company_context
    from fastplace_tenancy.queue import COMPANY_META_KEY, TenantQueue

    from fastplace.queue import Job, MemoryQueue, reset_registry

    @Job(name="tenancy_plain")
    async def plain(x: int):
        return x

    try:
        memory = MemoryQueue()
        async with company_context(7):
            await TenantQueue(memory).dispatch("tenancy_plain", x=1)
        await memory.run_pending()
        assert memory.failures[0].error.__class__.__name__ == "TypeError"
        assert COMPANY_META_KEY in str(memory.failures[0].error)
    finally:
        reset_registry()


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def test_storage_paths_live_under_the_company_subtree(tmp_path):
    from fastplace_tenancy.storage import company_root, company_storage_path

    path = company_storage_path(7, "invoices", "q1.pdf", root=tmp_path)
    assert path == (tmp_path / "company_7" / "invoices" / "q1.pdf").resolve()
    assert path.is_relative_to(company_root(7, root=tmp_path).resolve())


def test_storage_traversal_is_rejected(tmp_path):
    from fastplace_tenancy.storage import company_storage_path

    with pytest.raises(ValueError, match="escapes"):
        company_storage_path(7, "../company_8", "secret.pdf", root=tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        company_storage_path(7, "reports/../../..", "etc", root=tmp_path)


def test_storage_guard_checks_outside_paths(tmp_path):
    from fastplace_tenancy.storage import company_root, ensure_company_path

    inside = company_root(7, root=tmp_path) / "a.txt"
    outside = company_root(8, root=tmp_path) / "a.txt"
    assert ensure_company_path(str(inside), 7, root=tmp_path) == inside.resolve()
    with pytest.raises(ValueError, match="escapes"):
        ensure_company_path(str(outside), 7, root=tmp_path)


# ---------------------------------------------------------------------------
# search + vector
# ---------------------------------------------------------------------------
async def test_search_wrapper_drops_foreign_rows(caplog):
    from types import SimpleNamespace

    from fastplace_tenancy import company_context
    from fastplace_tenancy.search import TenantSearchService

    class LeakyService:
        async def search(self, query, *, model=None, limit=20):
            return [
                SimpleNamespace(body="mine", company_id=7),
                SimpleNamespace(body="theirs", company_id=9),
                SimpleNamespace(body="shared doc"),  # company-free: passes
            ]

    service = TenantSearchService(LeakyService())
    async with company_context(7):
        with caplog.at_level("WARNING", logger="fastplace_tenancy.search"):
            rows = await service.search("hello")
    assert [getattr(r, "body", None) for r in rows] == ["mine", "shared doc"]
    assert any("company 9" in record.message or "9" in record.message for record in caplog.records)


async def test_search_wrapper_requires_a_company():

    from fastplace_tenancy import MissingCompanyContext
    from fastplace_tenancy.search import TenantSearchService

    class AnyService:
        async def search(self, query, *, model=None, limit=20):
            return []

    with pytest.raises(MissingCompanyContext):
        await TenantSearchService(AnyService()).search("hello")


async def test_vector_wrapper_drops_foreign_neighbors():
    from types import SimpleNamespace

    from fastplace_tenancy import company_context
    from fastplace_tenancy.vectors import TenantVectorStore

    class LeakyStore:
        async def search(self, model_cls, embedding, limit=10):
            return [
                SimpleNamespace(text="mine", company_id=7),
                SimpleNamespace(text="theirs", company_id=13),
            ]

    async with company_context(7):
        rows = await TenantVectorStore(LeakyStore()).search(object(), [0.1, 0.2])
    assert [r.text for r in rows] == ["mine"]


# ---------------------------------------------------------------------------
# documents (filter injection is wire-free; round trips are server-gated)
# ---------------------------------------------------------------------------
async def test_document_where_injects_the_tenant_filter():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.documents import CompanyDocument

    class Note(CompanyDocument):
        title: str = ""

    async with company_context(5):
        query = Note.where(title="hello")
    assert query._filter == {"title": "hello", "company_id": 5}


def test_document_query_without_a_company_fails_closed():
    from fastplace_tenancy import MissingCompanyContext
    from fastplace_tenancy.documents import CompanyDocument

    class Note(CompanyDocument):
        title: str = ""

    with pytest.raises(MissingCompanyContext):
        Note.where(title="hello")


async def test_document_caller_filter_cannot_override_the_tenant():
    from fastplace_tenancy import company_context
    from fastplace_tenancy.documents import CompanyDocument

    class Note(CompanyDocument):
        title: str = ""

    async with company_context(5):
        query = Note.where({"company_id": 9, "title": "forged"})
    assert query._filter["company_id"] == 5  # the bound company wins


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
async def test_rls_refuses_on_backends_without_it(backend):
    from fastplace_tenancy import RLSNotSupported
    from fastplace_tenancy.rls import supports_rls

    from fastplace.db import db

    class Guarded:
        __tablename__ = "rls_guarded"

    if db.capabilities.supports("row_level_security"):
        pytest.skip("backend has native RLS — refusal path needs sqlite/mysql")
    assert supports_rls() is False
    with pytest.raises(RLSNotSupported, match="no native Row-Level"):
        from fastplace_tenancy.rls import enable_company_rls

        await enable_company_rls(Guarded)


# ---------------------------------------------------------------------------
# review-fix layer hardening
# ---------------------------------------------------------------------------
async def test_tenant_job_rejects_sync_handlers():
    """@Job's async guard must not be laundered — a sync fn under tenant_job
    fails at decoration with the same clarity, not at 3am in the worker."""
    from fastplace_tenancy.queue import tenant_job

    with pytest.raises(TypeError, match="async def"):

        @tenant_job
        def sync_handler(x: int):
            return x


async def test_cache_remember_keeps_the_protocol_defaults():
    """remember(key) is a legal CacheStore call — the wrapper must not make
    ttl/factory required positional arguments the protocol treats as optional."""
    from fastplace_tenancy import company_context
    from fastplace_tenancy.cache import CompanyCacheStore

    from fastplace.cache import MemoryCache

    store = CompanyCacheStore(MemoryCache())
    async with company_context(7):
        assert await store.remember("plain") is None
        assert await store.remember("made", factory=lambda: "v") == "v"


async def test_document_update_cannot_move_the_tenant():
    """query.update({'$set': {...}}) is a write path the class-level guard
    never sees — the tenant query type must refuse tenant-column writes."""
    from fastplace_tenancy import company_context
    from fastplace_tenancy.documents import CompanyDocument

    class Note(CompanyDocument):
        title: str = ""

    async with company_context(5):
        with pytest.raises(ValueError, match="company_id"):
            await Note.where(title="x").update({"$set": {"company_id": 9, "title": "t"}})
        with pytest.raises(ValueError, match="company_id"):
            await Note.where(title="x").update({"$set": {"nested": {"company_id": 9}}})


def test_storage_rejects_malformed_company_ids(tmp_path):
    """company_root() interpolates the id into a path — it must be a positive
    int, or the guard itself becomes the traversal."""
    from fastplace_tenancy.storage import company_root, company_storage_path

    with pytest.raises(ValueError, match="company id"):
        company_root("../etc", root=tmp_path)
    with pytest.raises(ValueError, match="company id"):
        company_root(0, root=tmp_path)
    with pytest.raises(ValueError, match="company id"):
        company_storage_path(-1, "a", root=tmp_path)


async def test_search_flags_untagged_rows(caplog):
    """A row with no company_id at all passes (shared content) — but loudly:
    on tenant tables the column is non-nullable, so untagged means a leaky
    index, and silence would hide it."""
    from types import SimpleNamespace

    from fastplace_tenancy import company_context
    from fastplace_tenancy.search import TenantSearchService

    class HalfLeakyService:
        async def search(self, query, *, model=None, limit=20):
            return [SimpleNamespace(body="shared doc")]

    async with company_context(7):
        with caplog.at_level("WARNING", logger="fastplace_tenancy.search"):
            rows = await TenantSearchService(HalfLeakyService()).search("hello")
    assert len(rows) == 1  # still served — shared content is a legal pattern
    assert any("no company" in r.message for r in caplog.records)


async def test_vector_store_flags_untagged_neighbors(caplog):
    """Symmetric with the search wrapper: an untagged neighbor is kept as
    shared content but flagged — silence would hide a leaky index."""
    from types import SimpleNamespace

    from fastplace_tenancy import company_context
    from fastplace_tenancy.vectors import TenantVectorStore

    class HalfLeakyStore:
        async def search(self, model_cls, embedding, limit=10):
            return [SimpleNamespace(text="shared chunk")]

    async with company_context(7):
        with caplog.at_level("WARNING", logger="fastplace_tenancy.vectors"):
            rows = await TenantVectorStore(HalfLeakyStore()).search(object(), [0.1])
    assert len(rows) == 1
    assert any("no company" in r.message for r in caplog.records)
