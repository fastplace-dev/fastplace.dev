# Changelog

All notable changes to the Fastplace framework are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-09-29

### Added

- Storage: `S3Disk` driver (`fastplace.storage_s3`) over the same `Disk` ABC —
  primitives, presigned `temporary_url`, public URL policy (`public_base` /
  vhost / refuse-with-hint), and the same containment gate as local paths
  (refusals + stack-based dot-dot normalization bounded by the configured key
  prefix). aioboto3 ships as the optional `fastplace[s3]` extra (also part of
  `all`); constructing the driver without it fails loud with the install hint.
  `config/storage.py` carries the commented disk entry; `.env.example` the S3
  variable block.
- Mail: rendered mailables. `Mailable` (fastplace.mail.mailable) renders
  str.format placeholders into a MailMessage — values html-escaped (user
  data can never inject markup), subject raw, unknown names failing loud,
  optional `Layout` with a required {body} slot, best-effort tag-stripped
  text fallback. `Mail.send` and the notification mail channel accept a
  Mailable; rendering happens before the queue decision so payloads carry
  final html/text only.
- Queue: the SAQ web dashboard, mounted behind one fail-closed auth guard
  (`QUEUE_DASHBOARD_ENABLED`, off by default; mounts only under
  QUEUE_DRIVER=saq). Anonymous browsers redirect to /login with the
  intended URL parked, programmatic callers get the 401/403 JSON envelope,
  and QUEUE_DASHBOARD_ABILITY gate-checks every request including SAQ's
  retry/abort POSTs — an undefined ability fails loud, never silently
  allows. `queue:health` warns when the flag is on but the driver is not
  saq; failed jobs stay on the CLI surface (queue:failed / retry / forget
  / flush / prune-failed).
- Broadcasting: `broadcast(channel, payload)` fans JSON frames to
  WebSocket subscribers over a pluggable bus (`BROADCAST_DRIVER` = memory
  for single-process dev/tests, redis pub/sub for multi-worker). Channels
  follow one grammar — public `orders.42`, gated `private.*` /
  `presence.*` (subscribe-time gate check via BROADCAST_PRIVATE_ABILITY,
  fail-closed when unset), and tenancy-scoped `company.{id}.*` checked
  against the requesting user's membership. The `/ws/broadcast` endpoint
  resolves the session cookie at the handshake (close codes 4400/4401/
  4403), enforces payload/subscription/queue bounds, and derives
  presence rosters from join/leave bus events plus local connection
  tracking — no separate roster store; a heartbeat bounds ghosts from
  crashed processes. Domain events broadcast only through an explicit
  opt-in `broadcast_to` mapping. Frontend: `useBroadcast` /
  `usePresence` hooks in `@fastplace/react` (exponential-backoff
  reconnect, resubscribe on reconnect, SSR-dormant). Sample app gains a
  `/broadcast` demo page; guide at docs/site/guides/broadcasting.md.

### Fixed

- Broadcasting: presence control channel names (`__presence::…`, any leading
  `_` or `::`) are refused by the channel grammar — previously they parsed
  as public channels, letting a client subscribe to roster plumbing (join/
  leave/snapshot frames carrying user ids).
- Broadcasting: tenancy-scoped `company.<id>.*` channels are denied when no
  channel authorizer weighs in (fastplace-tenancy absent or not installed) —
  previously they degraded to the public rule.
- Broadcasting: the redis driver's crashed-listener restart path re-queues
  broker-side subscriptions and releases the dead connection pair —
  previously the rebuilt listener never re-subscribed, so the process
  silently stopped receiving; a publish racing `close()` can no longer
  resurrect a listener close() would never tear down.
- Broadcasting: the WebSocket layer owns a process-wide presence heartbeat
  task (every third of the 45s ghost window) — previously nothing
  heartbeated outside tests, so every remote roster ghost-aged out after
  the last control message while sockets stayed open. Join/leave control
  events now carry honest per-tab connection counts and refresh the
  origin's liveness stamp, and a presence channel's control plumbing is
  released when its last local member leaves (bounded memory). Channel
  names are capped at 200 characters.
- Storage: the S3 disk's `public` flag is parsed, not cast — an env
  `S3_PUBLIC=false` arrived as the string `'false'` and `bool('false')` is
  True, so disabling public URLs silently enabled them.
- Gates: the ruff/mypy `storage` exclusion is anchored to the repo root —
  the unanchored pattern also excluded `tests/storage/` from every gate;
  that directory is lint-clean now.

## [0.2.1] - 2026-09-28

