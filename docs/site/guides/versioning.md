# Versioning

Fastplace follows [Semantic Versioning](https://semver.org/): `PATCH` fixes
bugs, `MINOR` adds backwards-compatible features, `MAJOR` makes breaking
changes.

While the framework is `0.x`, a minor bump may break — the cost of iterating
fast on a young core. Treat `0.MINOR` as the major unit and check the
changelog before bumping.

## What counts as public API

- The `fastplace.*` Python API surface — importable names and their behavior.
- CLI commands and their flags (`fastplace run`, `fastplace migrate`,
  `fastplace make:*`, ...).
- The exported hooks of `@fastplace/react` and `@fastplace/ai-react`
  (`usePage`, `useAIStream`, `useAgent`, ...).
- The `.env` / config contract: every key documented in `.env.example`.
- The migration layout — `database/migrations/` as Alembic-managed state.

Anything not listed above is internal and may change in any release.

For the step-by-step procedure of moving an app between releases — including
the python/npm lockstep matrix — see [Upgrading](./upgrading.md).
