# Getting started

Fastplace is a Python 3.12+ backend (Starlette/FastAPI, Pydantic v2,
SQLAlchemy 2.x + Alembic) paired with a React 18+ / Tailwind / Vite
frontend through a server-driven SPA bridge. This page takes you from
nothing to a running full-stack app.

## Requirements

- Python **3.12+**
- Node.js **18+** (for the frontend toolchain)
- Nothing else — SQLite is the default database, zero-config

## Scaffold a new app

```bash
pip install fastplace
fastplace new my-app
cd my-app
```

`fastplace new` writes a ~38-file modular monolith: routes, controllers,
an example module, migrations pre-configured, a generated `APP_KEY`, and
frontend wiring that resolves `@fastplace/react` from your checkout.

Two decisions are asked at creation and both can be pre-answered:
`--auth/--no-auth` installs the built-in authentication scaffold
(auth is the default), and `--install/--no-install` runs the whole
local setup for you — virtualenv, `pip install -e .`, migrations,
`npm install`, and the frontend build. When every install step
succeeds, the printed next steps shrink to `cd`, activate, and
`fastplace run dev`; a failed step keeps the full manual list, with
the failing step's error shown so you can finish by hand.

## Install dependencies

Skip this section when you scaffolded with `fastplace new --install` —
otherwise:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm install
```

## Environment

```bash
cp .env.example .env
```

Defaults are runnable as-is: SQLite, local env, a generated app key. Every
value is documented inline in `.env.example` — it is the canonical list of
configuration keys, kept in sync by the framework itself.

## Database

```bash
fastplace migrate      # run pending Alembic migrations
fastplace db:seed      # optional: demo data
```

Switching databases is one env pair (the CLI can write it for you):

```bash
fastplace db:configure postgresql   # sets DATABASE_URL/DATABASE_DRIVER in .env
```

PostgreSQL is recommended for production and required for pgvector-backed
AI features; MySQL is fully supported; MongoDB rides a separate document
adapter.

## Run it

```bash
fastplace run dev      # Uvicorn (reload) + Vite (HMR) together
```

Open <http://127.0.0.1:9000>. Asset requests are proxied to Vite in dev;
for production see `fastplace serve` in the
[deployment guide](/guides/deployment).

## The layout

```
app/
  http/            # controllers, requests, middleware
  modules/<name>/  # bounded feature modules
    models/  repositories/  services/
  ai/              # agents, tools, vector stores
  jobs/            # background jobs
routes/            # web.py (bridge), api.py (/api/v1), ai.py (SSE)
resources/js/      # React app: pages/, layouts/, components/
database/          # migrations/, seeders/
config/            # app.py, database.py, auth.py, ai.py
```

The dependency direction is enforced (a CI gate, not a convention):
controllers may import services, services may import repositories — never
the reverse, and controllers never touch models directly.

## Your first module

```bash
fastplace make:controller Project
fastplace make:model Project -m      # model + migration
```

Wire a route, delegate to a service, render a page:

```python
# routes/web.py
router.get("/projects", ProjectController, "index")


# app/http/controllers/project_controller.py
class ProjectController(Controller):
    async def index(self, request):
        result = await ListProjects().handle()
        return render(request, component="Projects/Index", props=result.props())
```

Then create `resources/js/pages/Projects/Index.jsx` and read
`usePage().props`. The [bridge guide](/guides/pages-and-the-bridge) covers
the full round trip.

## Next steps

- [Pages & the bridge](/guides/pages-and-the-bridge)
- [Database & ORM](/guides/database)
- [Testing](/guides/testing) — the suite your app inherits
