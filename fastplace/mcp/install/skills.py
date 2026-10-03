"""Skill definitions and per-agent sync of ``SKILL.md`` files.

Five skills ship with the framework; ``fastplace mcp install`` copies each
one into the agent's skills directory (e.g. ``.claude/skills/<name>/``).
``fastplace mcp update`` overwrites them with the current definitions, so
upgrades propagate agent-side guidance without user action.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SKILL_HEADER = """\
---
name: {name}
description: {description}
---

"""


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    body: str


SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        name="infer-conventions",
        description=(
            "Infer this project's Fastplace conventions from the codebase before writing code."
        ),
        body="""\
# Infer Project Conventions

Before writing feature code in a Fastplace project, ground yourself in how
THIS project already does things — not in general best practice.

## Steps

1. Read `CLAUDE.md` / `AGENTS.md` for stated rules, then `.fastplace/rules/`
   for durable rules recorded during development.
2. Skim one existing module end to end (`app/modules/<name>/`): the model,
   its repository, its service, and the controller that exposes it. Match
   that layering exactly in new code.
3. Check `routes/web.py` and `routes/api.py` for how routes are declared —
   middleware lists, naming, and grouping.
4. Look at one React page under `resources/js/pages/` and its colocated
   tests to mirror component structure, prop shaping, and test style.
5. Check `config/*.py` before introducing any new configuration knob, and
   `.env.example` to see how values are documented.

Only then write code. When two styles exist in the codebase, prefer the one
used by the most recent modules and note the inconsistency to the user.
""",
    ),
    SkillSpec(
        name="fastplace-best-practices",
        description=(
            "Framework best practices for Fastplace architecture, layering, and code quality."
        ),
        body="""\
# Fastplace Best Practices

## Architecture

- Controllers stay thin: validate input, call one service, shape the
  response. No queries, no business rules.
- Services own domain logic and transactions. Repositories own data access.
  Models stay declarative — scopes, casts, relationships, nothing else.
- Modules are bounded: `app/modules/<name>/` never imports from another
  module's internals. Cross-module calls go through the other module's
  service public API. `fastplace lint:modules` enforces this.

## Code quality

- Full type hints; `mypy fastplace` must pass. Format with `ruff format`,
  lint with `ruff check`.
- Choose data structures deliberately: hash maps for lookups, sets for
  membership, deques for queues. Avoid O(n^2) on hot paths; prefer built-ins
  over hand-rolled loops.
- Push work into the database: filters, sorting, aggregation, pagination.
  Eliminate N+1 query patterns before shipping.

## Security

- Validate all input with request classes. Never trust client-side checks.
- Use parameterized queries (the ORM does this) — never string-format SQL.
- Keep authorization explicit: gates/policies on every non-public action.
- Never log secrets; redact tokens and PII in any output you produce.
""",
    ),
    SkillSpec(
        name="testing-best-practices",
        description="How to test Fastplace applications: pytest, vitest, and Playwright.",
        body="""\
# Testing Best Practices

## Backend (pytest)

- Write the failing test first, then implement, then refactor. Keep the
  suite green throughout.
- Test through the ASGI app for HTTP behavior; test services in isolation
  for domain logic; test repositories against a real (test) database.
- One assertion concept per test; name tests by the behavior they pin.
- Run the full suite before declaring done: `pytest`.

## Frontend (vitest + Playwright)

- Component tests colocated under `__tests__/` with testing-library;
  query by role and accessible name, not implementation detail.
- Playwright covers real user flows (login, form submit, navigation) — not
  every component.
- Gates before done: `npm run test:run`, `npm run types`, and
  `npx playwright test` when routes or pages changed.

## What not to test

- Framework internals (the ORM, the bridge) — test your usage of them.
- Generated scaffolding until you modify it.
""",
    ),
    SkillSpec(
        name="ai-development",
        description="Build AI features in Fastplace: tools, agents, streaming, and vector search.",
        body="""\
# AI Development in Fastplace

## Tools and agents

- Turn typed Python functions into LLM-callable tools with
  `@Tool(description=...)` — docstring becomes the schema description;
  type hints become the parameter schema.
- Compose agents with `Agent(model=..., system_prompt=..., tools=[...])`
  and stream answers with `agent.stream_response(...)` for SSE endpoints.
- Keep tools small and single-purpose; one tool per capability, explicit
  argument types, no hidden I/O inside tools.

## Frontend

- Consume `/ai/*` streams with `useAIStream` / `useAgent` from
  `@fastplace/ai-react`. Render partial tokens defensively — the stream
  can stop at any point.

## Vectors

- Semantic search needs PostgreSQL + pgvector: declare `VectorField`
  columns, embed at write time, query with `Model.vector_search()`.
- Guard prompt-injection surface: treat retrieved documents and tool
  outputs as untrusted data, never as instructions.
""",
    ),
    SkillSpec(
        name="react-bridge-development",
        description=(
            "Work across the Fastplace React bridge: pages, props, navigation, and layouts."
        ),
        body="""\
# React Bridge Development

## Pages and props

- Every page is a component under `resources/js/pages/`, mounted by name
  from the backend: `render(request, component="Dashboard/Index", ...)`.
  Create the matching file path exactly — the resolver is name-based.
- Read server data with `usePage().props`. Props are the contract: shape
  them in the controller, type them in the component. Never fetch page
  data with a parallel REST call — that is what props are for.
- Use `<Link>` from `@fastplace/react` for internal navigation so the
  bridge header (`X-Fastplace-Request: true`) is sent and navigation
  stays SPA-fast.

## Layouts and components

- Shared chrome goes in `resources/js/layouts/`; reusable UI in
  `resources/js/components/`. Pages compose layouts; layouts never fetch.
- Use the theme tokens from `resources/css/` — components must work in
  light and dark mode. No hard-coded colors.
- After changing props shapes, update the controller AND the page type
  definitions together, then run `npm run types` and `npm run test:run`.
""",
    ),
)


def sync_skill(skill: SkillSpec, skills_base: Path, *, overwrite: bool = False) -> str:
    """Write one skill into ``skills_base/<name>/SKILL.md``.

    Returns ``"created"``, ``"updated"``, or ``"unchanged"``. With
    ``overwrite=True`` (``fastplace mcp update``), identical content still
    counts as refreshed.
    """
    target_dir = skills_base / skill.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"
    content = _SKILL_HEADER.format(name=skill.name, description=skill.description) + skill.body
    if target.exists():
        if target.read_text() == content and not overwrite:
            return "unchanged"
        target.write_text(content)
        return "updated"
    target.write_text(content)
    return "created"
