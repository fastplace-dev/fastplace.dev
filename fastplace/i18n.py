"""i18n — JSON lang files, translation helpers, request-scoped locale.

Translations live in ``lang/<locale>.json`` under the project root, keyed in
dot notation (``messages.welcome``). The active locale is a contextvar, so
each request/task scopes its own language and concurrent scopes never bleed;
:class:`LocaleMiddleware` resolves it per request from the ``?locale=`` query
parameter, then the ``Accept-Language`` header, then the ``LOCALE`` default.
Background work calls :func:`set_locale` explicitly before translating.
"""

from __future__ import annotations

import contextvars
import json
from pathlib import Path
from typing import Any

from fastplace.config import config

#: App-wide default — also the fallback locale for missing keys.
DEFAULT_LOCALE = "en"

#: Plural cardinality rules for the named-forms dict (exact matches first,
#: ``other`` catches everything else). Deliberately not full CLDR — the
#: framework's built-in languages are cardinal-simple.
_FORM_RULES: dict[str, int] = {"zero": 0, "one": 1, "two": 2}

_locale: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fastplace.locale", default=DEFAULT_LOCALE
)

_lang_cache: dict[str, dict[str, Any]] = {}

_LANG_DIR = Path("lang")


def get_locale() -> str:
    """The active locale for this request/task (``LOCALE`` default)."""
    return _locale.get()


def set_locale(locale: str) -> None:
    """Scope this request/task to ``locale`` (revert is a new task/request)."""
    _locale.set(locale)


def reset() -> None:
    """Drop cached lang files — test isolation and dev hot-reload."""
    _lang_cache.clear()
    _locale.set(DEFAULT_LOCALE)


def supported_locales() -> list[str]:
    """Locales the app serves — ``LOCALES`` (comma-separated) or ``[LOCALE]``."""
    raw = str(config("LOCALES", default="") or "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(config("LOCALE", default=DEFAULT_LOCALE) or DEFAULT_LOCALE)]


def default_locale() -> str:
    return str(config("LOCALE", default=DEFAULT_LOCALE) or DEFAULT_LOCALE)


def _lang(locale: str) -> dict[str, Any]:
    """Parsed ``lang/<locale>.json``, cached per process (deploy-time files)."""
    if locale not in _lang_cache:
        path = _LANG_DIR / f"{locale}.json"
        try:
            _lang_cache[locale] = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _lang_cache[locale] = {}
    return _lang_cache[locale]


def _lookup(messages: dict[str, Any], key: str) -> Any:
    node: Any = messages
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _resolve_key(key: str) -> Any:
    """Search the active locale, then the default — None when nowhere."""
    active = get_locale()
    chain = [active] if active == default_locale() else [active, default_locale()]
    for locale in chain:
        found = _lookup(_lang(locale), key)
        if found is not None:
            return found
    return None


def _replace(text: str, replacements: dict[str, Any]) -> str:
    for name, value in replacements.items():
        text = text.replace(f":{name}", str(value))
    return text


def trans(key: str, *, default: str | None = None, **replacements: Any) -> str:
    """Translate ``key`` in the active locale; ``:name`` placeholders fill in.

    Missing everywhere → ``default``, else the key itself (visible, loud).
    """
    found = _resolve_key(key)
    if found is None:
        return _replace(default, replacements) if default is not None else key
    return _replace(str(found), replacements)


def trans_choice(key: str, count: int, *, default: str | None = None, **replacements: Any) -> str:
    """Translate with pluralization — pipe forms (``one apple|:count apples``)
    or named forms (``{"zero": ..., "one": ..., "other": ...}``).

    ``:count`` substitutes in every shape; extra replacements pass through.
    """
    replacements = {"count": count, **replacements}
    found = _resolve_key(key)
    if found is None:
        return _replace(default, replacements) if default is not None else key
    if isinstance(found, dict):
        for form, exact in _FORM_RULES.items():
            if count == exact and form in found:
                chosen = found[form]
                break
        else:
            chosen = found.get("other", found.get("one", ""))
        return _replace(str(chosen), replacements)
    parts = str(found).split("|")
    chosen = parts[0] if count == 1 else parts[-1]
    return _replace(chosen, replacements)


def negotiate(requested: str | None, header: str | None) -> str:
    """Pick the request locale: explicit value, then Accept-Language, then
    the default — anything unsupported falls back, never 4xx."""
    supported = supported_locales()
    candidates: list[str] = []
    if requested:
        candidates.append(requested.strip())
    if header:
        for part in header.split(","):
            tag = part.split(";")[0].strip()
            if tag:
                candidates.append(tag)
    for candidate in candidates:
        if candidate in supported:
            return candidate
    return supported[0]


class LocaleMiddleware:
    """Pure-ASGI: resolve the request locale into the contextvar.

    Priority: ``?locale=`` query → ``Accept-Language`` header → default.
    Installed by the kernel on every app, so ``trans()`` inside handlers,
    error rendering, and jobs spawned from the request all agree.
    """

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        query = scope.get("query_string", b"").decode("latin-1")
        requested: str | None = None
        for pair in query.split("&"):
            name, _, value = pair.partition("=")
            if name == "locale" and value:
                from urllib.parse import unquote

                requested = unquote(value)
                break
        header = ""
        for name, value in scope.get("headers") or []:
            if name == b"accept-language":
                header = value.decode("latin-1")
                break
        token = _locale.set(negotiate(requested, header or None))
        try:
            await self.app(scope, receive, send)
        finally:
            _locale.reset(token)

    def __init__(self, app: Any) -> None:
        self.app = app
