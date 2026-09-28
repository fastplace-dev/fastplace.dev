# Testing

Fastplace apps inherit a testing stack that mirrors the runtime: async
backend tests against the real ASGI app, component tests for the React
side, and Playwright E2E over the full stack.

**Writing tests for *your* app?** Start with
[Testing your app](/guides/app-testing) — the pytest plugin, HTTP
assertions, fakes, clock, and factories that ship with every Fastplace
project. This page covers how the framework's own suites are organized,
which is what you need when contributing to Fastplace itself.

## Backend — pytest

```bash
pytest                          # whole suite
pytest tests/http -k render     # one pattern
```

```python
async def test_projects_index(client):
    response = await client.get("/projects")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
```

Async tests run natively (no `asyncio.run` boilerplate). The fixtures
that matter:

- **`sample_app`** — the real app, migrated + seeded on the same event
  loop the test runs on (aiosqlite connections are loop-bound — create
  tables where you run them).
- **`backend`** — the portable matrix: SQLite always, PostgreSQL / MySQL
  when `TEST_POSTGRES_URL` / `TEST_MYSQL_URL` are set. Write the test
  once; CI runs it everywhere the framework supports.

```python
async def test_scopes_survive_postgres_too(
    backend,
): ...  # parametrized across every configured backend
```

Feature knobs worth knowing: monkeypatch `request.set_user(...)` instead
of building auth fixtures; `.env` is never read by the suite (config
comes from explicit test config).

## Running the test matrix

```bash
python -m pytest -q
```

A plain run is SQLite-only (about 2,600 tests and growing), plus a
handful of env-gated tests that skip with self-explanatory reasons. The remaining backends are opt-in through
three environment variables:

- **`TEST_POSTGRES_URL`** — the PostgreSQL dialect suite plus the tenancy
  RLS suite.
- **`TEST_MYSQL_URL`** — the MySQL dialect suite.
- **`TEST_MONGODB_URL`** — the MongoDB contract suite (9 tests).

```bash
TEST_POSTGRES_URL="postgresql://user@localhost/fastplace_test" \
TEST_MYSQL_URL="mysql://root@localhost:3306/fastplace_test" \
TEST_MONGODB_URL="mongodb://localhost:27017" python -m pytest tests/orm/ tests/tenancy/ -q
```

The parametrized `backend` fixture also skips pairings that do not apply
to the backend at hand — native-RLS tests skip on non-PostgreSQL backends —
so a fully green matrix run can still print skips. `pytest -rs` lists
every skip reason.

## Frontend — vitest + testing-library

```bash
npm run test:run
```

Tests are colocated in `__tests__/` directories (see `packages/react` and
`packages/ai-react` for framework-package examples — 48 tests over the
bridge router, page resolver, and stream hooks).

```tsx
import { render, screen } from "@testing-library/react";
import { usePage } from "@fastplace/react";

it("renders server props", () => {
    // the harness seeds the page payload; assert on rendered output
    render(<ProjectRow title="first" />);
    expect(screen.getByText("first")).toBeInTheDocument();
});
```

## End-to-end — Playwright

```bash
npx playwright test
```

The config starts its own dev server (scratch database, hermetic
teardown) and runs the smoke path: boots the app, checks the bridge
round trip and the unified API health endpoint.

## Static analysis

```bash
mypy fastplace        # types are part of the contract
ruff check . && ruff format --check .
npm run types         # tsc --noEmit
npm run lint:check    # eslint
npm run format:check  # prettier
```

All five run in CI on every push, alongside the backend matrix
(PostgreSQL with pgvector, MySQL, MongoDB service containers).

## Boundary gate

`lint:modules` (a CI job, also runnable locally) enforces the
architecture: controllers may not import models, repositories may not
import services, `app/` code may not reach into `fastplace/` internals.
A violated dependency is a red test, not a code-review argument.
