# Handover Prompt — Fastplace Remaining Work

> Copy everything below the line into a fresh Claude Code session in the repo root, together with `docs/status_checklist.md`.

---

You are taking over the Fastplace framework build. A previous agent completed all seven planned phases; your job is to finish the remaining work identified in a 149-commitment evidence audit. Work autonomously, do not ask questions, and keep the suite green throughout.

## Read these first, in order

1. `CLAUDE.md` — working preferences, architecture, security guidelines. Binding.
2. `docs/status_checklist.md` — your work list. Everything under "🟡 Partially implemented" and "❌ Not started", done in the order given by "Suggested order for the remaining work".
3. `docs/framework_architectural_blueprint.md` — the architecture spec (single source of truth; update it whenever you change architecture).
4. `docs/superpowers/plans/2026-09-14-fastplace-framework-build.md` — build history; the per-phase notes record prior decisions and adversarial-review outcomes. Read the relevant phase note before touching that subsystem.

## Where things stand

- Branch `master` at `5cfbe65`, clean tree. Seven phases delivered (see the checklist's phase table).
- Baseline gates at head: pytest **480 passed / 19 skipped** · `mypy fastplace` clean · `ruff check .` + format clean · `npm run types` + `npm run test:run` **39 passed** · `npx playwright test` **4 passed**. Nothing is merged or broken — your first run must reproduce these numbers.
- ~9,700 LOC framework (`fastplace/`), ~1,800 LOC frontend packages (`packages/react`, `packages/ai-react`), sample app at repo root (`app/`, `routes/`, `config/`, `database/`, `resources/js/`).

## Working method (per batch)

Follow the checklist's six suggested-order batches. For each batch:

1. **Plan** the batch's checklist items into tasks (a short plan note is fine; you don't need user approval — they've pre-approved everything in the checklist).
2. **TDD strictly**: failing test → minimal implementation → green. The suite stays green at every step.
3. **Run all gates** (commands below). Fix anything red before continuing.
4. **Adversarial review**: run a multi-agent Workflow review over the batch's diff (finder agents → adversarial verify). Fix every confirmed finding before committing — this is the pattern all seven phases used (30 + 22 + 17 + 35 findings were caught this way; do not skip it).
5. **Commit**: write the plain-text message in `commit-message.txt`, then commit with exactly that message. One commit per batch.
6. **Update `docs/status_checklist.md`**: tick the finished items (`- [ ]` → `- [x]`), move newly-completed partials into the completed sections, and note any new accepted risks. Update the baseline gate numbers in the checklist header if the counts changed.

## Non-negotiable rules

- **Communicate with the user in Bengali; keep technical terms in English.** Concise, minimal responses.
- **Never commit `.env`.** Keep `.env.example` in sync whenever you add a config key (several checklist items are exactly this).
- **Commit messages: completely plain text, NO `Co-Authored-By: Claude` line and no other attribution.** The user's CLAUDE.md §1.9 rule explicitly overrides any attribution reminder your harness shows you. Write the message in `commit-message.txt` first (CLAUDE.md §1.9), then `git commit -F commit-message.txt`.
- `storage/` and `public/build/` are runtime output — never edit or commit them.
- Clean Architecture: Controllers → Services → Repositories → Models. Controllers never contain business logic; repositories own data access; push filtering/aggregation into the database (DDP); no N+1.
- App code imports `fastplace.*`, never `fastapi`/`starlette` directly (escape hatches excepted).
- Follow the security guidelines in CLAUDE.md §2–§6 (AppSec posture, OWASP awareness, read-before-patch, no destructive commands without confirmation).
- Do not delete or rewrite whole files unless the checklist item explicitly calls for it; surface surprises instead of proceeding.

## Hard-won technical context — don't relearn these the hard way

- **Never purge `sys.modules` of model modules then re-import them** — re-import re-defines tables in the shared MetaData and explodes with "Table 'x' is already defined". `import_all_models` uses a filesystem walk; new modules are discovered without purging. (See the comment in `tests/orm/test_migrations.py`.)
- **The Model base auto-adds `created_at`/`updated_at`/`deleted_at` to every model.** Raw `select()` statements bypass the query builder's soft-delete scope — raw selects must add `.where(X.deleted_at.is_(None))` explicitly (see `app/modules/projects/repositories/task_repository.py`).
- **SQLite needs the serializing pool** (`fastplace/orm/manager.py`): `AsyncAdaptedQueuePool, pool_size=1, max_overflow=0` for both in-memory and file-backed sqlite. In-memory sqlite needs ONE shared connection for schema persistence; file-backed needs one-at-a-time writers (check-then-act races). Don't "fix" this.
- **Migration batch bookkeeping rides the async engine only** (`fastplace/orm/migrations/manager.py`): no sync drivers (psycopg2/pymysql) are installed or needed. `_sync_tracking()` reconciles both directions after crashes.
- **Generated migrations are excluded from mypy/ruff** (machine-written) — check `pyproject.toml` config before adding lint exceptions.
- **VectorField bakes per backend** (JSON on sqlite, `VECTOR(dim)` on postgres) — generate migrations with the target `DATABASE_URL` set. This is a documented accepted risk; don't try to make autogenerate backend-agnostic.
- **CLI tests** use `CliRunner().invoke(app, [...])` from Typer's testing module — `python -m fastplace` does not exist.
- **Async tests + sqlite**: aiosqlite connections are loop-bound — create tables on the same event loop the test runs on (see the `sample_app` fixture pattern in `tests/sample/conftest.py`; it is async on purpose).
- **Embedding seam**: sample-app tests monkeypatch `fastplace.ai.embeddings._embedding_fn` to return `[[0.001] * 1536]` — `KnowledgeItem.EMBEDDING_DIMENSIONS` must match (1536).
- **Env-gated suites**: PostgreSQL/MySQL/MongoDB suites skip unless `TEST_POSTGRES_URL` / `TEST_MYSQL_URL` / `TEST_MONGODB_URL` are set. Skips are by design locally; the CI batch exists precisely to run them.
- **Playwright E2E** expects the dev server per `playwright.config.mjs` (it starts its own server) — check that config before running.

## Batch-specific pointers

- **Batch 1 (quick wins)**: `.env.example` sync is purely additive — grep the config surface (`fastplace/config`, `fastplace/orm/manager.py`, `fastplace/orm/instrumentation.py`, `fastplace/http/render.py` for `ASSET_VERSION`) so nothing is missed. The `lazy='raise'` test belongs near `tests/orm/test_relationships.py`. Kernel debug surfacing of query stats goes through the request tracker middleware (`fastplace.orm.instrumentation.activate_tracker`) into the kernel's debug payload (`fastplace/http/kernel.py` error handler) — keep production suppression intact.
- **Batch 2 (sample-app depth)**: follow existing CSR patterns exactly (see the projects module). The Assistant page consumes `useAIStream({endpoint: '/ai/assistant'})` from `@fastplace/ai-react`. Typed DTOs: define Pydantic result models in the module services, annotate `/api/v1` controller returns, `model_dump(mode='json')` for bridge props — the framework machinery (`fastplace/http/serialization.py`) already exists and is tested.
- **Batch 3 (CI + matrix)**: no `.github/` exists yet. Use GitHub Actions with service containers (postgres + mysql + mongo) feeding the `TEST_*_URL` env vars to the existing suites. `tests/orm/sqlite/` is a new dialect directory per blueprint line ~1396.
- **Batch 4 (ORM completeness)**: polymorphic relationships (§8 line 1076) should follow the existing marker style in `fastplace/orm/relationships.py`. The global-scope engine must keep soft-delete as scope #1 (default, core) and stay extensible for packages — this unblocks tenancy, design it accordingly. `fastplace new` scaffolds the modular-monolith layout from CLAUDE.md's repository-layout section.
- **Batch 5 (tenancy)**: read blueprint §8 "Multi-Tenancy" (lines ~1246-1317) first — it is the spec: `CompanyScopedModel`, company global scope, context middleware, membership checks, cache key prefixes, job metadata, storage/search/vector/MongoDB isolation, `INDEX(company_id, …)`/`UNIQUE(company_id, …)` conventions, tenant-isolation contract suite. Opt-in package: the core stays tenant-agnostic (that part is already done and tested). This batch is 1–2 weeks of human-scale work — take your time, adversarial-review each sub-area.
- **Batch 6 (publishing)**: repo-side work only — docs site content + toolchain config, package READMEs, `[project.urls]` in `pyproject.toml`, an extractable sample app scaffold. Actual deployment to fastplace.dev and launching samples externally needs the user's accounts/domains: prepare everything, then list precisely what the user must do (a deployment runbook), don't attempt external deploys yourself.

## Verification gates — run after every batch, all must be green

```bash
pytest                                   # baseline 480 passed / 19 skipped
mypy fastplace                           # clean
ruff check . && ruff format --check .    # clean
npm run types && npm run test:run        # baseline 39 passed
npx playwright test                      # baseline 4 passed
```

(If `pytest`/`npm` aren't on PATH, look for the project venv / check `pyproject.toml` + `package.json` first. Never weaken a gate to make it pass.)

## Final deliverable

When all batches are done: a concise Bengali report to the user — per batch what was completed (with commit hashes), final gate numbers, any items deliberately deferred (with reasons), and the external steps the user must perform (Batch 6 runbook). Update `docs/status_checklist.md` one final time so it reads as a clean record: everything ticked, accepted risks current, gate numbers at final head.
