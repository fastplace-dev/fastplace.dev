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
ExecStart=/srv/app/.venv/bin/fastplace serve --host 127.0.0.1 --port 9000
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

One residual risk to know about: gzip on responses that **reflect user
input alongside secrets** enables BREACH-style compression-oracle attacks.
The framework does not rate-limit or suppress reflected params because
honest detection is unreliable; the standard mitigations are yours to
apply where a page reflects request data (query strings, error messages)
and also renders sensitive values (CSRF tokens, session identifiers,
addresses) — avoid reflecting arbitrary user input on such pages, or
randomize any secret rendered next to it. Pages without reflected input
carry no such risk.

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

### Logging

The kernel boots the logging channels at startup — `storage/logs/` is
real with zero configuration. Every line carries correlation ids:
`request_id` (the `X-Request-ID` the app accepts or mints per request and
echoes on the response) and `job_id` (the queue job name, on both the
memory and saq drivers). Ship the file to your aggregator of choice and
trace one request across app, queue and error lines by its id. Unhandled
errors stay a generic 500 on the wire — the traceback lands here instead,
carrying the same `request_id`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `LOG_CHANNEL` | `single` | `single` (rotating file), `daily` (midnight-rotated file), `stderr`, `null`, or `stack` |
| `LOG_LEVEL` | `INFO` | Root and `fastplace` logger level (`DEBUG`…`CRITICAL`) |
| `LOG_STACK` | `single,daily,stderr` | Comma-separated channels written when `LOG_CHANNEL=stack` |
| `LOG_MAX_BYTES` | `10485760` | `single`: rotate the file at this size |
| `LOG_BACKUP_COUNT` | `5` | `single`: rotated files kept |
| `LOG_DAILY_DAYS` | `7` | `daily`: dated files kept |

`single` writes `storage/logs/fastplace.log` (10 MB × 5 backups); `daily`
writes `fastplace-YYYY-MM-DD.log` and prunes past `LOG_DAILY_DAYS` at
midnight. Note for `serve`'s multi-worker mode: each worker process rolls
its own rotation — size-based rotation is safe (workers rotate
independently and backups still cap total disk), but prefer the `daily`
channel across many workers so retention stays predictable.

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
