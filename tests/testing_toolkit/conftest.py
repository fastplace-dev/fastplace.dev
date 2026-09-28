"""Suite bootstrap for the ``fastplace.testing`` toolkit tests.

In-process tests (response assertions) share this package; everything that
boots real framework state runs through :mod:`pytester` in a subprocess —
importing the plugin's fixtures in-process would swap the repo suite's own
manager, metadata, and module cache.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

#: pyproject-driven pytest is the repo default; these tests need pytester's
#: own ini per project, plus asyncio auto mode for inner async tests.
_MINI_INI_HEAD = "[pytest]\nasyncio_mode = auto\n"

_MODELS_PY = '''"""Minimal model for fixture tests."""

from fastplace.orm import Field, Model


class Widget(Model):
    __tablename__ = "widgets_testing"

    __fillable__ = {"name"}

    id: int = Field(primary_key=True)
    name: str = Field(default="")
'''

_ROUTES_WEB_PY = '''"""Minimal web router — one JSON endpoint, no templates."""

from fastplace.http import Router


async def health(request):
    return {"ok": True}


router = Router()
router.get("/health", health)
'''


@pytest.fixture
def set_fastplace_ini(pytester: pytest.Pytester):
    """Write the inner project's pytest.ini; ``None`` = plugin default (auto)."""

    def _set(value: str | None) -> None:
        text = _MINI_INI_HEAD
        if value is not None:
            text += f"fastplace_test_database = {value}\n"
        pytester.makeini(text)

    return _set


@pytest.fixture
def mini_app(pytester: pytest.Pytester):
    """Lay down a minimal Fastplace project (one model, one route) in pytester."""
    (pytester.path / "app").mkdir(exist_ok=True)
    (pytester.path / "app" / "__init__.py").write_text("")
    (pytester.path / "app" / "models.py").write_text(_MODELS_PY)
    (pytester.path / "routes").mkdir(exist_ok=True)
    (pytester.path / "routes" / "__init__.py").write_text("")
    (pytester.path / "routes" / "web.py").write_text(_ROUTES_WEB_PY)
