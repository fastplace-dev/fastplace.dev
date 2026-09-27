# Deployment

Fastplace is a modular monolith: **one deployable unit**, one build step
for assets, one database, one process manager.

## Build & serve

```bash
npm run build               # vite build → public/build/
fastplace serve             # optimized multi-worker ASGI (Uvicorn multi-process)
```

`fastplace serve` hosts the static assets from `public/build/` and the
API/bridge routes under a single multi-worker process — no separate
static host required. `ASSET_VERSION` (env) fingerprints asset URLs so
cache invalidation is a config change, not a filename hunt.

Development is `fastplace run dev` (Uvicorn reload + Vite HMR); never use
it in production.

Both entrypoints read `.env` from the project root at startup — real
environment variables win over file values, which is how platforms that
inject env vars stay in control.

## Serve-time guards

`fastplace serve` refuses to boot configurations that will silently
misbehave, and says exactly what to change:

- **`--workers 2+` with memory sessions** — each worker holds its own
  session store, so saves land on one worker and reads hit another.
  Serve exits unless `SESSION_DRIVER` is `database`/`redis`, the deploy
  is single-worker, or `SESSION_ALLOW_MEMORY_MULTIWORKER=1` acknowledges
  the trade-off.
- **`APP_ENV=production` with `CACHE_DRIVER=memory`** — per-process
  rate-limit counters under-count under multiple workers. Set
  `CACHE_DRIVER=redis` (or `database`), or acknowledge a single-worker
  deploy with `CACHE_ALLOW_MEMORY_IN_PRODUCTION=1`.

## Process management (systemd)

`fastplace serve` runs in the foreground and supervises its own Uvicorn
worker processes — the unit only needs to keep the CLI alive and restart
it on failure:

```ini
# /etc/systemd/system/fastplace.service
[Unit]
Description=Fastplace app
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=fastplace
WorkingDirectory=/srv/app
ExecStart=/srv/app/.venv/bin/fastplace serve --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=2
# Hardening (adjust to your layout)
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/srv/app/storage

[Install]
WantedBy=multi-user.target
```

`SIGTERM` to the unit propagates to the whole worker tree: the CLI stops
workers gracefully, waits up to five seconds, then force-kills.

## Behind a reverse proxy

TLS should terminate at a proxy (nginx, Caddy, your platform's edge) in
front of `fastplace serve`. Uvicorn's proxy-header handling is on by
default and trusts `127.0.0.1`; when the proxy is not on localhost, tell
serve which IPs to trust so `X-Forwarded-*` become the request's real
scheme/IP/Host:

```bash
fastplace serve --forwarded-allow-ips "10.0.0.0/8"   # trust your proxy net
fastplace serve --no-proxy-headers                    # ignore X-Forwarded-* entirely
```

Untrusted `X-Forwarded-For` headers are otherwise attacker-controlled —
do not widen this to public ranges. Add HSTS at the proxy once TLS is
stable (`Strict-Transport-Security: max-age=31536000`), not in app code,
so plain-HTTP rollbacks stay possible.

## Response compression & asset caching

Serve compresses compressible responses over 1 KB with gzip for clients
that accept it (streaming/SSE and binary types are excluded — the stream
contract and the CPU budget both matter). There is nothing to configure.

Vite's content-hashed files under `/build/assets/` are served
`Cache-Control: public, max-age=31536000, immutable`; everything else
under `public/` revalidates after 5 minutes. Because filenames change on
every build, never cache-bust manually.

## Custom error pages

Browser navigations get styled HTML error pages out of the box (API and
bridge clients keep getting JSON). To brand them, drop an override per
status at `public/errors/{status}.html` — e.g.
`public/errors/500.html` or `public/errors/403.html` — and serve picks
it up on the next request; no code changes needed.

## Environment

Production checklist for `.env` (see `.env.example` — the canonical key
list):

| Key | Production value |
|---|---|
| `APP_ENV` | `production` |
| `APP_KEY` | a fresh generated secret (never commit it) |
| `DATABASE_URL` / `DATABASE_DRIVER` | PostgreSQL recommended (`fastplace db:configure postgresql` writes the pair) |
| `SESSION_DRIVER` | `database` or `redis` for multi-worker serve (memory is refused with `--workers 2+`; `SESSION_ALLOW_MEMORY_MULTIWORKER=1` acknowledges it) |
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
