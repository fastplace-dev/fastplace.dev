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
| `CACHE_DRIVER` / `QUEUE_DRIVER` | `redis` (production refuses `CACHE_DRIVER=memory` — per-process rate-limit counters — unless `CACHE_ALLOW_MEMORY_IN_PRODUCTION=1` acknowledges a single-worker deploy) |
| `REDIS_URL` | your Redis instance |
| `QUERY_SLOW_MS` / `QUERY_N1_THRESHOLD` | tune to your SLO |

Run migrations as part of the deploy:

```bash
fastplace migrate
```

## Workers

With `QUEUE_DRIVER=redis` (SAQ), run dedicated workers alongside web
processes; dispatches are at-least-once, so keep handlers idempotent. The
in-process memory driver drains at graceful shutdown and is intended for
dev and small deployments only.

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
