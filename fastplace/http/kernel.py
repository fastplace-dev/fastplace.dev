"""Fastplace kernel — the FastAPI application factory (ADR-006).

Fastplace owns bootstrapping above the FastAPI application core: middleware
registration, lifespan wiring, route mounting, and exception translation.
Application code imports ``fastplace.http`` and never ``fastapi`` directly.
"""

from __future__ import annotations

import importlib
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRouter
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from fastplace.errors import ConfigurationError, FastplaceError
from fastplace.http import lifecycle
from fastplace.http.compression import CompressionMiddleware
from fastplace.http.maintenance import MaintenanceMiddleware
from fastplace.http.middleware import Middleware, wrap_middleware
from fastplace.http.response import Html, Json, Response
from fastplace.http.router import Router, endpoint_adapter, resolve_route_middleware, route_bindings
from fastplace.http.websocket import websocket_adapter
from fastplace.logging.middleware import RequestIdMiddleware

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
    root = Path(project_root or cfg.root or Path.cwd())
    # Prerendered pages must outrank the router (Starlette matches routes
    # before mounts, so a StaticFiles mount never could) — added FIRST so
    # the lookup ends up innermost: prerendered HTML flows back out through
    # security headers and compression like every other HTML response.
    # The dev runtime is exempt: one prerender run must not freeze live
    # pages while the developer edits (FASTPLACE_RUNTIME=dev is set by
    # `fastplace run dev`; serve and direct uvicorn boots keep the lookup).
    if os.environ.get("FASTPLACE_RUNTIME") != "dev":
        from fastplace.http.prerender_static import PrerenderStaticFiles

        app.add_middleware(
            PrerenderStaticFiles, prerender_dir=root / "public" / "build" / "prerender"
        )
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
    # Request-scoped locale: ?locale= -> Accept-Language -> LOCALE default,
    # resolved once per request so trans() agrees across the whole response.
    from fastplace.i18n import LocaleMiddleware

    app.add_middleware(LocaleMiddleware)
    # Compression sits outside the security headers so it sees the final
    # header set; pure ASGI, streaming-safe (see fastplace/http/compression.py).
    app.add_middleware(CompressionMiddleware)
    app.add_middleware(_QueryTrackerMiddleware)
    _install_session_middleware(app, cfg, app_env=app_env)
    # The broadcast WebSocket endpoint — the socket half of the broadcasting
    # layer. Mounted here (not from app routes) because it is framework
    # surface; BROADCAST_ENABLED=false mounts nothing.
    if bool(cfg.get("BROADCAST_ENABLED", default=True)):
        from fastplace.http.broadcast_ws import BROADCAST_WS_PATH, broadcast_socket
        from fastplace.http.websocket import websocket_adapter

        app.add_api_websocket_route(
            BROADCAST_WS_PATH,
            websocket_adapter(broadcast_socket),
            name="broadcast",
        )
    # Added last -> outermost (add_middleware inserts at index 0). A down
    # app answers with the 503 gate before sessions mint cookies or the
    # bridge/React surface is reached.
    app.add_middleware(
        MaintenanceMiddleware,
        root=str(root),
    )
    # plat-G9 request correlation, outermost: minted (or accepted) before
    # anything downstream runs, so every log line and the response itself
    # carry the id — including maintenance 503s and error responses.
    app.add_middleware(RequestIdMiddleware)
    _install_error_handlers(app, debug=debug, root=root)
    return app


