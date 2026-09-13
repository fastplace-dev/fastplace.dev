"""Fastplace Request — the only request type application code touches.

Wraps the Starlette request underneath; application code never imports
``starlette``/``fastapi`` request objects directly (ADR-006).
"""

from __future__ import annotations

from typing import Any


class Request:
    """Framework request wrapper handed to every controller."""

    def __init__(self, starlette_request: Any) -> None:
        self._r = starlette_request
        self._user: Any = None

    # -- underlying access (escape hatch) ---------------------------------
    @property
    def starlette(self) -> Any:
        """Escape hatch to the underlying Starlette request."""
        return self._r

    # -- basics ------------------------------------------------------------
    @property
    def method(self) -> str:
        return self._r.method

    @property
    def url(self) -> str:
        return str(self._r.url)

    @property
    def path(self) -> str:
        return self._r.url.path

    @property
    def full_path(self) -> str:
        """Path plus query string, e.g. ``/items?page=2``."""
        query = self._r.url.query
        return self._r.url.path + (f"?{query}" if query else "")

    @property
    def headers(self) -> Any:
        return self._r.headers

    def header(self, name: str, default: str | None = None) -> str | None:
        return self._r.headers.get(name, default)

    @property
    def query_params(self) -> Any:
        return self._r.query_params

    def query(self, name: str, default: str | None = None) -> str | None:
        return self._r.query_params.get(name, default)

    @property
    def path_params(self) -> dict:
        return self._r.path_params

    def param(self, name: str, default: Any = None) -> Any:
        """Route parameter first, then query string."""
        if name in self._r.path_params:
            return self._r.path_params[name]
        return self._r.query_params.get(name, default)

    @property
    def cookies(self) -> dict:
        return self._r.cookies

    def cookie(self, name: str, default: str | None = None) -> str | None:
        return self._r.cookies.get(name, default)

    @property
    def ip(self) -> str | None:
        client = self._r.client
        return client.host if client else None

    @property
    def session(self) -> dict:
        """Signed-cookie session (requires SessionMiddleware)."""
        return self._r.session

    # -- body ---------------------------------------------------------------
    async def body(self) -> bytes:
        return await self._r.body()

    async def json(self) -> Any:
        return await self._r.json()

    async def form(self) -> Any:
        return await self._r.form()

    # -- authentication -------------------------------------------------------
    @property
    def user(self) -> Any:
        """The authenticated user, resolved by the auth middleware."""
        return self._user

    def set_user(self, user: Any) -> None:
        self._user = user

    @property
    def is_authenticated(self) -> bool:
        return self._user is not None

    # -- bridge protocol --------------------------------------------------------
    @property
    def is_bridge(self) -> bool:
        """True when the SPA asks for a JSON page payload (X-Fastplace-Request)."""
        return (self._r.headers.get("X-Fastplace-Request") or "").lower() == "true"
