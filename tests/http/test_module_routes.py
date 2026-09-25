"""Module route tables — create_app merges app/modules/*/routes.py at boot.

Each test boots a fresh interpreter against a temp project (the
test_scaffolded_project_boots pattern) so real imports never leak between
tests or into the repo's own app package.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BOOT = (
    "from fastplace.http import create_app\n"
    "from starlette.testclient import TestClient\n"
    "client = TestClient(create_app())\n"
    "response = client.get({path!r})\n"
    "print('STATUS', response.status_code, response.text)\n"
)

_API_ROUTES = (
    "from app.modules.billing.http.controllers.billing_controller import BillingController\n"
    "from fastplace.http import Router\n"
    "\n"
    "web_routes = None\n"
    "api_routes = Router()\n"
    'api_routes.get("/orders", BillingController, "index", name="api.orders.index")\n'
)

_WEB_ROUTES = (
    "from app.modules.billing.http.controllers.billing_controller import BillingController\n"
    "from fastplace.http import Router\n"
    "\n"
    "web_routes = Router()\n"
    'web_routes.get("/billing", BillingController, "index", name="billing.index")\n'
    "api_routes = None\n"
)

_NONE_ROUTES = "from fastplace.http import Router\n\nweb_routes = None\napi_routes = None\n"


def _project(tmp_path: Path, module_routes: str | None) -> Path:
    root = tmp_path / "proj"
    mod = root / "app" / "modules" / "billing"
    (mod / "http" / "controllers").mkdir(parents=True)
    for marker in (
        root / "app" / "__init__.py",
        root / "app" / "modules" / "__init__.py",
        mod / "__init__.py",
        mod / "http" / "__init__.py",
        mod / "http" / "controllers" / "__init__.py",
    ):
        marker.write_text("")
    (mod / "http" / "controllers" / "billing_controller.py").write_text(
        "from fastplace.http import Controller, Json, Request\n"
        "\n"
        "\n"
        "class BillingController(Controller):\n"
        "    async def index(self, request: Request):\n"
        '        return Json({"items": []})\n'
    )
    if module_routes is not None:
        (mod / "routes.py").write_text(module_routes)
    return root


def _boot(root: Path, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BOOT.format(path=path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_module_api_routes_serve_under_api_v1(tmp_path):
    root = _project(tmp_path, _API_ROUTES)

    proc = _boot(root, "/api/v1/orders")

    assert proc.returncode == 0, proc.stderr
    assert "STATUS 200" in proc.stdout
    assert "items" in proc.stdout


def test_module_web_routes_serve_at_root(tmp_path):
    root = _project(tmp_path, _WEB_ROUTES)

    proc = _boot(root, "/billing")

    assert proc.returncode == 0, proc.stderr
    assert "STATUS 200" in proc.stdout


def test_module_without_routes_boots(tmp_path):
    root = _project(tmp_path, module_routes=None)

    proc = _boot(root, "/missing")

    assert proc.returncode == 0, proc.stderr
    assert "STATUS 404" in proc.stdout


def test_module_with_none_routes_boots(tmp_path):
    root = _project(tmp_path, _NONE_ROUTES)

    proc = _boot(root, "/missing")

    assert proc.returncode == 0, proc.stderr
    assert "STATUS 404" in proc.stdout


def test_broken_module_routes_fail_boot(tmp_path):
    root = _project(tmp_path, "raise RuntimeError('broken module routes')\n")

    proc = _boot(root, "/")

    assert proc.returncode != 0
    assert "broken module routes" in proc.stderr


def test_central_routes_win_conflicts(tmp_path):
    root = _project(tmp_path, _API_ROUTES)
    routes = root / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from fastplace.http import Request, Router\n"
        "\n"
        "router = Router()\n"
        "\n"
        "\n"
        "async def central_orders(request: Request):\n"
        '    return {"central": True}\n'
        "\n"
        "\n"
        'router.get("/orders", central_orders)\n'
    )

    proc = _boot(root, "/api/v1/orders")

    assert proc.returncode == 0, proc.stderr
    assert "central" in proc.stdout
