"""Phase 3 adversarial-review fixes — regression tests for confirmed findings.

Security headers, debug/OpenAPI gating by environment, transitive import
errors, handler-name guards, prefix mounting, JSON cycle protection, type-hint
caching, payload escaping, router registration errors, manifest caching.
"""

from __future__ import annotations

import json as jsonlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from fastplace.http import Controller, Json, Request, Router, get_app, render, serialization


def _client(app, *, raise_app_exceptions: bool = False) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


async def test_responses_carry_security_headers():
    r = Router()

    async def ping(request: Request):
        return {"pong": True}

    r.get("/ping", ping)
    app = get_app(routes=r)
    async with _client(app) as c:
        resp = await c.get("/ping")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_error_responses_also_carry_security_headers():
    app = get_app(routes=Router())
    async with _client(app) as c:
        resp = await c.get("/definitely-not-here")
    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------------------------
# Debug detail + OpenAPI gating by environment
# ---------------------------------------------------------------------------


async def test_debug_detail_suppressed_in_production_even_with_app_debug_true():
    r = Router()

    async def boom(request: Request):
        raise RuntimeError("secret-token")

    r.get("/boom", boom)
    app = get_app(
        routes=r,
        config={
            "APP_DEBUG": True,
            "APP_ENV": "production",
            "APP_KEY": "test-secret-key-0123456789abcdef",
        },
    )
    async with _client(app, raise_app_exceptions=False) as c:
        resp = await c.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["message"] == "Server error."
    assert "secret-token" not in jsonlib.dumps(body)


async def test_debug_detail_present_in_local_when_app_debug_true():
    r = Router()

    async def boom(request: Request):
        raise RuntimeError("secret-token")

    r.get("/boom", boom)
    app = get_app(routes=r, config={"APP_DEBUG": True, "APP_ENV": "local"})
    async with _client(app, raise_app_exceptions=False) as c:
        resp = await c.get("/boom")
    body = resp.json()
    assert "secret-token" in body["debug"]


async def test_openapi_disabled_in_production():
    # APP_KEY supplied: production apps refuse to boot without it (kernel hardening)
    app = get_app(
        routes=Router(),
        config={"APP_ENV": "production", "APP_KEY": "test-secret-key-0123456789abcdef"},
    )
    async with _client(app) as c:
        docs = await c.get("/api/docs")
        schema = await c.get("/api/openapi.json")
    assert docs.status_code == 404
    assert schema.status_code == 404


async def test_openapi_enabled_outside_production():
    app = get_app(routes=Router(), config={"APP_ENV": "local"})
    async with _client(app) as c:
        schema = await c.get("/api/openapi.json")
    assert schema.status_code == 200


# ---------------------------------------------------------------------------
# Route module loading — transitive import failures must fail loudly
# ---------------------------------------------------------------------------


def _purge_modules(prefix: str) -> dict:
    """Remove ``prefix`` and its submodules from sys.modules; return them."""
    saved = {k: v for k, v in sys.modules.items() if k == prefix or k.startswith(f"{prefix}.")}
    for key in saved:
        del sys.modules[key]
    return saved