def create_app(project_root: str | Path | None = None) -> FastAPI:
    """Bootstrap a full project application: .env, config, routes, static, DB."""
    from fastplace.config import load_env, reset_config

    root = Path(project_root) if project_root else Path.cwd()
    load_env(root / ".env")
    reset_config(root)
    # plat-G9: the storage/logs promise (deployment guide) becomes true with
    # zero app config — channels, rotation and retention resolve from LOG_*
    # config here. Idempotent: an already-configured process is a no-op.
    from fastplace.logging import configure_logging

    configure_logging(root=root)
    _ensure_import_root(root)
    # One sweep before any importer runs: sys.modules is process-global
    # while ``app.*`` is project-local, and the importers below only sweep
    # behind their own file-existence gates — a project without app/jobs or
    # app/ai/vectors would otherwise keep the previous project's cached app
    # package (its module routes, gates, handlers) alive for this boot.
    from fastplace.queue import _evict_stale_app_modules

    _evict_stale_app_modules(root)

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
    # Framework passkey surface merges into the auth router when
    # AUTH_PASSKEYS is enabled — apps write zero route code for it.
    from fastplace.auth.passkeys_routes import mount_passkey_routes

    auth = mount_passkey_routes(auth)
    api = _load_router_module(root, "routes.api")
    ai = _load_router_module(root, "routes.ai")
    web, api = _merge_module_routers(root, web, api)

    middleware = middleware_from_config(root)
    app = get_app(
        routes=web,
        auth_routes=auth,
        api_routes=api,
        ai_routes=ai,
        middleware=middleware,
        project_root=root,
    )
    app.state.fastplace_root = str(root)
    # SAQ dashboard mount — a no-op unless QUEUE_DASHBOARD_ENABLED and the
    # saq driver are both on (the guard wraps the whole embedded app).
    from fastplace.http.dashboard import mount_dashboard

    mount_dashboard(app)
    _install_static_mounts(app, root)
    _register_db_lifecycle(root)
    _register_broadcast_lifecycle()
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
    # The routes surface is optional: an absent file means an absent router.
    # The file check must come BEFORE the import — importlib reuses
    # sys.modules, so a routes module imported for a different project root
    # would otherwise leak its router into this boot (two projects in one
    # process, or a test's throwaway project after the repo's routes were
    # imported). The eviction itself runs before the gate, not after: a
    # project lacking the file must still clean the previous project's
    # cached module under the same dotted name.
    expected = root.joinpath(*dotted.split(".")).with_suffix(".py")
    _evict_stale_route_module(root, dotted, expected)
    if not expected.is_file():
        return None
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


def _evict_stale_route_module(root: Path, dotted: str, expected: Path) -> None:
    """Drop cached route modules that resolve to a different project's file.

    sys.modules is process-global while routes modules are project-local:
    when the cache holds another root's module under the same dotted name
    (or a parent package whose ``__path__`` points elsewhere), a boot would
    silently import — or fail to find — the wrong file. Evicting both lets
    importlib resolve this project's own file.
    """
    cached = sys.modules.get(dotted)
    if cached is not None:
        cached_file = getattr(cached, "__file__", None)
        if cached_file is None or Path(cached_file).resolve() != expected.resolve():
            del sys.modules[dotted]
    parent, _, _ = dotted.rpartition(".")
    if not parent:
        return
    parent_mod = sys.modules.get(parent)
    if parent_mod is None:
        return
    parent_paths = list(getattr(parent_mod, "__path__", None) or [])
    expected_dir = (root / parent.replace(".", "/")).resolve()
    if not parent_paths or Path(parent_paths[0]).resolve() != expected_dir:
        del sys.modules[parent]


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
        expected = info.path / "routes.py"
        if not expected.is_file():
            continue
        # Same stale-module discipline as _load_router_module: a cached
        # routes.py from another root under the same dotted name must not
        # answer this boot's import.
        _evict_stale_route_module(root, info.dotted("routes"), expected)
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


