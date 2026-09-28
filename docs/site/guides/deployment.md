# Deployment

Fastplace is a modular monolith: **one deployable unit**, one build step
for assets, one database, one process manager.

## Build & serve

```bash
npm run build               # vite build → public/build/
fastplace serve             # optimized multi-worker ASGI (Gunicorn/Uvicorn)
```

`fastplace serve` hosts the static assets from `public/build/` and the
API/bridge routes under a single multi-worker process — no separate
static host required. `ASSET_VERSION` (env) fingerprints asset URLs so
cache invalidation is a config change, not a filename hunt.

Development is `fastplace run dev` (Uvicorn reload + Vite HMR); never use
it in production.

## Environment

Production checklist for `.env` (see `.env.example` — the canonical key
list):

| Key | Production value |
|---|---|
| `APP_ENV` | `production` |
| `APP_KEY` | a fresh generated secret (never commit it) |
| `DATABASE_URL` / `DATABASE_DRIVER` | PostgreSQL recommended (`fastplace db:configure postgresql` writes the pair) |
| `CACHE_DRIVER` | `redis` (production refuses `memory` — per-process rate-limit counters — unless `CACHE_ALLOW_MEMORY_IN_PRODUCTION=1` acknowledges a single-worker deploy) |
| `QUEUE_DRIVER` | `saq` for real workloads — the in-process `memory` driver is dev-only |
| `REDIS_URL` | your Redis instance |
| `QUERY_SLOW_MS` / `QUERY_N1_THRESHOLD` | tune to your SLO |

Run migrations as part of the deploy:

```bash
fastplace migrate
```

## Queue workers

With `QUEUE_DRIVER=saq`, run dedicated workers alongside the web processes;
dispatches are at-least-once, so keep handlers idempotent. The in-process
memory driver drains at graceful shutdown and is intended for dev only —
`fastplace queue:dispatch` refuses it outright, because nothing would ever
come back to run the job.

Supervise the worker so a crash or deploy restarts it — systemd:

```ini
# /etc/systemd/system/fastplace-worker.service
[Service]
WorkingDirectory=/srv/app
ExecStart=/srv/app/.venv/bin/fastplace queue:work
Restart=always
```

…or supervisor:

```ini
[program:fastplace-worker]
directory=/srv/app
command=/srv/app/.venv/bin/fastplace queue:work
autorestart=true
```

Deploys publish `fastplace queue:restart`: each worker finishes its current
job, exits 0, and the supervisor brings the replacement up on the new code —
no dropped jobs, no manual kills.

### Redis is the contract

The queue is only as durable as the Redis behind it. Dispatch is
synchronous with the caller: if Redis is unreachable, `dispatch()` raises
into the code that called it — there is no local outbox that silently
buffers and forwards. Wrap dispatches that must not die with the request in
`try/except` (or dispatch them from a scheduled task), and enable Redis
persistence (`appendonly yes`) so a broker restart does not erase queued
jobs.

### Dashboard

SAQ ships a web dashboard (`saq.web`); mount it behind an auth-protected
route if you want live queue inspection — never expose it publicly.

## Scheduler

The schedule runs in exactly one place per deployment: either a single
supervised `fastplace schedule:work` (foreground minute-tick worker)…

```ini
[Service]
WorkingDirectory=/srv/app
ExecStart=/srv/app/.venv/bin/fastplace schedule:work
Restart=always
```

…or one system-cron entry that fires every minute:

```cron
* * * * * cd /srv/app && .venv/bin/fastplace schedule:run
```

Pick one — two tickers double-fire every task. The queue worker does **not**
run the schedule. Tasks that must never overlap (a slow cleanup still
running when the next minute lands) carry `.without_overlapping()`: a mutex
on the cache store skips the second firing, so `CACHE_DRIVER=redis` makes
the guard effective across processes. While `fastplace down` is active every
task skips, except those marked `.even_in_maintenance()`.

## Observability

- DEBUG logging emits SQL with timing; slow queries WARN at
  `QUERY_SLOW_MS`; N+1 duplicate patterns are counted per request
  (`QUERY_N1_THRESHOLD`).
- Query stats ride the request tracker and surface in the kernel debug
  payload (debug env only — production 500s stay `{"message": "Server
  error."}`, exactly).
- Logs land under `storage/logs/` — ship them to your aggregator of
  choice.

## Security posture at serve time

- TLS terminates wherever your platform provides it; the ASGI app itself
  is protocol-agnostic behind the server.
- `APP_ENV=production` disables the debug overlay and stack traces —
  verify with a deliberate 500 before launch.
- CSRF and session cookies are signed with `APP_KEY`; rotating it logs
  everyone out (that is the desired breach response).
- Multi-tenant deployments: see the [tenancy guide](/guides/tenancy) —
  PostgreSQL RLS wants non-owner roles, and `SET LOCAL` bindings die with
  each transaction.

## Publishing this framework itself

The framework's own publishing steps (docs site, PyPI, npm) need accounts
and domains, so they live in the maintainers' internal runbook rather than
in this guide.
