"""Minimal translation helper — a stub, not a full i18n layer.

``__("auth.failed")`` resolves the line for the active locale, falling
back to the fallback locale and finally to the key itself. ``:names``
interpolate from kwargs: ``__("auth.throttled", seconds=60)``.
"""

from __future__ import annotations

import importlib
from typing import Any

from config.locale import APP_FALLBACK_LOCALE, APP_LOCALE


def _lines(locale: str) -> dict[str, str]:
    try:
        module = importlib.import_module(f"lang.{locale}.messages")
    except ModuleNotFoundError:
        return {}
    lines: Any = getattr(module, "LINES", {})
    return lines if isinstance(lines, dict) else {}


def __(key: str, **placeholders: object) -> str:
    """Translate a dotted key, then interpolate its :name placeholders."""
    line = _lines(APP_LOCALE).get(key) or _lines(APP_FALLBACK_LOCALE).get(key) or key
    for name, value in placeholders.items():
        line = line.replace(f":{name}", str(value))
    return line
