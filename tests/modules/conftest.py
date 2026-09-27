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
    from fastplace.db import reset_db

    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]
    # Fresh engine/manager so the per-test DATABASE_URL is honored.
    reset_db()


@pytest.fixture(autouse=True)
def _fresh_app_modules():
    from tests._registry import metadata_baseline, sweep_added_tables

    purge_app_modules()
    # Re-imported modules re-declare their tables on the shared
    # Model.metadata — redeclarations replace cleanly at the base, and only
    # this test's additions are swept afterwards. A global
    # metadata.clear()/clear_mappers() here would strand every model module
    # another suite already imported (see tests/_registry.py).
    baseline = metadata_baseline()
    yield
    purge_app_modules()
    sweep_added_tables(baseline)