The SQLAlchemy 2.1 compatibility release. `morph_to_many` eager loads are
pinned back to the join-based selectin path, and the dependency floor is
open above 2.0.36 again — fresh installs may resolve SQLAlchemy 2.1.x.
Python and npm packages stay in lockstep (`fastplace`,
`@fastplace/react`, `@fastplace/ai-react`), and `fastplace-tenancy` joins
the release train at the same version.

### Fixed

- SQLAlchemy 2.1 compatibility for `morph_to_many`: 2.1 auto-enables the
  `omit_join` selectin optimization for many-to-many shapes whose
  secondary FKs cover the parent pk, and that path never applies the
  custom primaryjoin — the owner-type discriminator was silently dropped
  and members owned by other types double-loaded. `morph_to_many` now
  pins `omit_join=False`, keeping the join-based loader (full
  primaryjoin) on every 2.x; behavior on 2.0 is unchanged.
- The `<2.1` SQLAlchemy freeze bound is lifted; the dependency is open
  above the tested 2.0.36 floor.
- `fastplace.__version__` no longer lags the release — it was stuck at
  0.1.0 through the 0.2.0 release, understating `fastplace --version`
  and doctor output. A contract test now pins it to pyproject.

### Added

- Packaging contract tests: `fastplace.__version__` must match
  pyproject, and `fastplace-tenancy` must ride the framework release
  train (same version, floor on the matching `fastplace`).

## [0.2.0] - 2026-09-28

The post-audit hardening release: every fix wave from the full-framework
parity audit is merged, and the release pipeline now gates publishes on a
boot-smoke of the built artifact. Python and npm packages move in lockstep —
`fastplace`, `@fastplace/react`, and `@fastplace/ai-react` all ship `0.2.0`
and are only supported as a matched set.

### Upgrade notes

Apps coming from 0.1.x have a verified three-step path (add
`email-validator`, `fastplace make:auth`, paste the `ROUTE_MIDDLEWARE`
registry into `config/app.py`) — see the Upgrading guide on the docs site.

### Added

- `fastplace new <name> --auth` starter kit with registration, email
  verification, login, password reset, two-factor TOTP with recovery codes,
  and a settings surface — the first registered account becomes the admin.
- Release-smoke CI job: builds the wheel, installs it into a clean venv, and
  scaffolds, boots, and tests an app from the built artifact before it can
  reach PyPI.
- Scaffolded apps derive their `@fastplace/react` / `@fastplace/ai-react`
  pins from the installed `fastplace` version, keeping the bridge matched
  without manual bookkeeping; a lockstep test pins all three manifests to
  one version.
- Upgrading guide (docs site) with the scaffold-ownership statement, the
  user-code drift procedure, and the python/npm compatibility matrix.

### Fixed

- Public scaffold APIs replace framework internals previously copied into
  generated projects; scaffold output runs against the public surface only.
- `npm publish` of the frontend packages rebuilds from source
  (`prepublishOnly`), so a stale `dist/` can no longer ship the previous
  release's bundle under a new version number.

## [0.1.0] - 2026-09-26

Initial public release of Fastplace — an opinionated, full-stack, AI-native web framework built on Python and React, shipped as a single-deployable modular monolith with strictly bounded internal modules. This release comprises the complete HTTP runtime, the Fastplace ORM with four database backends, the server-driven SPA bridge and Unified API, six phases of authentication and authorization, the SAQ + Redis task queue with scheduling, the native AI engine, the full `fastplace` CLI, and the first-party frontend and tenancy packages. The suite stands at 2141 tests, and the ORM compatibility matrix is verified live on SQLite, PostgreSQL + pgvector, MySQL/MariaDB, and MongoDB.

### Added

#### HTTP Runtime, Queue & Scheduling

- HTTP kernel, router, and request/response abstractions owned by the Fastplace layer, over FastAPI (application core) on the Starlette ASGI foundation, executed by Uvicorn in development and production.
- Route surfaces in `routes/web.py`, `routes/api.py`, `routes/ai.py`, and auto-discovered `routes/auth.py`; module-local route tables (`app/modules/<name>/routes.py`) merge into the central routers at boot.
- Global middleware stack plus per-route middleware aliases with groups and ordered chain execution — shipped aliases `auth`, `guest`, `throttle:N,D`, `verified`, `password.confirm`, and `can:ability`; an unregistered alias fails app assembly.
- WebSocket routes on the same routing table, surfaced by `fastplace route:list`.
- Server-side session stores — memory, database (portable upsert, lazy expiry), and Redis (namespaced keys, native TTL) — with session-id regeneration and sweeping garbage collection.
- Cache layer with memory, Redis, and database drivers behind `CACHE_DRIVER`, plus increment/TTL counter primitives; production refuses the memory driver so rate limits stay shared.
- Rate limiting through `ThrottleMiddleware` (429 + `Retry-After` per client per window) and the cache-backed `RateLimiter` sliding-decay attempt counter.
- Maintenance mode (`fastplace down` / `fastplace up`, every request a 503), baseline security headers applied by default, and production suppression of interactive API docs and exception details.
- Task queue on SAQ + Redis: job handlers in `app/jobs/`, delayed and scheduled dispatch, worker runtime options, a restart sentinel, queue depth monitoring, and a persisted failed-job store.
- Task scheduling with a cron-style task registry — `schedule:list`, `schedule:run`, foreground `schedule:work`, and single-task `schedule:test`.