def middleware_from_config(root: Path) -> list[Middleware]:
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
    # Built-in opt-in alias (app overrides always win): signed download /
    # unsubscribe-style links validate themselves via middleware=["signed"].
    from fastplace.http.urls import SignedMiddleware

    registry.setdefault("signed", SignedMiddleware)
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
    # Route/config optimization caches (audit sweep-G14) were measured and
    # rejected: registration costs ~0.2 ms per route (dominated by FastAPI's
    # own route-object construction, which a build-time manifest cannot
    # skip) and config() reads are memoized (~0.5 µs). Realistic apps pay
    # tens of ms once per worker — a persisted manifest would trade that
    # for cache-invalidation and drift risk. Revisit only if boot profiling
    # ever shows route registration as a real cost.
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
            endpoint_adapter(
                route.handler,
                chain,
                route_bindings(route.handler, prefix + route.path),
            ),
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
    store = session_store(config_get=cfg.get)
    # The broadcast ws endpoint cannot see the middleware's store (it skips
    # non-http scopes), so the instance is stashed for the handshake loader.
    app.state.fastplace_session_store = store
    app.add_middleware(
        ServerSessionMiddleware,
        store=store,
        cookie_name=str(cfg.get("SESSION_COOKIE", default="fastplace_session")),
        lifetime=int(cfg.get("SESSION_LIFETIME", default=7200)),
        path=str(cfg.get("SESSION_PATH", default="/")),
        domain=str(cfg.get("SESSION_DOMAIN", default="") or "") or None,
        secure=app_env == "production",
    )


def _wants_redirect_back(request: Any) -> bool:
    """Redirect-back policy shared with auth middleware — see
    :mod:`fastplace.http.redirect_back` for the single source of truth."""
    from fastplace.http.redirect_back import wants_redirect_back

    return wants_redirect_back(request)


def _error_page_override(
    root: Path, status_code: int, headers: dict[str, str] | None
) -> Html | None:
    """The app's own branded page for this status, if one ships.

    ``public/errors/404.html`` (any status) replaces the framework page
    verbatim — the plain-file contract mirrors static hosting conventions.
    """
    page = root / "public" / "errors" / f"{status_code}.html"
    try:
        if page.is_file():
            return Html(page.read_text(encoding="utf-8"), status_code=status_code, headers=headers)
    except (OSError, ValueError):
        # Unreadable file or bad encoding (UnicodeDecodeError is a
        # ValueError) — fall back to the framework page.
        return None
    return None


def _install_error_handlers(app: FastAPI, *, debug: bool, root: Path) -> None:
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(FastplaceError)
    async def fastplace_error_handler(request: Any, exc: FastplaceError) -> Response:
        from fastplace.errors import ValidationError
        from fastplace.http.redirect_back import redirect_back_with_errors

        if isinstance(exc, ValidationError) and _wants_redirect_back(request):
            return redirect_back_with_errors(request, getattr(exc, "errors", None) or {})
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
        # Domain errors may carry extra wire headers (the rate limiter's
        # X-RateLimit-* live on ThrottleRequestsError) — merge them after
        # Retry-After so a specific attribute always wins.
        extra_headers = getattr(exc, "headers", None)
        if isinstance(extra_headers, dict):
            for name, value in extra_headers.items():
                headers.setdefault(str(name), str(value))
        from fastplace.http.error_pages import http_error_page, wants_html

        # Validation keeps the 422 JSON envelope on every path — it is
        # form data for the bridge/API and the no-JS redirect-back flow,
        # not an error page. Other domain errors (403, 429, …) share the
        # browser contract: app override first, framework page after.
        if wants_html(request) and not isinstance(exc, ValidationError):
            override = _error_page_override(root, exc.status_code, headers)
            if override is not None:
                return override
            return http_error_page(
                request, exc.status_code, exc.message, debug=debug, headers=headers
            )
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
        from fastplace.http.error_pages import http_error_page, wants_html

        # Headers carried by the exception (Retry-After on 429, the CSRF
        # retry hint on 419, WWW-Authenticate on 401) survive both paths.
        headers = dict(exc.headers) if exc.headers else None
        if wants_html(request):
            override = _error_page_override(root, exc.status_code, headers)
            if override is not None:
                return override
            return http_error_page(
                request, exc.status_code, str(exc.detail), debug=debug, headers=headers
            )
        return Json({"message": str(exc.detail)}, status_code=exc.status_code, headers=headers)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Any, exc: Exception) -> Response:
        # plat-G9: the response stays generic (by design), so the traceback
        # must land in storage/logs — with the request's correlation id —
        # or a production 500 leaves no trace anywhere. The id comes from
        # the scope stamp: exception propagation already reset the
        # contextvar by the time ServerErrorMiddleware calls this handler.
        import logging

        from fastplace.http.error_pages import (
            debug_error_page,
            production_error_page,
            wants_html,
        )
        from fastplace.logging.context import request_context

        request_id = (getattr(request, "scope", None) or {}).get("fastplace_request_id", "")
        with request_context(request_id):
            logging.getLogger("fastplace.http").exception(
                "unhandled exception during %s %s", request.method, request.url.path
            )

        # Browser navigations get a styled page (rich in debug, generic in
        # production); API clients and the SPA bridge keep the JSON contract.
        # The app's own 500.html override wins in both modes — a branded
        # error page is not debug output.
        if wants_html(request):
            override = _error_page_override(root, 500, None)
            if override is not None:
                return override
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


