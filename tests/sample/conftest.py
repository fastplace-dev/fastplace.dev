"""Sample app test fixtures — fresh registry, fresh database, per test.

The sample-app modules under ``app/`` register real ORM models on the shared
``Model.metadata``; other suites (``tests/orm``) clear that registry after
their own tests. Purging ``app.*`` from ``sys.modules`` before and after
every test keeps imports here fresh regardless of suite ordering.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def purge_app_modules() -> None:
    import sqlalchemy

    from fastplace.orm.model import Model

    for name in [
        m
        for m in list(sys.modules)
        if m == "app" or m.startswith(("app.", "routes.", "_fastplace_seeder_"))
    ]:
        del sys.modules[name]
    Model.metadata.clear()
    sqlalchemy.orm.clear_mappers()
    from fastplace.ai import reset_tool_registry
    from fastplace.db import reset_db

    reset_db()
    # app.* re-imports re-run @Tool decorators — without this clear the
    # fresh module objects collide with their own prior registrations.
    reset_tool_registry()


@pytest.fixture(autouse=True)
def _fresh_app_modules():
    purge_app_modules()
    yield
    purge_app_modules()


@pytest.fixture()
async def sample_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The full sample app (all three routers) over a fresh database.

    Async on purpose: tables are created on the same event loop the test
    and its HTTP client run on (aiosqlite connections are loop-bound).
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sample.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")

    from app.modules.accounts.models.personal_access_token import PersonalAccessToken  # noqa: F401
    from app.modules.accounts.models.user import User  # noqa: F401
    from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
    from app.modules.projects.models.project import Project  # noqa: F401
    from app.modules.projects.models.task import Task  # noqa: F401
    from fastplace.db import db
    from fastplace.http import get_app
    from fastplace.http.kernel import _middleware_from_config
    from routes.ai import router as ai_router
    from routes.api import router as api_router
    from routes.auth import router as auth_router
    from routes.web import router as web_router

    await db.create_all()
    # The same config-driven stack create_app installs — config/app.py
    # MIDDLEWARE (app-owned entries included), outermost first.
    middleware = _middleware_from_config(Path(_PROJECT_ROOT))
    return get_app(
        routes=web_router,
        auth_routes=auth_router,
        api_routes=api_router,
        ai_routes=ai_router,
        middleware=middleware,
        config={"APP_ENV": "local", "APP_DEBUG": True},
    )


@pytest.fixture()
async def sample_client(sample_app):
    import httpx

    transport = httpx.ASGITransport(app=sample_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # The app stack now includes CSRF (config/app.py MIDDLEWARE). Play
        # the browser's part: first page load mints the session token, and
        # every unsafe-method request carries it back. The middleware's own
        # acceptance/rejection matrix lives in tests/auth/test_csrf.py.
        page = await client.get("/", headers={"X-Fastplace-Request": "true"})
        token = page.headers.get("X-Fastplace-CSRF-Token")

        async def attach_csrf(request: httpx.Request) -> None:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and token:
                request.headers.setdefault("X-Fastplace-CSRF-Token", token)

        client.event_hooks["request"].append(attach_csrf)
        yield client


@pytest.fixture()
async def sample_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A fresh sqlite database with every sample-app table created."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/sample.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    # Import after the env is set so models register on the live registry.
    from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
    from app.modules.projects.models.project import Project  # noqa: F401
    from app.modules.projects.models.task import Task  # noqa: F401
    from fastplace.db import db

    await db.create_all()
    return db


@pytest.fixture()
def embedding_seam(monkeypatch: pytest.MonkeyPatch):
    """Deterministic embeddings without a provider call.

    The vector matches ``KnowledgeItem.EMBEDDING_DIMENSIONS`` — ingest
    validates the width it is about to persist.
    """
    import fastplace.ai.embeddings as embeddings

    calls: list[dict] = []

    async def fake(*, model: str, input: list[str]) -> list[list[float]]:
        calls.append({"model": model, "input": list(input)})
        return [[0.001] * 1536 for _ in input]

    monkeypatch.setattr(embeddings, "_embedding_fn", fake)
    return calls
