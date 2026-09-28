# Database & ORM

The Fastplace ORM wraps SQLAlchemy 2.x + Alembic in a fluent,
async-first API. You get typed models, relations with eager loading,
global scopes, domain events, and migrations — without writing mapping
boilerplate.

## Models

```python
from fastplace.orm import Field, Model, belongs_to, has_many


class Project(Model):
    __tablename__ = "projects"

    owner_id: int = Field(foreign_key="users.id", index=True)  # type: ignore[assignment]
    title: str
    slug: str = Field(unique=True)  # type: ignore[assignment]

    owner: "User" = belongs_to("User", back_populates="projects")  # type: ignore[assignment]
    tasks: list["Task"] = has_many("Task", back_populates="project")  # type: ignore[assignment]
```

Every model automatically carries `created_at` / `updated_at` /
`deleted_at` columns. Queries are lazy, immutable chains:

```python
projects = (
    await Project.where(Project.title.like("%fast%"))
    .order_by(Project.created_at.desc())
    .limit(20)
    .get()
)

project = await Project.find(42)
await project.update(title="renamed")  # mass-assignment guarded
await project.delete()  # soft: sets deleted_at
```

## Relations

`belongs_to`, `has_one`, `has_many`, `many_to_many` (+ `pivot_table`),
and polymorphic `morph_many` / `morph_one` / `morph_to` with a
process-global `morph_map`. Eager loading avoids N+1:

```python
projects = await Project.with_("tasks", "tasks.assignee").get()
```

Relationship loads honor the target model's **global scopes** — a row
hidden from direct queries (soft-deleted, archived, another tenant) cannot
come back through the relationship door.

## Global scopes

A scope is a named criteria applied to every query of a model. Soft delete
is scope #1 (`SoftDeleteScope`); the registry is MRO-merged and
extensible:

```python
class ArchivedScope(GlobalScope):
    def criteria(self, model):
        return [model.archived_at.is_(None)]


Project.add_global_scope("archived", ArchivedScope())
```

Escape per query — explicit, never ambient:

```python
await Project.without_global_scope("soft_delete").where(...).get()
await Project.only_deleted().get()  # ONLY deleted rows
```

## Mass assignment

`__fillable__` (allowlist) or `__guarded__` (denylist, raises
`MassAssignmentError`). Structural columns — soft-delete stamps, tenant
columns — are guarded by the framework itself; `create()` stamps them
from context, payloads cannot forge them.

## Persistence helpers

The day-to-day writes live on the model. Both locate-or-write helpers
locate by keyword attributes, and a race with a concurrent writer never
creates a duplicate:

```python
user = await User.first_or_create(
    {"name": "fallback"}, email="a@x.com"
)  # found rows keep their name
user = await User.update_or_create(
    {"name": "new name"}, email="a@x.com"
)  # found rows get the defaults applied
```

A writer that claims the unique key between the lookup and the insert
is detected via the constraint, rolled back to a savepoint, and the
winner's row is returned — exactly one row lands. `update_or_create`
requires at least one locate attribute: with none, it would match an
arbitrary row and rewrite it, so the call is refused.

Batch insert-or-update runs inside one transaction on every backend
(no dialect-specific grammar):

```python
written = await Project.upsert(
    [{"slug": "alpha", "stars": 3}, {"slug": "beta", "stars": 1}],
    unique_by=["slug"],            # or update=["stars"]
)
```

Rows pass the same mass-assignment guard as `create()`. Soft-deleted
rows match by physical key — the update repairs the row, the tombstone
stays. A key repeated later in the batch updates the row the earlier
entry inserted (last row wins). The match/write runs on the physical
key: global scopes (soft delete, tenancy) are query-side and neither
hide rows from `upsert` nor constrain what it writes — partition
multi-tenant data by putting the tenant column in `unique_by`. Costs
up to two statements per missed row: right for hundreds of rows, not
for bulk loads.

Counters run in the database, then reload the instance:

```python
await project.increment("stars")        # SET stars = stars + 1
await project.decrement("credits", 3)   # plain arithmetic, no floor
```

The target must be a numeric column (`int`, `float`, `Decimal`) —
arithmetic on a text column silently corrupts it, so anything else
raises `TypeError` before a write.

Large result sets stream through the query builder:

```python
async for row in Project.query().order_by(Project.id).chunk(500):
    ...   # OFFSET paging; you own the ORDER BY

async for row in Project.query().chunk_by_id(500):
    ...   # keyset paging — stable under concurrent writes

async for row in Project.query().cursor():
    ...   # server-side cursor where the driver has one
```

`chunk_by_id` is the recommended default: each page resumes after the
last primary key, so a row inserted mid-iteration appears in a later
page instead of shifting every offset. `cursor()` streams one row at a
time (PostgreSQL honors `stream_results`; SQLite buffers). Inside an
open transaction the stream joins it; standalone, the cursor owns its
own read session and closing it early — `break` — releases that
connection, wherever the generator is finalized.

## Migrations

```bash
fastplace make:model Invoice -m     # model + autogenerated migration
fastplace migrate                   # run pending
fastplace migrate:rollback          # revert the latest batch
fastplace migration:status          # Rich-rendered ledger
```

Autogenerate diffs against the live database; run it with the target
`DATABASE_URL` set (column types such as `VectorField` bake per backend —
JSON on SQLite, `VECTOR(dim)` on PostgreSQL).

## Seeders

Transactional, idempotent seeders live in `database/seeders/`:

```bash
fastplace db:seed
```

## Backends

| Backend | Driver | Notes |
|---|---|---|
| SQLite | aiosqlite | default, zero-config; serializing pool tuned for async |
| PostgreSQL | asyncpg (+ pgvector) | recommended for production & AI |
| MySQL | asyncmy | fully supported |
| MongoDB | pymongo (async) | separate `Document` adapter, native filters |

The capability registry (`db.capabilities.supports(...)`) lets code adapt
to the backend instead of guessing — e.g. native Row-Level Security is
claimed by PostgreSQL only.

## Documents (MongoDB)

```python
from fastplace.orm.documents import Document


class Article(Document):  # collection "articles"
    title: str = ""
    views: int = 0


await Article.where({"views": {"$gte": 3}}).sort("views", -1).limit(5).get()
```

The idiomatic escape hatch is a raw Mongo filter, not an emulation of SQL.
pymongo imports lazily — without the `mongodb` extra you get an actionable
use-time `ConfigurationError`, never an import crash.
