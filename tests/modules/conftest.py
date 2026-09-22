"""Accounts-module fixtures — fresh app.* registry per test (sample-suite pattern)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def purge_app_modules() -> None:
    """Drop app.* from sys.modules so each test re-imports fresh state."""
    import sqlalchemy

    from fastplace.db import reset_db
    from fastplace.orm.model import Model

    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    # Re-imported modules re-declare their tables on the shared Model.metadata —
    # without this clear the second test collides ("users" already defined).
    Model.metadata.clear()
    sqlalchemy.orm.clear_mappers()
    # Fresh engine/manager so the per-test DATABASE_URL is honored.
    reset_db()


@pytest.fixture(autouse=True)
def _fresh_app_modules():
    purge_app_modules()
    yield
    purge_app_modules()
