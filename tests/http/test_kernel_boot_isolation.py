"""Boot isolation — one process, two projects, no router leakage.

``importlib.import_module`` reuses ``sys.modules``, so a ``routes.auth``
imported for project A must never surface in project B's boot — and the
reverse order (B's empty ``routes`` package cached first) must not hide A's
real auth file behind a stale parent-package path. Assertions ride
``url_path_for`` — the framework's own route resolution, which sees through
FastAPI's included-router wrappers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from starlette.routing import NoMatchFound

ROUTE_MODULES = (
    "routes",
    "routes.web",
    "routes.auth",
    "routes.api",
    "routes.ai",
)


def _boot_module_names() -> set[str]:
    """Module namespaces one boot may cache: the routes surface plus the
    project's own ``app`` package tree (module routes, gates)."""
    return {
        name
        for name in sys.modules
        if name in ROUTE_MODULES or name == "app" or name.startswith("app.")
    }


@pytest.fixture()
def boot_isolation(tmp_path: Path):
    """create_app boots leak by design: .env keys join os.environ, project
    modules join sys.modules, the project root joins sys.path, and the
    kernel rebinds the default config registry to the project root. Snapshot
    and restore all four so later suites see the process as it was."""
    import fastplace.config as config_module

    env_before = set(os.environ)
    path_before = list(sys.path)
    default_config_before = config_module._default_config
    modules_before = {name: sys.modules[name] for name in _boot_module_names()}
    yield
    for key in set(os.environ) - env_before:
        os.environ.pop(key, None)
    sys.path[:] = path_before
    config_module._default_config = default_config_before
    for name in _boot_module_names():
        module = sys.modules.get(name)
        if module is not None and modules_before.get(name) is not module:
            file = getattr(module, "__file__", None)
            if file is None or Path(file).is_relative_to(tmp_path):
                sys.modules.pop(name, None)


def _scaffold_project(root: Path, *, with_auth: bool, module_route: str | None = None) -> None:
    (root / "config").mkdir(parents=True)
    (root / "config" / "app.py").write_text(
        'APP_ENV = "local"\nAPP_KEY = ""\nAPP_URL = "http://fastplace.local"\n'
    )
    (root / "routes").mkdir()
    (root / "routes" / "__init__.py").write_text("")
    (root / "routes" / "web.py").write_text(
        "from fastplace.http import Json, Router\n\n\n"
        "async def home(request):\n"
        "    return Json({'ok': True})\n\n\n"
        "router = Router()\n"
        "router.get('/', home)\n"
    )
    (root / ".env").write_text("APP_ENV=local\n")
    if with_auth:
        (root / "routes" / "auth.py").write_text(
            "from fastplace.http import Json, Router\n\n\n"
            "async def login(request):\n"
            "    return Json({'ok': True})\n\n\n"
            "router = Router()\n"
            "router.post('/login', login)\n"
        )
    if module_route is not None:
        # Same package markers the generator writes (app/__init__.py down to
        # the module's own) — regular packages, so importlib's resolution is
        # deterministic across two roots on sys.path.
        billing = root / "app" / "modules" / "billing"
        billing.mkdir(parents=True)
        for marker in (
            root / "app" / "__init__.py",
            root / "app" / "modules" / "__init__.py",
            billing / "__init__.py",
        ):
            marker.write_text("")
        (billing / "routes.py").write_text(
            "from fastplace.http import Router\n\n\n"
            "async def only(request):\n"
            "    from fastplace.http import Json\n\n"
            "    return Json({'ok': True})\n\n\n"
            "web_routes = Router()\n"
            f"web_routes.get('/{module_route}', only, name='{module_route}')\n"
            "api_routes = None\n"
        )


def test_second_project_never_inherits_the_first_projects_auth_router(
    tmp_path: Path, boot_isolation: None
):
    from fastplace.http.kernel import create_app

    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    _scaffold_project(project_a, with_auth=True)
    _scaffold_project(project_b, with_auth=False)

    app_a = create_app(project_a)
    assert app_a.url_path_for("login") == "/login"  # sanity: A ships its auth router

    app_b = create_app(project_b)
    assert app_b.url_path_for("home") == "/"  # sanity: B's own web router mounts
    with pytest.raises(NoMatchFound):
        app_b.url_path_for("login")


def test_project_with_auth_boots_after_one_without(tmp_path: Path, boot_isolation: None):
    from fastplace.http.kernel import create_app

    project_b = tmp_path / "b"
    project_a = tmp_path / "a"
    _scaffold_project(project_b, with_auth=False)
    _scaffold_project(project_a, with_auth=True)

    create_app(project_b)  # caches an auth-less routes package first
    app_a = create_app(project_a)
    assert app_a.url_path_for("login") == "/login"
    assert app_a.url_path_for("home") == "/"


def test_second_project_never_inherits_the_first_projects_module_routes(
    tmp_path: Path, boot_isolation: None
):
    from fastplace.http.kernel import create_app

    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    _scaffold_project(project_a, with_auth=False, module_route="a_only")
    _scaffold_project(project_b, with_auth=False, module_route="b_only")

    app_a = create_app(project_a)
    assert app_a.url_path_for("a_only") == "/a_only"  # sanity: A ships its module route

    app_b = create_app(project_b)
    assert app_b.url_path_for("b_only") == "/b_only"  # B's own module route mounts
    with pytest.raises(NoMatchFound):
        app_b.url_path_for("a_only")


def test_module_route_projects_never_leak_in_reverse_order(tmp_path: Path, boot_isolation: None):
    from fastplace.http.kernel import create_app

    project_b = tmp_path / "b"
    project_a = tmp_path / "a"
    _scaffold_project(project_b, with_auth=False, module_route="b_only")
    _scaffold_project(project_a, with_auth=False, module_route="a_only")

    app_b = create_app(project_b)
    assert app_b.url_path_for("b_only") == "/b_only"  # sanity: B ships its module route

    app_a = create_app(project_a)
    assert app_a.url_path_for("a_only") == "/a_only"
    with pytest.raises(NoMatchFound):
        app_a.url_path_for("b_only")


def test_boot_without_auth_cleans_the_previous_projects_cached_auth_module(
    tmp_path: Path, boot_isolation: None
):
    """An absent routes file must still evict the previous project's cached
    module under the same name — the file gate alone leaves the residue for
    whatever imports (or boots) next."""
    from fastplace.http.kernel import create_app

    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    _scaffold_project(project_a, with_auth=True)
    _scaffold_project(project_b, with_auth=False)

    create_app(project_a)
    assert "routes.auth" in sys.modules  # sanity: A's auth module is cached
    create_app(project_b)
    assert "routes.auth" not in sys.modules
