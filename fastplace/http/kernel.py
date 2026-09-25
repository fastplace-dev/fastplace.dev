"""Fastplace kernel — the FastAPI application factory (ADR-006).

Fastplace owns bootstrapping above the FastAPI application core: middleware
registration, lifespan wiring, route mounting, and exception translation.
Application code imports ``fastplace.http`` and never ``fastapi`` directly.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRouter
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from fastplace.errors import ConfigurationError, FastplaceError
from fastplace.http import lifecycle
from fastplace.http.maintenance import MaintenanceMiddleware
from fastplace.http.middleware import Middleware, wrap_middleware
from fastplace.http.response import Json, Response
from fastplace.http.router import Router, endpoint_adapter, resolve_route_middleware
from fastplace.http.websocket import websocket_adapter

API_PREFIX = "/api/v1"
AI_PREFIX = "/ai"

# Baseline hardening applied to every response unless the app overrides it.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)


class _SecurityHeadersMiddleware:
    """Pure-ASGI middleware appending baseline security headers."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _QueryTrackerMiddleware:
    """Pure-ASGI middleware scoping query instrumentation to one request.

    Opens an ``activate_tracker()`` window so SQL logging, slow-query
    warnings, and N+1 detection attribute to the request that caused them.
    When an exception escapes, the window closes before the outermost error
    handler runs — the stats snapshot is stashed on the scope so debug error
    payloads can still report what the request executed.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from fastplace.orm.instrumentation import activate_tracker

        with activate_tracker() as tracker:
            try:
                await self.app(scope, receive, send)
            except BaseException:
                scope["fastplace_query_stats"] = tracker.stats
                raise


def get_app(
    *,
    routes: Router | None = None,
    auth_routes: Router | None = None,
    api_routes: Router | None = None,
    ai_routes: Router | None = None,
    middleware: list[Middleware] | None = None,
    route_middleware: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    project_root: str | Path | None = None,
) -> FastAPI:
    """Build a FastAPI application around Fastplace routers and middleware.

    ``routes`` mount at the root (bridge pages), ``auth_routes`` at the root
    beside them (credential POST endpoints), ``api_routes`` under
    ``/api/v1``, ``ai_routes`` under ``/ai``.
    """

    cfg = _ConfigShim(config)
    app_env = str(cfg.get("APP_ENV", default="local")).lower()
    # Debug details never ship from a production environment, even when a
    # stray APP_DEBUG=true survives in the environment. Note the FastAPI
    # `debug` flag stays off: Starlette's ServerErrorMiddleware would otherwise
    # bypass our JSON error handler with a plaintext traceback.
    debug = bool(cfg.get("APP_DEBUG", default=False)) and app_env != "production"
    enable_docs = app_env != "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await lifecycle.run_startup()
        try:
            yield
        finally:
            await lifecycle.run_shutdown()

    app = FastAPI(
        title=str(cfg.get("APP_NAME", default="Fastplace")),
        lifespan=lifespan,
        docs_url="/api/docs" if enable_docs else None,
        openapi_url="/api/openapi.json" if enable_docs else None,
        redoc_url=None,
    )
    app.state.fastplace_root = str(project_root or cfg.root)
    _mount_routes(
        app,
        routes=routes,
        auth_routes=auth_routes,
        api_routes=api_routes,
        ai_routes=ai_routes,
        route_middleware=_route_middleware_registry(route_middleware, cfg),
    )
    _install_middleware(app, middleware or [])
    app.add_middleware(_SecurityHeadersMiddleware)
    app.add_middleware(_QueryTrackerMiddleware)
    _install_session_middleware(app, cfg, app_env=app_env)
    # Added last -> outermost (add_middleware inserts at index 0). A down
    # app answers with the 503 gate before sessions mint cookies or the
    # bridge/React surface is reached.
    app.add_middleware(
        MaintenanceMiddleware,
        root=str(project_root or cfg.root or Path.cwd()),
    )
    _install_error_handlers(app, debug=debug)
    return app


def create_app(project_root: str | Path | None = None) -> FastAPI:
    """Bootstrap a full project application: .env, config, routes, static, DB."""
    from fastplace.config import load_env, reset_config

    root = Path(project_root) if project_root else Path.cwd()
    load_env(root / ".env")
    reset_config(root)
    _ensure_import_root(root)
    # app/ai/vectors registrations must exist before anything resolves
    # active_vector_store() — boot is the one place a project's stores are
    # guaranteed to be importable.
    from fastplace.ai.vectors import import_vector_stores

    import_vector_stores(root)

    # Same reasoning for app/jobs: @Job registrations must exist before the
    # first domain event dispatches, or the event→queue bridge would find no
    # consumer and silently stay in-process.
    from fastplace.queue import import_jobs

    import_jobs(root)

    # Gates need the same treatment: app/auth/gates.py registrations must
    # exist before the first request authorizes — an empty registry would
    # fail loud (ConfigurationError) on every can:/authorize() check.
    from fastplace.authz import import_gates

    import_gates(root)

    web = _load_router_module(root, "routes.web")
    auth = _load_router_module(root, "routes.auth")
    api = _load_router_module(root, "routes.api")
    ai = _load_router_module(root, "routes.ai")
    web, api = _merge_module_routers(root, web, api)

    middleware = _middleware_from_config(root)
    app = get_app(
        routes=web,
        auth_routes=auth,
        api_routes=api,
        ai_routes=ai,
        middleware=middleware,
        project_root=root,
    )
    app.state.fastplace_root = str(root)
    _install_static_mounts(app, root)
    _register_db_lifecycle(root)
    # Default shared props: every page payload carries the auth snapshot
    # (spec §4.16). Registered here — the real boot path — never in get_app,
    # whose test factories pin exact props shapes.
    from fastplace.auth.middleware import shared_auth_props
    from fastplace.http.render import share

    share(shared_auth_props)
    return app


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


class _ConfigShim:
    """Config lookup honoring explicit overrides, then env/config modules."""

    def __init__(self, overrides: dict[str, Any] | None, root: str | Path | None = None) -> None:
        self.overrides = overrides or {}
        self._config: Any = None
        self.root = root

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.overrides:
            return self.overrides[key]
        if self._config is None:
            from fastplace.config import Config

            self._config = Config(self.root)
        return self._config.get(key, default=default)


def _ensure_import_root(root: Path) -> None:
    import sys

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _load_router_module(root: Path, dotted: str) -> Router | None:
    try:
        module = importlib.import_module(dotted)
    except ModuleNotFoundError as exc:
        # Only the routes module itself may be absent (optional surface). A
        # transitive import failure inside it is a real bug and must fail
        # loudly instead of silently booting an empty app.
        missing = exc.name or ""
        if missing and missing != dotted and not dotted.startswith(f"{missing}."):
            raise
        return None
    router = getattr(module, "router", None)
    if router is None and dotted == "routes.web":
        return None
    return router


def _merge_module_routers(
    root: Path, web: Router | None, api: Router | None
) -> tuple[Router | None, Router | None]:
    """Fold each module's routes.py (web_routes/api_routes) into the central routers.

    Central routes keep first-match priority: module routes append after them.
    A module routes.py that fails to import fails the boot loudly — same
    contract as import_vector_stores/import_jobs/import_gates. A module
    without routes.py, or with None attributes, is skipped silently.
    """
    from fastplace.modules import discover_modules

    for info in discover_modules(root).values():
        if not (info.path / "routes.py").is_file():
            continue
        module = importlib.import_module(info.dotted("routes"))
        web_extra = getattr(module, "web_routes", None)
        api_extra = getattr(module, "api_routes", None)
        if web_extra is not None:
            web = web or Router()
            web.routes.extend(web_extra.routes)
            web.websocket_routes.extend(web_extra.websocket_routes)
        if api_extra is not None:
            api = api or Router()
            api.routes.extend(api_extra.routes)
            api.websocket_routes.extend(api_extra.websocket_routes)
    return web, api


def _middleware_from_config(root: Path) -> list[Middleware]:
    from fastplace.config import Config

    cfg = Config(root)
    declared = cfg.get("MIDDLEWARE", default=[])
    instances: list[Middleware] = []
    for entry in declared or []:
        instances.append(_instantiate_middleware(entry))
    return instances


def _instantiate_middleware(entry: str) -> Middleware:
    module_path, _, class_name = entry.rpartition(".")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    instance = cls()
    if not isinstance(instance, Middleware):  # pragma: no cover - config error
        raise TypeError(f"{entry} is not a fastplace.http.Middleware")
    return instance


def _route_middleware_registry(
    overrides: dict[str, Any] | None, cfg: _ConfigShim
) -> dict[str, Any]:
    """Programmatic aliases first, then ROUTE_MIDDLEWARE dotted paths."""
    registry: dict[str, Any] = dict(overrides or {})
    declared = cfg.get("ROUTE_MIDDLEWARE", default={}) or {}
    if not isinstance(declared, dict):
        # Env vars arrive as strings — a string simply means "not configured".
        declared = {}
    for name, entry in declared.items():
        if name in registry:
            continue
        if isinstance(entry, str):
            module_path, _, class_name = entry.rpartition(".")
            module = importlib.import_module(module_path)
            entry = getattr(module, class_name)
        registry[name] = entry
    return registry


def _mount_routes(
    app: FastAPI,
    *,
    routes: Router | None,
    auth_routes: Router | None = None,
    api_routes: Router | None,
    ai_routes: Router | None,
    route_middleware: dict[str, Any] | None = None,
) -> None:
    api = APIRouter()
    if routes:
        _register_router(api, routes, route_middleware=route_middleware)
    if auth_routes:
        # Credential endpoints live on the root surface beside the web
        # routes (POST /login, /register, /logout) — no prefix.
        _register_router(api, auth_routes, route_middleware=route_middleware)
    if api_routes:
        _register_router(api, api_routes, prefix=API_PREFIX, route_middleware=route_middleware)
    if ai_routes:
        _register_router(api, ai_routes, prefix=AI_PREFIX, route_middleware=route_middleware)
    app.include_router(api)


def _register_router(
    target: APIRouter,
    router: Router,
    prefix: str = "",
    route_middleware: dict[str, Any] | None = None,
) -> None:
    registry = route_middleware or {}
    for route in router.routes:
        chain = tuple(resolve_route_middleware(registry, alias) for alias in route.middleware)
        target.add_api_route(
            prefix + route.path,
            endpoint_adapter(route.handler, chain),
            methods=[route.method],
            name=route.name or getattr(route.handler, "__name__", None) or "endpoint",
        )
    for ws_route in router.websocket_routes:
        target.add_api_websocket_route(
            prefix + ws_route.path,
            websocket_adapter(ws_route.handler),
            name=ws_route.name or getattr(ws_route.handler, "__name__", None) or "endpoint",
        )


def _install_middleware(app: FastAPI, middleware: list[Middleware]) -> None:
    # Starlette's add_middleware inserts at index 0, so the LAST added ends
    # up outermost — register in reverse so the documented contract
    # ("first declared = outermost", e.g. ResolveUser before Csrf) holds.
    for mw in reversed(middleware):
        app.add_middleware(wrap_middleware(mw), mw=mw)  # type: ignore[arg-type]


def _install_session_middleware(app: FastAPI, cfg: _ConfigShim, *, app_env: str) -> None:
    """Server-side sessions on every Fastplace app (opaque-ID cookie + store)."""
    from fastplace.http.session import session_store
    from fastplace.http.session.middleware import ServerSessionMiddleware

    if app_env == "production" and not str(cfg.get("APP_KEY", default="") or ""):
        # An ephemeral per-process key silently invalidates signed URLs and
        # JWTs across workers/restarts — production must fail fast.
        raise ConfigurationError(
            "APP_KEY is required in production — set it in .env "
            "(generate: python -c 'import secrets; print(secrets.token_urlsafe(48))')"
        )
    app.add_middleware(
        ServerSessionMiddleware,
        store=session_store(config_get=cfg.get),
        cookie_name=str(cfg.get("SESSION_COOKIE", default="fastplace_session")),
        lifetime=int(cfg.get("SESSION_LIFETIME", default=7200)),
        path=str(cfg.get("SESSION_PATH", default="/")),
        domain=str(cfg.get("SESSION_DOMAIN", default="") or "") or None,
        secure=app_env == "production",
    )


def _install_error_handlers(app: FastAPI, *, debug: bool) -> None:
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(FastplaceError)
    async def fastplace_error_handler(request: Any, exc: FastplaceError) -> Response:
        payload: dict[str, Any] = {"message": exc.message}
        code = getattr(exc, "code", None)
        if code:
            payload["code"] = code
        errors = getattr(exc, "errors", None)
        if errors:
            payload["errors"] = errors
        headers: dict[str, str] = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        return Json(payload, status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Any, exc: RequestValidationError) -> Response:
        errors: dict[str, list[str]] = {}
        for item in exc.errors():
            loc = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            errors.setdefault(loc or "body", []).append(item.get("msg", "invalid"))
        return Json(
            {"message": "The given data was invalid.", "errors": errors},
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Any, exc: HTTPException) -> Response:
        return Json({"message": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Any, exc: Exception) -> Response:
        from fastplace.http.error_pages import (
            debug_error_page,
            production_error_page,
            wants_html,
        )

        # Browser navigations get a styled page (rich in debug, generic in
        # production); API clients and the SPA bridge keep the JSON contract.
        if wants_html(request):
            if debug:
                return debug_error_page(request, exc)
            return production_error_page(request)
        detail = repr(exc) if debug else "Server error."
        payload: dict[str, Any] = {"message": "Server error."}
        if debug:
            payload["debug"] = detail
            # What the request executed before it died — statement counts and
            # N+1 candidates, never surfaced outside debug mode.
            from fastplace.orm.instrumentation import request_query_stats

            stats = request_query_stats(request)
            if stats is not None:
                payload["queries"] = stats.summary()
        return Json(payload, status_code=500)


def _install_static_mounts(app: FastAPI, root: Path) -> None:
    # A fresh clone ships no public/ at all (nothing under it is tracked),
    # and the first `vite build` can land after the server has already
    # booted — CI smoke runs do exactly that. Create the runtime dirs at
    # startup (public/build is gitignored framework output, like storage/)
    # so the mounts exist from boot and serve assets whenever they appear.
    public_dir = root / "public"
    build_dir = public_dir / "build"
    try:
        build_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only project tree: keep the pre-existing behavior — mount
        # only what already exists and let absent paths 404 cleanly.
        pass
    if build_dir.is_dir():
        app.mount("/build", StaticFiles(directory=str(build_dir)), name="build")
    if public_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(public_dir), check_dir=False), name="public")


def _register_db_lifecycle(root: Path) -> None:
    from fastplace.config import Config

    cfg = Config(root)
    if not cfg.get("DATABASE_URL", default=None):
        return

    @lifecycle.on_shutdown
    async def _dispose_engines() -> None:
        # Drain the in-memory queue first — under the default memory driver,
        # domain events enqueued by web requests would otherwise never run
        # (nothing else consumes them in this process).
        await _drain_memory_queue_on_shutdown()
        from fastplace.db import db

        await db.dispose()


async def _drain_memory_queue_on_shutdown() -> None:
    """Run pending memory-queue jobs at graceful shutdown; log failures."""
    import logging

    from fastplace.queue import MemoryQueue, queue

    memory = queue()
    if not isinstance(memory, MemoryQueue) or not memory.pending:
        return
    # honor_sentinel=False: this process is not a restartable queue worker —
    # nothing on the web path ever consumes the sentinel, so honoring one
    # (e.g. latched on a shared cache by `queue:restart` for saq workers)
    # would silently skip the very jobs this drain exists to run.
    executed = await memory.run_pending(honor_sentinel=False)
    if memory.failures:
        logging.getLogger("fastplace.queue").error(
            "%d/%d shutdown-drained job(s) failed: %s",
            len(memory.failures),
            executed,
            ", ".join(failure.name for failure in memory.failures),
        )