#### Fastplace ORM & Database Layer

- Fastplace ORM public API — an await-only fluent model surface (`find`, `first`, `get`, `create`, `save`, `delete`, `query().where().order_by().paginate()`) over SQLAlchemy 2.x async engines.
- Relationships — `has_one`, `has_many`, `belongs_to`, `many_to_many`, self-referential, and polymorphic — eager-loaded with `.with_()` or the explicit `relation()` accessor; implicit lazy loads raise instead of emitting hidden N+1 queries.
- Soft deletes (`deleted_at` with `with_deleted()` / `only_deleted()` accessors and a `restored` event) alongside reusable and global query scopes.
- Model lifecycle events (`creating` through `restored`) feeding domain-event dispatch to jobs, websockets, and notifications.
- First-class transaction scopes (`async with db.transaction()`), named connections, and per-connection pooling through the Database Manager.
- Database capability registry (`supports_vector`, `supports_json`, `supports_full_text`, `supports_rls`, ...) with a portable-first policy and explicit escape hatches (`sa_model`, `sa_query()`, `db.raw()`).
- Four database backends: SQLite (zero-config default), PostgreSQL with pgvector (recommended production and AI database), MySQL/MariaDB (`asyncmy`), and MongoDB behind a separate async document adapter.
- Vector search via the `VectorField` column type and `Model.vector_search()`; full-text search via `Model.full_text_search()` behind the capability gate.
- Migrations and seeders with Alembic surfaced only through the CLI, batch bookkeeping for exact `migrate:rollback` semantics, and framework-owned tables (sessions, remember tokens, cache) kept outside Alembic.
- ORM compatibility matrix running every portable public-API behavior against each supported backend — verified live on SQLite, PostgreSQL + pgvector, MySQL/MariaDB, and MongoDB.
- First-party `fastplace-tenancy` package (opt-in): `CompanyScopedModel` automatic company scoping, company-context middleware, prefixed cache keys, job metadata context, and PostgreSQL Row-Level Security integration.

#### Server-Driven SPA Bridge & Unified API

- Server-driven SPA bridge (Inertia pattern): first load returns HTML with an embedded page payload; subsequent navigation sends `X-Fastplace-Request: true` and receives component + props JSON — no REST or GraphQL wiring for page state.
- `render(request, component, props)` from `fastplace.http`; one Controller-Service-Repository backend serves both the bridge and the Unified API, so business rules exist exactly once.
- Unified API of versioned JSON endpoints (`/api/v1/...`) validated by Pydantic v2 response schemas, with OpenAPI docs at `/api/docs`.
- `@fastplace/react` package: `usePage`, `Link`, `Form`, `useForm`, `Head`, `applyLayouts`, and `useCan`.
- Bridge forms serialize to JSON with the CSRF token, map 422 payloads onto per-field errors, honor `resetOnSuccess`, and still post without JavaScript; CSRF tokens rotate across auth boundary changes.
- Shared page props — `auth: {user, can}` precomputed by `SharedAbilitiesMiddleware`, plus the one-shot `status` flash prop.
- Appearance runtime: theme (light/dark/system), accent, and radius persisted per viewer and applied pre-paint; components style through semantic design tokens shared by both palettes.
- Frontend toolchain: Vite HMR beside Uvicorn live-reload under `fastplace run dev`, production asset builds served by the ASGI process under `fastplace serve`.
- Frontend testing: vitest + Testing Library unit suites and Playwright end-to-end journeys (login, registration, password reset, verification).

#### Authentication, Authorization & Mail

