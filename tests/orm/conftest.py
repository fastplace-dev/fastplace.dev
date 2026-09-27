"""Shared ORM test fixtures — in-memory SQLite per test."""

from __future__ import annotations

import pytest


@pytest.fixture()
def db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = "sqlite+aiosqlite:///:memory:"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    return url


@pytest.fixture(autouse=True)
def reset_db_singleton():
    """Isolate the db facade between tests."""
    from fastplace.db import reset_db

    reset_db()
    yield
    reset_db()


@pytest.fixture(autouse=True)
def reset_model_registry():
    """Unmap every model class around each test; the registry starts empty.

    ORM tests declare domain-shaped models under generic names (``User``,
    ``Post``, ``Comment``) that collide with models other suites mapped
    earlier in the same process — and with each other across files. A bare
    string like ``relationship("Comment")`` then resolves across the
    collisions, and one failed configure leaves a poisoned mapper that
    breaks every later ``configure_mappers()`` — the damage surfaces suites
    away. So each test runs against a registry holding only its own
    declarations: everything mapped is disposed at setup and at teardown
    via the same per-class teardown the base performs on redeclaration —
    unlike ``clear_mappers()``, which strands disposed managers and breaks
    two suites later (see ``tests/_registry.py``).

    The test's own tables are swept at teardown, and every mapped class's
    Table goes with its disposal — a Table left behind without its class can
    no longer be attributed to a project and leaks into the next suite's
    autogenerate. Tables nothing maps (framework bookkeeping) stay on the
    shared metadata. Redeclared tablenames replace
    cleanly at the base, so re-imported project modules need no registry
    reset. Cached project modules (app.*, seeder/config loaders from tmp
    projects) are dropped too — each test imports from its own project
    tree.

    sys.path is restored as well: the CLI's model discovery inserts the
    (tmp) project root at import time and never removes it — fine for a
    real process that exits, a leak in this long-lived pytest process.
    Left on, a later suite's fresh ``import app`` (the entries above were
    dropped) resolves the LAST tmp project instead of the repo and fails
    with ``ModuleNotFoundError: No module named 'app.modules.knowledge'``.
    """
    import sys

    from tests._registry import dispose_all_models, metadata_baseline, sweep_added_tables

    dispose_all_models()
    baseline = metadata_baseline()
    saved_path = list(sys.path)
    yield
    dispose_all_models()
    sweep_added_tables(baseline)
    for name in [
        m
        for m in list(sys.modules)
        if m == "app" or m.startswith(("app.", "_fastplace_seeder_", "_fastplace_config_"))
    ]:
        del sys.modules[name]
    sys.path[:] = saved_path
