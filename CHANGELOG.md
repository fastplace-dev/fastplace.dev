# Changelog

All notable changes to the Fastplace framework are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.2] - 2026-10-06

### Added

- **fastplace-aibrain MCP server** (`pip install fastplace[mcp]`, `fastplace mcp start`):
  a Model Context Protocol stdio server that gives AI coding agents live
  context about a Fastplace project — application info (framework, Python,
  database, every installed package), read-only database schema and query
  tools, application/browser log readers, hosted docs search
  (search-docs, backed by the deployed fastplace.dev digest API), a
  durable project-rules recorder (`.fastplace/rules/`), and an opt-in
  isolated `tinker` REPL (`FASTPLACE_MCP_TINKER=1`). Ships with install
  machinery for 13 agent clients (`fastplace mcp install` — Claude Code,
  Cursor, Codex, Copilot, Zed, and more), an AGENTS.md guidelines writer,
  and a `fastplace-code-simplifier` prompt. Every tool is gated by
  `FASTPLACE_MCP_*` env switches; the server reports the framework
  version in `serverInfo`.
- The `all` extra now includes the `mcp` dependencies.

### Fixed

- Intermittent `migrate:check`/`make:migration` schema drift: tables whose
  model class was garbage-collected stayed on the ORM metadata and leaked
  into autogenerate. The boot sandbox now sweeps class-less tables on
  entry (and restores them after a non-persisting boot).
- `search-docs` defaults to the deployed `https://fastplace.dev` docs API —
  the previous default host had no DNS record.

## [0.4.0] - 2026-10-01

### Added

- No-JS form verb override: the bridge `Form` component injects a hidden
  `_method` field for put/patch/delete intents, and the kernel's new
  `MethodOverrideMiddleware` rewrites the routing verb after CSRF has
  validated the real POST — so browsers without JavaScript drive the full
  REST surface. Only form-encoded/multipart posts are honored (a JSON
  `_method` key stays payload data), and GET/HEAD can never be spoofed.
- SECURITY.md and CONTRIBUTING.md: supported-versions + private
  vulnerability-reporting policy, and the dev-setup / test-gate / PR
  workflow for contributors.
- CI hardening: a Python 3.13 leg joins 3.12 (the shipped classifier is
  now actually tested), pip-audit and `npm audit --audit-level=high`
  supply-chain gates, and a redis service container so the redis
  integration lane runs on every push instead of never.
- The `all` extra now includes `pwdlib[argon2]` — the one optional
  dependency an everything-installed environment expects.
- Static prerendering (SSG): `fastplace prerender` captures configured GET
  routes to static HTML under `public/build/prerender` (in-process ASGI
  capture with full lifecycle boot; `prerender-manifest.json` with sha256
  hashes and no timestamps, so output is reproducible). `PrerenderStaticFiles`
  serves those files ahead of the app for plain GET navigations — method GET,
  `Accept: text/html`, no query string — and falls through to the live app for
  everything else; assets and existing static mounts are untouched. Routes
  come from `--route` flags, the `PRERENDER_ROUTES` list on the asgi module,
  the `PRERENDER_ROUTES` env var, or `/` by default. `--timeout` (seconds,
  default 30) bounds each capture so a hanging page fails the run instead of
  stalling it. The writer refuses a non-empty `--out` directory that has no
  `prerender-manifest.json` from a previous run — `--force` overrides. A run
  where every route skipped (transient 500s, non-HTML) writes nothing, so the
  pages the previous run produced stay live. The `/build` static mount never
  serves the prerender tree — prerendered pages answer at their own URLs and
  `prerender-manifest.json` stays unpublished. A
  capture-time crash exits 1 with the cause in a one-line red message.
  `httpx` moves from dev dependency to core.

### Changed

- Default server port: `fastplace run dev` / `fastplace serve` now bind 9000
  instead of 8000 (`--port` / APP_PORT still pin any port). The dev default
  also auto-falls back to the next free port (9000, 9001, ...) when nothing
  pins one; explicitly chosen ports and every `serve` bind stay strict — a
  busy port is an error naming the nearest free port. Out-of-range or
  non-integer `--port` / APP_PORT values fail with a clean one-line error
  instead of a traceback (an explicit `--port` wins without APP_PORT ever
  being parsed, so the flag is the escape hatch for a broken `.env` value),
  error and notice lines escape Rich markup so hostile env values cannot
  crash the console, the fallback walk never crosses 65535, the port probe
  follows IPv6 host literals, a malformed APP_WORKERS gets the same clean
  error, and child exits propagate to the wrapper with signal deaths
  normalized to the shell's 128+N convention. Scaffolded `.env` / config
  defaults and the WebAuthn origin fallback follow the new port.
