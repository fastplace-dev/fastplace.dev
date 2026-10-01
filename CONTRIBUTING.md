# Contributing to Fastplace

Thanks for helping build Fastplace. This guide covers the working
agreement: how to set up, which gates your change must pass, and what a
pull request is expected to look like.

## Development setup

1. Fork the repository and create a branch from `master`.
2. Clone your fork and create a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev]"
   python -m pip install -e ./packages/tenancy
   ```

3. Install the frontend workspaces (Node 20+):

   ```bash
   npm install
   ```

4. Copy `.env.example` to `.env` and set `APP_KEY` (a generated 32+ byte
   secret) before booting the app.

## Development loop

- `fastplace run dev` — ASGI backend with live reload + Vite dev server
- `fastplace shell` — interactive shell with models and framework context
- `fastplace lint:modules` — module boundary gate (runs on every save in dev)

## Testing — tests come first

Work test-driven: write the failing test, watch it fail, then implement.
Keep the suite green throughout.

Backend (pytest):

```bash
pytest                          # full suite
pytest tests/http/test_render.py  # one file
pytest -k <pattern>             # one selection
```

Frontend (vitest, colocated `__tests__/` directories):

```bash
npm run test:run                # once
npm run test                    # watch mode
```

End to end (Playwright):

```bash
npx playwright test
```

## Gates your change must pass

```bash
ruff format .                   # formatting
ruff check .                    # lint
mypy fastplace packages/tenancy/src
fastplace lint:modules          # module boundaries (hard gate)
npm run types                   # frontend typecheck
npm run lint:check              # ESLint
npm run format:check            # Prettier
```

CI runs all of these plus the database compatibility matrix, so a red
gate locally is a red build.

## Architecture expectations

- Controllers → Services → Repositories → Models. Controllers hold no
  business logic; repositories own data access; modules stay
  self-contained (`fastplace lint:modules` enforces the boundaries).
- Routes in `routes/*.py` stay thin; domain logic lives in
  `app/modules/<name>/services/`.
- Prefer hash-based lookups over scans, push filtering and aggregation
  into the database, and design for pagination on anything that grows.
- `docs/framework_architectural_blueprint.md` is the source of truth —
  when your change moves the architecture, update the blueprint.

## Naming guardrail

Refer to other software projects only by neutral pattern names —
"declarative", "fluent", "active-record style", "server-driven SPA
bridge" — never by product name, in code, comments, docstrings, tests,
docs, package metadata, or commit messages. The two permitted exceptions
are the Acknowledgements sections of `README.md` and
`docs/framework_architectural_blueprint.md`.

## Commit style

Conventional commits, present tense, plain text — matching the history:

```
feat(queue): bounded memory drain for the memory driver
fix(auth): bridge CSRF token refresh on stale sessions
docs(prerender): changelog entry
chore(release): fastplace 0.4.0
```

## Pull request expectations

- One logical change per PR, with the tests that prove it.
- All gates green before review.
- A short rationale in the description: what changed, why, and how it
  was verified. For bug fixes, include the reproduction the tests pin.
- New public API deserves a docs touch: guide, blueprint, or docstring.
