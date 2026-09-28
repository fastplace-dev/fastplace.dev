"""Local fixtures — fresh sqlite db and fresh channel registry per test.

The database channel is Core-only over the default engine (the cache-table
pattern), so every test points DATABASE_URL at its own sqlite file; the
registry reset also rebuilds the built-ins, dropping any stale store state
from a previous engine binding.
"""

from __future__ import annotations

import pytest

from fastplace.db import reset_db
from fastplace.notifications import reset_channels


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/notifications.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    reset_db()
    yield
    reset_db()


@pytest.fixture(autouse=True)
def _fresh_channels():
    reset_channels()
    yield
    reset_channels()
