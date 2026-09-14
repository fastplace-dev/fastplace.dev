# API overview

The public surface, one screen at a time. Import paths are the contract;
everything here is typed (mypy-checked) and covered by the test suite.

## fastplace.http

| Symbol | Kind | Purpose |
|---|---|---|
| `create_app` / `get_app` | fn | build the ASGI application |
| `Router` | class | `router.get(path, Controller, "action", name=...)` |
| `Controller` | class | base for controllers |
| `Middleware` | class | `async handle(request, call_next)` |
| `Request` | class | params/session/user/json/form, `validate(Schema)`, `.starlette` escape |
| `Json` `Html` `Text` `Redirect` `Stream` `File` `NoContent` | classes | response family |
| `render` | fn | bridge page response (`component=`, `props=`) |
| `WebSocket` | class | websocket wrapper |

Route prefixes: `AI_PREFIX` (`/ai`), `API_PREFIX` (`/api/v1`).

## fastplace.orm

| Symbol | Kind | Purpose |
|---|---|---|
| `Model` | class | declarative base; `where/find/all/create/update/delete`, timestamps |
| `Field` | fn | column options (`foreign_key=`, `unique=`, `index=`, `default=`) |
| `belongs_to` `has_one` `has_many` `many_to_many` `pivot_table` | fns | relationships |
| `morph_many` `morph_one` `morph_to` `morph_map` | fns | polymorphic relationships |
| `GlobalScope` | class | named criteria on every query |
| `Paginator` | class | page/limit pagination |
| `VectorField` | fn | embedding column (backend-aware) |

Model classmethods: `add_global_scope`, `remove_global_scope`,
`without_global_scope(s)` (per-query), `with_(...)` eager loads,
`vector_search`.

## fastplace.orm.documents

`Document` (MongoDB base): `where/find/first/count/create/insert`;
`DocumentQuery`: `where/sort/skip/limit/get/first/count/update/delete`.
Extension hooks: `__query_class__`, `_build_payload`.

## fastplace.auth

`SessionGuard` (`login/logout/user`), `TokenGuard` (`issue_for/issue/
decode`), `OrmUserProvider`, `Hash` (`make/check`); middleware
`ResolveUserMiddleware`, `CsrfMiddleware`. Config: `AUTH_DEFAULT_GUARD`,
`AUTH_GUARDS`, `AUTH_PROVIDERS`.

## fastplace.ai

| Symbol | Kind | Purpose |
|---|---|---|
| `Tool` | deco | `@Tool(description=...)` — typed function → LLM tool schema |
| `Agent` | class | model + system_prompt + tools → `stream_response(message, history=None)` SSE |
| `embed` / `embed_many` | fns | config-driven embeddings |
| `tool_registry` / `registered_tools` | registry | registered tool specs |
| `vector_registry` / `reset_vector_registry` | registry + reset | vector stores (`dict[str, VectorStore]`) |

## fastplace.queue / fastplace.cache

- `Job` (decorator), `queue()` (configured driver), `set_queue(driver)`
  (process-wide install), `reset_queue()`.
- `DomainEvent` + `Model.__dispatches__` for event→queue dispatch.
- `cache()` (configured store), `CacheStore` protocol (`get/put/forget/
  flush/remember`), `MemoryCache`, `RedisCache`.

## fastplace.db

`db` manager (`create_all/drop_all/raw/connection/transaction/dispose`),
`reset_db()`, `capabilities` registry (`supports("row_level_security")`
&c.).

## fastplace.config

`config(key, default=...)` — env-first, config-module defaults,
type-coerced against the default. `load_env()`, `reset_config()`.

## CLI

`fastplace run dev` · `serve` · `shell` · `migrate` · `migrate:rollback`
· `migration:status` · `db:seed` · `db:configure <driver>` ·
`make:controller|model|agent|page|migration` · `new <path>` ·
`lint:modules` · `lint:watch`.

## @fastplace/react

`usePage()` (props + page state), `Link` (bridge navigation), the page
resolver registry, layout application.

## @fastplace/ai-react

`useAIStream({endpoint})`, `useAgent({endpoint})` — SSE consumption with
abort and batched rendering.

## fastplace_tenancy

`Company` `CompanyMembership` `CompanyScopedModel` `company_context`
`require_company_context` `current_company_id` `require_membership`
`CompanyContextMiddleware` `request_company_id` `CompanyCacheStore`
`TenantQueue` `tenant_job` `company_root` `company_storage_path`
`TenantSearchService` `TenantVectorStore` `CompanyDocument`
`enable_company_rls` `set_rls_company` `supports_rls`.
