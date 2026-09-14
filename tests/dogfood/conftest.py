"""Dogfood app test fixtures — fresh registry, fresh database, per test.

The dogfood modules under ``app/`` register real ORM models on the shared
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
    from fastplace.db import reset_db

    reset_db()


@pytest.fixture(autouse=True)
def _fresh_app_modules():
    purge_app_modules()
    yield
    purge_app_modules()


@pytest.fixture()
async def dogfood_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The full dogfood app (all three routers) over a fresh database.

    Async on purpose: tables are created on the same event loop the test
    and its HTTP client run on (aiosqlite connections are loop-bound).
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/dogfood.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")

    from app.modules.knowledge.models.knowledge_item import KnowledgeItem  # noqa: F401
    from app.modules.projects.models.project import Project  # noqa: F401
    from app.modules.projects.models.task import Task  # noqa: F401
    from fastplace.db import db
    from fastplace.http import get_app
    from routes.ai import router as ai_router
    from routes.api import router as api_router
    from routes.web import router as web_router

    await db.create_all()
    return get_app(
        routes=web_router,
        api_routes=api_router,
        ai_routes=ai_router,
        config={"APP_ENV": "local", "APP_DEBUG": True},
    )


@pytest.fixture()
async def dogfood_client(dogfood_app):
    import httpx

    transport = httpx.ASGITransport(app=dogfood_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture()
async def dogfood_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A fresh sqlite database with every dogfood table created."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/dogfood.db")
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