- Server-side session guard (`SessionGuard`) with the attempt family — `attempt`, `attempt_when`, `once`, `login_using_id`, `logout_other_devices` — and session-id regeneration on every login.
- Login throttling keyed per email and IP (429 + `Retry-After` past `AUTH_LOGIN_MAX_ATTEMPTS`) and remember-me through rotating selector|validator tokens with only the SHA-256 hash stored.
- Password reset: PHC-hashed single-use tokens with one live token per email, enumeration-safe responses, peek-then-consume redemption, and revocation of every session and token on success.
- Email verification over `APP_KEY`-signed HMAC links with idempotent redemption and the `verified` route middleware gate.
- Password confirmation windows (`PASSWORD_TIMEOUT`) enforced by the `password.confirm` middleware alias.
- Two-factor authentication: TOTP secret and recovery codes encrypted at rest, single-use codes with a timestep high-water mark, inline QR SVG, and a login challenge interrupt.
- Personal access tokens with abilities: token guard with `token_can`, session-only issue/revoke endpoints, and prune/list tooling.
- Authorization gate: `gate.define` abilities, policy classes with `before()` hooks and module auto-discovery, verdict objects including `deny_as_not_found`, the `authorize()` controller helper, and the `can:` route middleware.
- `fastplace make:auth` scaffolding the full auth surface — accounts module, auth controllers and requests, `routes/auth.py`, a user seeder, and starter gates in `app/auth/gates.py`.
- Mail subsystem (`fastplace.mail`): log, memory, and SMTP transports behind `Mail.to(...).send`, queued delivery under smtp+saq, and `MAIL_FROM_NAME` sender identity.

#### AI Engine

- `@Tool` decorator (`fastplace.ai.Tool`) deriving JSON schemas for LLM function calling from Python type hints and docstrings.
- `Agent` engine with provider-agnostic model routing through LiteLLM (OpenAI, Anthropic) and structured outputs validated through Instructor and Pydantic.
- SSE streaming: `agent.stream_response(...)` returns SSE stream responses from `routes/ai.py`, with a public `stream_events` iterator over the frames.
- Vector stores registered in `app/ai/vectors/`, with capability-aware search that never hard-codes a vector engine.
- Project discovery helpers `import_tools()` and `import_agents()` over `app/ai/tools/` and `app/ai/agents/`.
- `@fastplace/ai-react` package with `useAIStream` and `useAgent` hooks consuming the streamed `/ai/*` endpoints.

#### CLI & Documentation

- `fastplace` CLI (Typer + Rich) with more than 100 commands across server runtimes, scaffolding, inspection, database, queue, scheduling, auth, mail, AI, and operations; `fastplace list` prints the grouped inventory.
- Runtimes: `fastplace run dev` (Uvicorn live-reload + Vite HMR), `fastplace serve` (frontend build + multi-worker production server), and `fastplace shell` (interactive shell with models loaded).
- Generators: `make:module` scaffolds a self-contained vertical slice (`--api`, `--resource`, `--web`, `--bare`, `-m` flags compose), alongside `make:model`, `make:controller`, `make:service`, `make:repository`, `make:agent`, `make:page`, `make:command`, `make:auth`, `make:vector-store`, and stubs for jobs, requests, middleware, policies, seeders, scopes, and support classes.
- Database operations: `migrate` (with `--pretend`), `migrate:rollback`, `migrate:status`, `migrate:reset`, `db:seed`, `db:reset`, `db:wipe`, `db:show` / `db:table`, `db:cli`, `db:configure`, `db:health`, `db:query`, `db:export`, `db:truncate`, and `db:documents`.
- Queue, cache, session, key, and env operations: `queue:work` / `queue:failed` / `queue:retry` / `queue:monitor` / `queue:restart`, `cache:*`, `session:gc`, `key:generate` / `key:rotate` / `key:verify`, `env:encrypt` / `env:decrypt`, `env:lint`, and `env:set`.
- Provisioning and operations: `token:create` / `token:revoke` / `token:list`, `user:create`, `mail:test` / `mail:outbox` / `mail:preview` / `mail:resend` / `mail:clear`, `auth:sessions`, `auth:2fa-disable`, `auth:reset-link`, `gate:check`, and `throttle:status`.
- Doctor and test tooling: `doctor`, `http:doctor`, `db:doctor`, `queue:health`, `auth:doctor`, `mail:doctor`, `ai:doctor`, `test:doctor`, plus `test:db`, `test:matrix`, `test:coverage`, `test:watch`, and `test:e2e`.
- Module-boundary lint (`fastplace lint:modules`, a hard gate also enforced in CI, with `lint:watch` re-checking on save).
- VitePress documentation site under `docs/site/` (home, getting started, nine guides, and an API overview) with a GitHub Pages deploy workflow.
