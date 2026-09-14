# Multi-tenancy

Company scoping is **opt-in** and lives in a sibling package — the core
framework stays tenant-agnostic until you install it:

```bash
pip install fastplace-tenancy
```

One database, company-scoped rows (`Account → Company → company_id →
company-owned records`), isolated at every layer a request touches.

## The model base

```python
from fastplace_tenancy import Company, CompanyMembership, CompanyScopedModel


class Project(CompanyScopedModel):
    __tablename__ = "projects"
    __unique_per_company__ = [("slug",)]  # UNIQUE(company_id, slug)

    title: str
    slug: str
```

From the moment a model extends `CompanyScopedModel`:

- every query carries `WHERE company_id = <bound company>` (a global scope
  riding the same engine as soft delete — including relationship and
  eager loads);
- `create()` stamps `company_id` from the bound context;
- `company_id` is guarded from mass assignment **and cannot be unguarded**
  by a subclass — a payload can never move a row into another company.

Unbound context fails closed: reads raise `MissingCompanyContext`, never
"return everything". Cross-company work (admin shells, exports) is an
explicit escape:

```python
await Project.without_global_scope("company").where(...)
async with company_context(company.id):
    await Project.all()
```

## Binding the request

```python
# config/app.py — after ResolveUserMiddleware, before CSRF
MIDDLEWARE = [
    "fastplace.auth.middleware.ResolveUserMiddleware",
    "fastplace_tenancy.middleware.CompanyContextMiddleware",
    "fastplace.auth.middleware.CsrfMiddleware",
]
```

Resolution order: a session `company_id` **validated against membership**
(a stored value is a request, not a grant — non-members fall back to
their real membership), else the user's earliest membership, else
unbound. Controllers read it through `request_company_id(request)`;
override `_resolve_company_id()` to source the company from a subdomain,
header, or JWT claim.

```python
# a switch endpoint pairs the write with a membership check
async def switch(self, request):
    body = await request.json()
    await require_membership(request.user.id, body["company_id"])
    request.session["company_id"] = body["company_id"]
```

## Every other layer

| Layer | What you get |
|---|---|
| Authorization | `require_membership(user_id, company_id, roles={...})` |
| Cache | `CompanyCacheStore` — `company:{id}:` keys, `flush()` refused |
| Queue | `TenantQueue` + `@tenant_job` — company rides job metadata, re-binds in the worker |
| Files | `company_root` / `company_storage_path` — validated ids, traversal guards |
| Search | `TenantSearchService` — foreign rows dropped + logged |
| Vectors | `TenantVectorStore` — same contract for neighbors |
| MongoDB | `CompanyDocument` — tenant filter on every query; `update()` refuses `company_id` at any depth |
| Database | RLS helpers (below) |

## Row-Level Security (PostgreSQL)

Defense-in-depth *below* the ORM scope:

```python
from fastplace_tenancy.rls import enable_company_rls, set_rls_company

await enable_company_rls(Project)  # once, at migration time

async with db.connection():  # per transaction
    await set_rls_company(company.id)
```

`set_rls_company` uses `SET LOCAL` — the setting dies with the
transaction, so a pooled connection never carries a tenant into the next
borrower's request. Deployment duties: the connecting role must not own
the table or carry `BYPASSRLS` (owners bypass policies silently).

## Conventions

- `__unique_per_company__ = [("slug",)]` → `UNIQUE(company_id, slug)`
- `__index_per_company__ = [("status",)]` → `INDEX(company_id, status)`

The tenant column always leads — a per-company lookup stays an index
seek, not a cross-tenant scan. Declarations inherit through abstract
bases (nearest wins), and both `__table_args__` forms survive.

## The contract suite

`tests/tenancy/` in the framework repo is the isolation contract: two
companies, one code path — cross-company reads come back empty, writes
stamp the right tenant, cache keys never collide, jobs re-bind their
company. Run it against your own tenant-aware models the same way.