def test_transitive_import_error_in_routes_module_is_raised(tmp_path, monkeypatch):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "__init__.py").write_text("")
    (tmp_path / "routes" / "web.py").write_text(
        "from app.http.controllers.nope import Missing\n"  # broken transitive import
        "router = None\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    from fastplace.http.kernel import _load_router_module

    # The real project's routes package may already be imported — swap it out
    # for the tmp copy, then restore.
    saved = _purge_modules("routes")
    try:
        with pytest.raises(ModuleNotFoundError):
            _load_router_module(tmp_path, "routes.web")
    finally:
        _purge_modules("routes")
        sys.modules.update(saved)


def test_missing_routes_module_still_returns_none(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    from fastplace.http.kernel import _load_router_module

    assert _load_router_module(tmp_path, "routes.definitely_missing") is None


# ---------------------------------------------------------------------------
# Route registration guards
# ---------------------------------------------------------------------------


async def test_handler_without_dunder_name_registers_with_fallback_name():
    from functools import partial

    async def handler(request: Request):
        return {"ok": True}

    r = Router()
    r.get("/partial", partial(handler))  # partials have no __name__
    app = get_app(routes=r)
    async with _client(app) as c:
        resp = await c.get("/partial")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_router_action_with_non_class_handler_raises_type_error():
    async def handler(request: Request):
        return {"ok": True}

    r = Router()
    with pytest.raises(TypeError, match="Controller class"):
        r.get("/x", handler, "index")


def test_router_unknown_action_raises_clear_attribute_error():
    class PingController(Controller):
        async def index(self, request: Request):
            return {"ok": True}

    r = Router()
    with pytest.raises(AttributeError, match="has no action 'show'"):
        r.get("/x", PingController, "show")


# ---------------------------------------------------------------------------
# api_routes / ai_routes prefix mounting (pytest coverage, not only E2E)
# ---------------------------------------------------------------------------


async def test_api_routes_mount_under_api_v1_prefix():
    class HealthController(Controller):
        async def index(self, request: Request):
            return {"status": "ok"}

    api = Router()
    api.get("/health", HealthController, "index", name="api.health")
    app = get_app(api_routes=api)
    async with _client(app) as c:
        resp = await c.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ai_routes_mount_under_ai_prefix():
    async def assistant(request: Request):
        return {"agent": "ready"}

    ai = Router()
    ai.get("/assistant", assistant)
    app = get_app(ai_routes=ai)
    async with _client(app) as c:
        resp = await c.get("/ai/assistant")
    assert resp.status_code == 200
    assert resp.json() == {"agent": "ready"}


# ---------------------------------------------------------------------------
# Response serialization — cycle protection
# ---------------------------------------------------------------------------


async def test_cyclic_payload_raises_value_error_not_recursion_error():
    r = Router()

    async def cyclic(request: Request):
        payload: dict[str, Any] = {"name": "loop"}
        payload["self"] = payload
        return payload

    r.get("/cyclic", cyclic)
    app = get_app(routes=r)
    async with _client(app, raise_app_exceptions=False) as c:
        resp = await c.get("/cyclic")
    assert resp.status_code == 500
    assert resp.json()["message"] == "Server error."


def test_jsonable_cycle_detection_unit():
    from fastplace.http.response import _jsonable

    data: dict[str, Any] = {"a": 1}
    data["loop"] = data
    with pytest.raises(ValueError, match="[Cc]ircular"):
        _jsonable(data)


# ---------------------------------------------------------------------------
# Serialization — type hints resolved once per handler
# ---------------------------------------------------------------------------


class _HintedOut(BaseModel):
    n: int


async def hinted_handler(request: Request) -> _HintedOut:
    return _HintedOut(n=1)


def test_validated_payload_caches_type_hints_per_handler(monkeypatch):
    calls = {"n": 0}
    original = serialization.get_type_hints

    def counting(handler):
        calls["n"] += 1
        return original(handler)

    monkeypatch.setattr(serialization, "get_type_hints", counting)

    serialization.validated_payload(hinted_handler, {"n": 1})
    serialization.validated_payload(hinted_handler, {"n": 2})
    assert calls["n"] == 1
    assert serialization.validated_payload(hinted_handler, {"n": 3}).n == 3


# ---------------------------------------------------------------------------
# render — payload escaping + Vary
# ---------------------------------------------------------------------------


def test_render_escapes_payload_in_html_shell():
    from starlette.requests import Request as SRequest

    from fastplace.http.request import Request as FpRequest

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    resp = render(
        FpRequest(SRequest(scope)),
        component="Dashboard/Index",
        props={"evil": "</script><script>alert(1)</script>"},
    )
    assert "&lt;/script&gt;" in resp.body.decode()
    assert "</script><script>alert(1)" not in resp.body.decode()


def test_render_sets_vary_on_html_response():
    from starlette.requests import Request as SRequest

    from fastplace.http.request import Request as FpRequest

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    resp = render(FpRequest(SRequest(scope)), component="A/B", props={})
    assert resp.headers["vary"] == "X-Fastplace-Request"


# ---------------------------------------------------------------------------
# Assets — manifest caching + branch coverage
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    build = tmp_path / "public" / "build" / ".vite"
    build.mkdir(parents=True, exist_ok=True)
    path = build / "manifest.json"
    path.write_text(jsonlib.dumps(manifest))
    return path


def test_manifest_parsed_once_across_requests(tmp_path, monkeypatch):
    import fastplace.http.assets as assets

    _write_manifest(
        tmp_path,
        {"resources/js/main.jsx": {"file": "assets/main-abc123.js"}},
    )
    calls = {"n": 0}
    original = jsonlib.loads

    def counting(s):
        calls["n"] += 1
        return original(s)

    monkeypatch.setattr(
        assets, "json", SimpleNamespace(loads=counting, JSONDecodeError=jsonlib.JSONDecodeError)
    )

    first = assets.asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    second = assets.asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    assert first == second
    assert calls["n"] == 1


def test_manifest_cache_invalidated_on_mtime_change(tmp_path):
    import os

    import fastplace.http.assets as assets

    path = _write_manifest(tmp_path, {"resources/js/main.jsx": {"file": "assets/main-a.js"}})
    assert "assets/main-a.js" in assets.asset_tags(
        tmp_path, vite_dev_url=None, app_env="production"
    )

    _write_manifest(tmp_path, {"resources/js/main.jsx": {"file": "assets/main-b.js"}})
    os.utime(path, ns=(0, 0))  # force a different mtime_ns
    assert "assets/main-b.js" in assets.asset_tags(
        tmp_path, vite_dev_url=None, app_env="production"
    )


def test_invalid_manifest_returns_comment(tmp_path):
    build = tmp_path / "public" / "build" / ".vite"
    build.mkdir(parents=True)
    (build / "manifest.json").write_text("{not json")
    tags = __import__("fastplace.http.assets", fromlist=["asset_tags"]).asset_tags(
        tmp_path, vite_dev_url=None, app_env="production"
    )
    assert tags == "<!-- fastplace: invalid build manifest -->"


def test_entry_missing_from_manifest_returns_comment(tmp_path):
    import fastplace.http.assets as assets

    _write_manifest(tmp_path, {"some/other.js": {"file": "assets/other.js"}})
    tags = assets.asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    assert tags == "<!-- fastplace: entry resources/js/main.jsx missing from manifest -->"


def test_imported_chunk_css_and_scripts_emitted(tmp_path):
    import fastplace.http.assets as assets

    _write_manifest(
        tmp_path,
        {
            "resources/js/main.jsx": {
                "file": "assets/main-abc.js",
                "imports": ["_vendor.js"],
            },
            "_vendor.js": {"file": "assets/vendor-xyz.js", "css": ["assets/vendor-xyz.css"]},
        },
    )
    tags = assets.asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    assert '<link rel="stylesheet" href="/build/assets/vendor-xyz.css">' in tags
    assert '<script type="module" src="/build/assets/vendor-xyz.js"></script>' in tags
    assert '<script type="module" src="/build/assets/main-abc.js"></script>' in tags


# ---------------------------------------------------------------------------
# Serialization — Response passthrough under a return annotation
# ---------------------------------------------------------------------------


async def test_validated_payload_passes_responses_through_untouched():
    """A controller that builds its own response (status codes, headers)
    declares the contract by annotation; the Response itself is final."""
    from fastplace.http import Json

    response = Json({"n": 1}, status_code=201)
    assert serialization.validated_payload(hinted_handler, response) is response


async def test_annotated_controller_can_wrap_its_payload_in_json():
    """The dogfood pattern: `-> ProjectResource` + Json(dto.model_dump(), 201)
    must not explode in outbound validation."""
    r = Router()

    class TypedController(Controller):
        async def store(self, request: Request) -> _HintedOut:
            return Json(_HintedOut(n=7).model_dump(mode="json"), status_code=201)

    r.post("/typed", TypedController, "store")
    app = get_app(api_routes=r)
    async with _client(app) as c:
        resp = await c.post("/api/v1/typed", json={})
    assert resp.status_code == 201
    assert resp.json() == {"n": 7}