- `fastplace serve` protects the prerender tree: the frontend build's
  `emptyOutDir` used to wipe `public/build/prerender` wholesale. Serve now
  snapshots the manifest before `npm run build` and re-captures the tree
  in-process afterwards whenever the app ships prerendered (pre-build
  manifest) or configures routes explicitly — a recapture failure fails
  serve loudly instead of deploying a half-wiped tree. A never-prerendered
  app with no explicit routes is left untouched.
- Scaffold polish: `make:model` snake-cases the module name (`BlogPost`
  lands in `blog_post/`, not a mixed-case module), the scaffolded `.env`
  template carries the full commented key blocks (queue retry knobs,
  broadcasting, S3, mail, logging, prerender routes), the scaffold
  `.gitignore` excludes every `.env.*` variant while keeping
  `.env.example` trackable, and the framework `.env.example` documents
  APP_HOST and the QUEUE_TRIES / QUEUE_TIMEOUT / QUEUE_BACKOFF / QUEUE_TTL
  retry knobs.
- Migration tooling: the reflected ANN-index exclusion (hnsw/ivfflat) that
  the generated migration env applies is now one shared hook used by both
  the env template and `fastplace migrate:check`, so autogenerate parity
  cannot drift.

### Fixed

- Security — ORM: multi-hop eager loads (`with_("a.b")`) skipped global
  scopes on intermediate entities, leaking soft-deleted and cross-tenant
  rows. Loader criteria are now applied per visited entity on the dotted
  path. Covered by a new invariant suite walking every SQL-emitting Model
  and query path.
- Security — tenancy: `first_or_create` / `update_or_create` / `upsert`
  now stamp the company scope on every write path (including the
  IntegrityError-race retry), and tenant queue jobs carry the tenant id
  through `job()` deferral and `dispatch_many` batches — background work
  can no longer run unscoped.
- Security — starter app: the knowledge search API required no
  authentication and no rate limit (spending real embedding calls) and
  returned the raw embedding vector; routes now sit behind auth + a named
  throttle and the vector is stripped from responses.
- Security — HTTP: CSRF token comparison no longer raises a TypeError on
  non-ASCII tokens (it compares on encoded bytes), 500 responses carry the
  security headers and request id instead of bypassing them, and
  production refuses an APP_KEY shorter than 32 bytes (offline-brute-
  forceable across HS256 JWTs, signed URLs, and encrypted secrets).
- AI: structured streaming serializes with `model_dump(mode="json")` so
  datetime/UUID/Decimal/Enum fields stream instead of surfacing as a
  stream failure; `Literal`/enum tool parameters derive provider-accepted
  JSON schemas; the agent forwards its temperature to structured calls;
  and a docstring `Args:` line like "Note: …" no longer hijacks parameter
  parsing.
- Bridge: the clicked submit button's name/value rides the submission
  (matching native forms); a form with a file input submits native
  multipart instead of corrupting the File into `{}`; `usePresence` drops
  the old room's roster the moment the channel changes; and
  `document.title` resets on bridge navigation so a page without `<Head>`
  no longer shows the previous page's title.
- Events: a broadcast-leg failure after commit no longer rolls back the
  committed write — the leg is best-effort with the failure logged.
- Cache: the sliding-window rate limiter behaves the same across drivers
  (redis expiry is unconditional; the database store slides `expires_at`).
- ORM: `read_session()` caches its session factory per engine instead of
  building one on every read call.
- Prerender: query/fragment routes are rejected up front (the output could
  never be served), and a hung lifespan startup/shutdown surfaces as a
  named timeout error instead of a message-less failure.
- CLI: `migrate:rollback --steps 0` is rejected instead of silently
  reverting one revision; a malformed E2E_PORT fails with a clean error;
  `make:model` module names snake-case (see above).

### Removed

- The docs.yml GitHub Pages workflow: production docs (fastplace.dev) are
  served from the separate fastplace-docs application repository, and a
  stray docs push from this repo could clobber the live site.

## [0.3.1] - 2026-09-29

### Changed

- Project status: **Beta**. The `Development Status` trove classifier moves from
  _3 - Alpha_ to _4 - Beta_ — the first release carrying Beta metadata, matching
  the public beta.

### Fixed

- React bridge: a `FastplaceProvider` re-render after `router.reset()` no longer
  throws "no page initialized". A pending React update scheduled by an earlier
  `emit()` could flush after the store was emptied and re-render the provider
  against it; the provider now falls back to the page it booted with
  (`initialPage`). The failure was timing-dependent (local green, CI red) and
  surfaced in vitest as an unhandled exception with every test passing.

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
