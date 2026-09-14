"""Kernel debug payload surfaces per-request query stats (checklist, Batch 1).

Instrumentation already counts statements per request; the unhandled-exception
debug payloads (JSON and the rich HTML page) must expose them — statement
counts, slow queries, and N+1 candidates — while production stays suppressed.
"""

from __future__ import annotations

import itertools

import pytest

from fastplace.orm import Field, Model

# Fresh table name per fixture invocation: other suites' autouse metadata
# clears wipe registered tables between tests, so a shared module-level model
# would lose its mapper depending on suite ordering.
_seq = itertools.count(1)


@pytest.fixture()
async def crashing_db_app(tmp_path, monkeypatch):
    """Debug-mode app whose endpoint runs real SQL, then explodes."""
    from fastplace.db import reset_db
    from fastplace.http import Request, Router, get_app

    table = f"stat_rows_{next(_seq)}"

    class StatRow(Model):
        __tablename__ = table

        id: int = Field(primary_key=True)
        title: str = ""

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/stats.db")
    reset_db()

    from fastplace.db import db

    await db.create_all()

    async def boom(request: Request):
        await db.raw("SELECT 1")
        for index in range(3):
            await StatRow.where(StatRow.title == f"row-{index}").first()
        raise RuntimeError("kaboom")

    router = Router()
    router.get("/boom", boom)
    app = get_app(routes=router, config={"APP_DEBUG": True, "APP_ENV": "local"})

    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        yield app
    await db.dispose()
    reset_db()


@pytest.fixture()
async def production_crashing_app(tmp_path, monkeypatch):
    """Same crash under APP_ENV=production — everything stays suppressed."""
    from asgi_lifespan import LifespanManager

    from fastplace.db import reset_db
    from fastplace.http import Request, Router, get_app

    table = f"prod_rows_{next(_seq)}"

    class ProdRow(Model):
        __tablename__ = table

        id: int = Field(primary_key=True)

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/prod.db")
    reset_db()

    from fastplace.db import db

    await db.create_all()

    async def boom(request: Request):
        await db.raw("SELECT 1")
        raise RuntimeError("secret-kaboom")

    router = Router()
    router.get("/boom", boom)
    app = get_app(
        routes=router,
        config={
            "APP_DEBUG": True,
            "APP_ENV": "production",
            "APP_KEY": "test-app-key-not-for-production-use-only",
        },
    )

    async with LifespanManager(app):
        yield app
    await db.dispose()
    reset_db()


async def test_debug_json_payload_carries_query_stats(crashing_db_app):
    import httpx

    transport = httpx.ASGITransport(app=crashing_db_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom", headers={"Accept": "application/json"})

    assert resp.status_code == 500
    body = resp.json()
    queries = body["queries"]
    # One raw SELECT plus three identical ORM lookups.
    assert queries["statements"] >= 4
    # The repeated SELECT is reported as an N+1 candidate.
    assert sum(queries["duplicates"].values()) >= 2


async def test_debug_html_page_shows_query_stats(crashing_db_app):
    import httpx

    transport = httpx.ASGITransport(app=crashing_db_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/boom", headers={"Accept": "text/html"})

    assert "Query stats" in resp.text


async def test_production_payload_never_carries_query_stats(production_crashing_app):
    """Same crash in production: opaque JSON, no stats, no SQL text."""
    import httpx

    transport = httpx.ASGITransport(app=production_crashing_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom", headers={"Accept": "application/json"})

    body = resp.json()
    assert body == {"message": "Server error."}
    assert "queries" not in body


def test_stats_summary_is_a_json_safe_digest():
    from fastplace.orm.instrumentation import QueryTracker

    tracker = QueryTracker()
    for _ in range(4):
        tracker.record("SELECT * FROM rows WHERE id = ?", 0.002)
    tracker.record("SELECT 1", 0.5)

    summary = tracker.stats.summary()
    assert summary["statements"] == 5
    assert summary["slow_queries"] == 0  # threshold lives in the listener, not record()
    assert summary["total_seconds"] > 0
    # Duplicate keys are collapsed/previews so payloads stay bounded.
    assert len(summary["duplicates"]) == 1
    assert summary["duplicates"]["SELECT * FROM rows WHERE id = ?"] == 4


def test_stats_summary_merges_counts_on_preview_collision():
    """Two distinct repeated statements whose 120-char previews collide roll
    up under one preview — summed, never silently dropped."""
    from fastplace.orm.instrumentation import QueryTracker

    head = "SELECT * FROM orders WHERE " + "x" * 100  # longer than one preview
    tracker = QueryTracker()
    for _ in range(2):
        tracker.record(f"{head} AND status = 'open'", 0.001)
        tracker.record(f"{head} AND status = 'paid'", 0.001)

    summary = tracker.stats.summary()
    assert len(summary["duplicates"]) == 1
    assert summary["duplicates"][head[:120]] == 4
