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

One protocol, several stores (`CACHE_DRIVER`: `memory`, `redis`) —
`config/cache.py` and `.env.example` are the canonical key list:

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
    report = await build_report(42)   # one runner at a time, per cache driver
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
