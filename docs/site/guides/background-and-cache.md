# Background jobs & cache

## Jobs

A job is an async function registered with `@Job()` in `app/jobs/`:

```python
from fastplace.queue import Job


@Job()
async def rebuild_report(project_id: int):
    report = await BuildReport(project_id).handle()
    await cache().put(f"reports:{project_id}", report, ttl=600)
```

Dispatch from services — never await the work inline:

```python
from fastplace.queue import queue

await queue().dispatch("rebuild_report", project_id=project.id)
```

Drivers follow configuration (`QUEUE_DRIVER`): the in-process **memory**
driver (default — drains at graceful shutdown, fine for dev and small
apps) and **SAQ + Redis** for real workers (`pip install "fastplace[queue]"`).

### The retry envelope

Every dispatch carries a reliability envelope — how many times a job may
run, how long each attempt gets, how attempts space out, how long the
result is kept. The four `QUEUE_*` env keys set the framework defaults:

| Key | Default | Meaning |
|---|---|---|
| `QUEUE_TRIES` | `3` | Total attempts per job (>= 1) — a job swept up by a worker crash stays retryable |
| `QUEUE_TIMEOUT` | `60` | Per-attempt seconds (> 0) — replaces SAQ's hidden 10s default |
| `QUEUE_BACKOFF` | `0` | Exponential base delay in seconds between attempts (>= 0; `0` = off) |
| `QUEUE_TTL` | `600` | Result retention seconds (>= 1) |

Each layer can override: builder (per dispatch) > `@Job(...)` params >
`QUEUE_*` env > framework default. `@Job(retries=..., timeout=...,
backoff=...)` pins one handler's envelope without touching global config:

```python
@Job(retries=5, timeout=120, backoff=2)
async def rebuild_report(project_id: int): ...
```

One dispatch can raise the envelope further still — the builder entry
(handler kwargs and reliability options never collide there):

```python
await queue().job("rebuild_report", retries=8, backoff=5).dispatch(project_id=42)
```

Misconfigured values (a zero timeout, `QUEUE_TRIES=0`, `QUEUE_TTL=0`)
fail loudly at dispatch with a `ConfigurationError` instead of
misbehaving server-side. `fastplace queue:health` surfaces the resolved
values.

### Domain events → queue

Models declare which events dispatch where:

```python
class Invoice(Model):
    __dispatches__ = {"created": "invoice_created"}
```

Ordering is the contract: events are buffered until the owning
transaction commits, discarded on rollback, and an auto-enqueue failure
never fails the committed write. Explicit dispatch stays strict
(`to_queue=True` raises if the queue is unavailable).

### Installing a custom queue

```python
from fastplace.queue import set_queue

set_queue(my_driver)  # process-wide; domain-event dispatch rides it
```

This is exactly how `fastplace-tenancy` installs `TenantQueue` — every
dispatch (explicit or event-driven) stamps the company and the worker
re-binds it.

## Cache

One protocol, several stores (`CACHE_DRIVER`: `memory`, `redis`,
`database`) — `config/cache.py` and `.env.example` are the canonical key
list:

```python
from fastplace.cache import cache

await cache().put("reports:42", payload, ttl=600)
value = await cache().get("reports:42")  # None on miss
await cache().forget("reports:42")

# compute-or-fetch — the framework-native memoization
value = await cache().remember("reports:42", ttl=600, factory=build_report)
```

- `remember()` treats a cached `None` as a hit — negative lookups must
  not re-run the factory on every call.
- TTLs are validated loudly: a non-positive (or non-numeric) ttl raises
  a clear `ValueError` instead of misbehaving server-side.
- The default TTL is `CACHE_TTL` (3600s).

Tenant-aware apps wrap the store once — `CompanyCacheStore(store)` —
and every key becomes `company:{id}:…`; `flush()` is refused because a
company-scoped wrapper cannot know what else lives in the shared store.

### Locks

Every store hands out named, auto-expiring locks — one holder per name,
useful to keep concurrent jobs from duplicating work:

```python
async with cache().lock("rebuild:42", ttl=60):
    report = await build_report(42)  # one runner at a time, per cache driver
    await cache().put("reports:42", report, ttl=600)
```

- `ttl` is required: the lock frees itself if the holder dies, so a crash
  cannot deadlock the name forever.
- Release is owner-checked — a stale handle whose lock expired and was
  reclaimed cannot drop the new holder's lock; it is a no-op.
- `acquire()` is the non-blocking form (`True`/`False`); `block(timeout=...)`
  waits, raising `LockTimeout` when the timeout passes. The context manager
  waits indefinitely — pass a `timeout` to `block()` when you need a bound.
- Locks survive `flush()` (that forgets cached keys, not coordination
  state), and work identically on `memory`, `redis`, and `database`.
- The `locks:` key segment is reserved — lock state lives under it, and
  on `redis` a value there survives `flush()` by design — so never store
  a value under a key starting with `locks:`.
- On the `database` driver a lock name plus the configured cache prefix
  must fit 255 characters — keep lock names short (memory and redis
  accept longer names; short names behave identically everywhere).

Cache tags are not supported on purpose: memory tags are per-process and
lie under multi-worker serve. Namespace your keys instead
(`"user:{id}:settings"`) — invalidation behaves the same on every driver.

## Scheduling

Long-running work belongs in the queue, not the request: the kernel
drains the memory driver at graceful shutdown, and Redis-backed SAQ
workers consume dispatches independently of web processes. Keep handlers
idempotent — dispatches are at-least-once under SAQ.

## The SAQ dashboard

SAQ ships a web UI — live queue depth, job inspection, retry/abort — and
Fastplace mounts it behind authentication. It is **off by default**;
turn it on in `.env`:

```env
QUEUE_DASHBOARD_ENABLED=true
QUEUE_DASHBOARD_PATH=/queue-dashboard   # any mount path you like
QUEUE_DASHBOARD_ABILITY=                # empty = any logged-in user
```

The mount exists only under `QUEUE_DRIVER=saq` (a memory-driver process
has no SAQ queue to show — `queue:health` warns about the combination
instead of the boot crashing). Every request through the mount — the
pages and SAQ's own retry/abort POSTs — passes one guard that mirrors
the `auth` middleware contract:

- anonymous browsers are redirected to `/login` with the intended URL
  parked, so login resumes on the dashboard;
- programmatic callers (the bridge header, JSON `Accept`) get the 401/403
  JSON envelope, never an HTML page;
- `QUEUE_DASHBOARD_ABILITY`, when set, is checked through the gate on
  every request. An ability nobody defined surfaces its
  `ConfigurationError` on the first request — a typo can never silently
  open the dashboard.

Scope it to operators with a gate ability:

```python
# app/authz/gates.py (or wherever your gates load from)
@gate.define("view-queue-dashboard")
async def view_queue_dashboard(user):
    return getattr(user, "is_ops", False)
```

…then set `QUEUE_DASHBOARD_ABILITY=view-queue-dashboard`.

The dashboard is read-mostly by design. Failed jobs live in the
persisted failed-job store, surfaced and managed through the CLI —
`queue:failed` lists, `queue:retry <id>` requeues, `queue:forget <id>`
drops one record, `queue:flush` clears the ledger, and
`queue:prune-failed` drops stale entries. That surface round-trips the
same store the dashboard reads from.
