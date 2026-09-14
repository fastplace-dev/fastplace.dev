# fastplace-tenancy

First-party, **opt-in** multi-tenancy for
[Fastplace](https://fastplace.dev). The core framework stays tenant-agnostic;
applications that need company scoping install this package and get the same
class of guarantees the core gives soft deletes — invariants enforced by the
framework, not by developer discipline.

    pip install fastplace-tenancy

## The isolation boundary

Single database, company-scoped rows: `Account → Company → company_id →
company-scoped records`. Every company-owned table carries a `company_id`
column, and this package keeps that column meaningful at every layer a
request touches (blueprint §8, "Multi-Tenant Data Architecture"):

| Layer | Strategy |
| :--- | :--- |
| ORM | Automatic `company` global scope on `CompanyScopedModel` |
| API | `CompanyContextMiddleware` binds the request to one company |
| Authorization | Membership checks (`require_membership`) for services |
| Database | PostgreSQL Row-Level Security helpers (`fastplace_tenancy.rls`) |
| Cache | `company:{id}:…` key prefix (`CompanyCacheStore`) |
| Queue | Company context carried in job metadata (`TenantQueue` + `@tenant_job`) |
| Files | Company prefixes under `storage/` with traversal guards |
| Search | Company filter applied over the active `SearchService` |
| Vector store | Company filter on similarity results |
| MongoDB | Automatic `company_id` filters (`CompanyDocument`) |

## Automatic company scoping

Company-owned models extend `CompanyScopedModel`; the package ships the
tenant entities themselves:

```python
from fastplace_tenancy import Company, CompanyMembership, CompanyScopedModel


class Project(CompanyScopedModel):
    __tablename__ = "projects"
    __unique_per_company__ = [("slug",)]  # UNIQUE(company_id, slug)

    title: str
    slug: str
```

Every read now receives the tenant filter — `SELECT … WHERE company_id = ?` —
and every `create()` stamps the column from the active company context. The
`company_id` column is guarded from mass assignment: a payload cannot move a
row into (or read it as) another company.

### Fail-closed by design

Querying a `CompanyScopedModel` **without** a company context raises
`MissingCompanyContext` — a missing tenant is a bug, not "return everything".
Cross-company work (admin shells, exports) is an explicit escape:

```python
await Project.without_global_scope("company").where(...)  # this query only
async with company_context(company.id):  # bind a context
    await Project.all()
```

## Request binding

```python
# config/app.py
MIDDLEWARE = [
    "app.http.middleware.resolve_user.ResolveUserMiddleware",
    "fastplace_tenancy.middleware.CompanyContextMiddleware",
    "app.http.middleware.csrf.CsrfMiddleware",
]
```

The middleware resolves the company for the authenticated user:

1. a session `company_id` — **validated against membership**. The stored
   value is a request, not a grant: it binds only when the user actually
   holds a membership in that company, otherwise it is ignored;
2. else the user's earliest membership (ordered by membership id — a
   documented key, not whatever the query planner returns first);
3. else nothing — the request stays unbound and company-scoped queries fail
   closed.

Controllers read the binding through the helper (there is no
`request.state.company_id`):

```python
from fastplace_tenancy.middleware import request_company_id

company_id = request_company_id(request)  # None when unbound
```

A company-switch endpoint should pair the session write with a membership
check, so a forged or stale value never even reaches the middleware:

```python
async def switch(self, request):
    body = await request.json()
    await require_membership(request.user.id, body["company_id"])
    request.session["company_id"] = body["company_id"]
```

Override `_resolve_company_id()` to source the company differently
(subdomain, header, JWT claim).

## Services check membership

```python
from fastplace_tenancy import require_membership


async def invite(request, company_id: int):
    await require_membership(request.user.id, company_id, roles={"owner", "admin"})
    ...
```

## Queue jobs carry the company

Wrap the driver once and decorate handlers; the context travels as job
metadata and re-binds inside the worker. Install the wrapper as the
process-wide queue so domain-event auto-dispatch flows through it too:

```python
from fastplace.queue import set_queue

from fastplace_tenancy import TenantQueue, tenant_job

set_queue(TenantQueue(existing_driver))


@Job()
@tenant_job
async def rebuild_report(project_id: int): ...
```

`@tenant_job` requires an `async def` handler — a sync function fails at
decoration with a `TypeError`, the same guard `@Job` itself applies.

## Multi-tenant indexing & uniqueness

High-volume company tables index with `company_id` first — the base model's
`company_id` column is indexed; declare composites with
`__index_per_company__`. Uniqueness distinguishes globally unique
(`Field(unique=True)`) from unique-within-a-company
(`__unique_per_company__` → `UNIQUE(company_id, …)`).

## Row-Level Security (PostgreSQL)

Where the backend supports native RLS (see the capability registry — MySQL
and SQLite never claim it), `fastplace_tenancy.rls` can push the same policy
into the database itself as defense-in-depth below the ORM scope:

```python
from fastplace_tenancy.rls import enable_company_rls, set_rls_company

await enable_company_rls(Project)  # once, at migration time

async with db.connection():  # per transaction
    await set_rls_company(company.id)
    ...
```

`set_rls_company` uses `SET LOCAL`, so the setting **dies with the
transaction** — a pooled connection carries nothing into the next borrower's
transaction. Deployment duties the DDL cannot do for you: the connecting role
must not own the table or carry `BYPASSRLS` (owners bypass policies
silently), and every transaction must set the company before touching
company tables.

## Tenant-isolation contract suite

`tests/tenancy/` is the contract: two companies, one code path — cross-company
reads come back empty, writes stamp the right tenant, cache keys never
collide, jobs re-bind their company, and uniqueness is per-company. The suite
runs on the same portable matrix as the core (SQLite always; PostgreSQL /
MySQL when `TEST_*_URL` is set).