class CachedStaticFiles(StaticFiles):
    """Static files with the serve cache policy (serve-G5).

    Vite emits content-hashed filenames under ``assets/`` — those are
    immutable, so browsers may cache them for a year. Everything else
    (manifest.json, robots.txt, un-hashed files) can change between
    deploys and gets a short revalidation window. Only successful file
    responses pass through here; 404s raise before a header is set.

    ``immutable_prefix`` scopes the year-long policy: the build mount
    hashes its ``assets/`` output, but the public-root mount serves Vite's
    ``publicDir`` files un-hashed, so it passes ``None`` and everything
    there revalidates after 5 minutes.
    """

    IMMUTABLE = "public, max-age=31536000, immutable"
    SHORT = "public, max-age=300"

    def __init__(self, *args: Any, immutable_prefix: str | None = "assets", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # StaticFiles realpaths the directory when serving; without the
        # matching resolve here, a symlinked project root makes every
        # relative path fail the prefix check (all SHORT, no IMMUTABLE).
        if self.directory is not None:
            self.directory = Path(self.directory).resolve()
        self.immutable_prefix = immutable_prefix

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        full_path = args[0] if args else kwargs.get("full_path", "")
        relative = str(full_path).removeprefix(str(self.directory) + os.sep)
        prefix = self.immutable_prefix
        immutable = prefix is not None and (
            relative.startswith(f"{prefix}{os.sep}") or relative == prefix
        )
        response.headers["Cache-Control"] = self.IMMUTABLE if immutable else self.SHORT
        return response


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
        app.mount("/build", CachedStaticFiles(directory=str(build_dir)), name="build")
    if public_dir.is_dir():
        app.mount(
            "/",
            # Public-root files (favicon, robots.txt, Vite publicDir copies)
            # are un-hashed: never immutable, always revalidate quickly.
            CachedStaticFiles(directory=str(public_dir), check_dir=False, immutable_prefix=None),
            name="public",
        )


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

        # Dispose without resetting the registry: db.dispose() follows the
        # clean await with reset_manager(), which — running inside the live
        # shutdown loop — logged a misleading "dropped without disposal"
        # warning on every graceful stop.
        await db.manager.dispose()


def _register_broadcast_lifecycle() -> None:
    """Close the process broadcast bus on shutdown under the redis driver.

    Only the redis bus holds a broker connection and a listener task — the
    memory driver registers nothing (idle apps keep the hook list clean).
    The listener itself starts lazily on first subscribe/publish; startup
    needs no hook.
    """
    from fastplace.config import config

    if str(config("BROADCAST_DRIVER", default="memory")).lower() != "redis":
        return

    from fastplace.broadcasting import broadcast_bus

    bus = broadcast_bus()

    @lifecycle.on_shutdown
    async def _close_broadcast_bus() -> None:
        await bus.close()


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
