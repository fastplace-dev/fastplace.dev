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

from fastplace.errors import FastplaceError
from fastplace.http import lifecycle
from fastplace.http.middleware import Middleware, wrap_middleware
from fastplace.http.request import Request
from fastplace.http.response import Json, Response
from fastplace.http.router import Router, endpoint_adapter
from fastplace.http.websocket import websocket_adapter

API_PREFIX = "/api/v1"
AI_PREFIX = "/ai"


def get_app(
    *,
    routes: Router | None = None,
    api_routes: Router | None = None,
    ai_routes: Router | None = None,
    middleware: list[Middleware] | None = None,
    config: dict[str, Any] | None = None,
    project_root: str | Path | None = None,
) -> FastAPI:
    """Build a FastAPI application around Fastplace routers and middleware.

    ``routes`` mount at the root (bridge pages), ``api_routes`` under
    ``/api/v1``, ``ai_routes`` under ``/ai``.
    """
    from fastplace.config import Config

    cfg = _ConfigShim(config)
    debug = bool(cfg.get("APP_DEBUG", default=False))

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
        debug=debug,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.fastplace_root = str(project_root or cfg.root)
    _mount_routes(app, routes=routes, api_routes=api_routes, ai_routes=ai_routes)
    _install_middleware(app, middleware or [])
    _install_error_handlers(app, debug=debug)
    return app


def create_app(project_root: str | Path | None = None) -> FastAPI:
    """Bootstrap a full project application: .env, config, routes, static, DB."""
    from fastplace.config import load_env, reset_config

    root = Path(project_root) if project_root else Path.cwd()
    load_env(root / ".env")
    reset_config(root)
    _ensure_import_root(root)

    web = _load_router_module(root, "routes.web")
    api = _load_router_module(root, "routes.api")
    ai = _load_router_module(root, "routes.ai")

    middleware = _middleware_from_config(root)
    app = get_app(
        routes=web,
        api_routes=api,
        ai_routes=ai,
        middleware=middleware,
        project_root=root,
    )
    app.state.fastplace_root = str(root)
    _install_static_mounts(app, root)
    _register_db_lifecycle(root)
    return app


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


class _ConfigShim:
    """Config lookup honoring explicit overrides, then env/config modules."""

    def __init__(self, overrides: dict[str, Any] | None, root: str | Path | None = None) -> None:
        self.overrides = overrides or {}
        self._config = None
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
    except ModuleNotFoundError:
        return None
    router = getattr(module, "router", None)
    if router is None and dotted == "routes.web":
        return None
    return router


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


def _mount_routes(
    app: FastAPI,
    *,
    routes: Router | None,
    api_routes: Router | None,
    ai_routes: Router | None,
) -> None:
    api = APIRouter()
    if routes:
        _register_router(api, routes)
    if api_routes:
        _register_router(api, api_routes, prefix=API_PREFIX)
    if ai_routes:
        _register_router(api, ai_routes, prefix=AI_PREFIX)
    app.include_router(api)


def _register_router(target: APIRouter, router: Router, prefix: str = "") -> None:
    for route in router.routes:
        target.add_api_route(
            prefix + route.path,
            endpoint_adapter(route.handler),
            methods=[route.method],
            name=route.name or route.handler.__name__,
        )
    for ws_route in router.websocket_routes:
        target.add_api_websocket_route(
            prefix + ws_route.path,
            websocket_adapter(ws_route.handler),
            name=ws_route.name or ws_route.handler.__name__,
        )


def _install_middleware(app: FastAPI, middleware: list[Middleware]) -> None:
    # First added = outermost; keep registration order stable for users.
    for mw in middleware:
        app.add_middleware(wrap_middleware(mw), mw=mw)


def _install_error_handlers(app: FastAPI, *, debug: bool) -> None:
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(FastplaceError)
    async def fastplace_error_handler(request: Any, exc: FastplaceError) -> Response:
        payload: dict[str, Any] = {"message": exc.message}
        errors = getattr(exc, "errors", None)
        if errors:
            payload["errors"] = errors
        return Json(payload, status_code=exc.status_code)

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
        detail = repr(exc) if debug else "Server error."
        return Json({"message": "Server error.", "debug": detail}, status_code=500)


def _install_static_mounts(app: FastAPI, root: Path) -> None:
    build_dir = root / "public" / "build"
    if build_dir.is_dir():
        app.mount("/build", StaticFiles(directory=str(build_dir)), name="build")
    public_dir = root / "public"
    if public_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(public_dir), check_dir=False), name="public")


def _register_db_lifecycle(root: Path) -> None:
    from fastplace.config import Config

    cfg = Config(root)
    if not cfg.get("DATABASE_URL", default=None):
        return

    @lifecycle.on_shutdown
    async def _dispose_engines() -> None:
        from fastplace.db import db

        await db.dispose()
