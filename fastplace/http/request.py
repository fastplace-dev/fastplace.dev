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

    # -- underlying access (escape hatch) ---------------------------------
    @property
    def starlette(self) -> Any:
        """Escape hatch to the underlying Starlette request."""
        return self._r

    @property
    def scope(self) -> dict:
        """The ASGI scope — shared per-request state across middleware layers."""
        return self._r.scope

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
        """Server-side session (requires ServerSessionMiddleware)."""
        return self._r.session

    # -- body ---------------------------------------------------------------
    async def body(self) -> bytes:
        return await self._r.body()

    async def json(self) -> Any:
        return await self._r.json()

    async def form(self) -> Any:
        return await self._r.form()

    async def validate(self, schema: type[Any]) -> Any:
        """Validate the request body against a Pydantic schema at the HTTP edge.

        JSON bodies and native (no-JS) form posts both flow through the same
        schema. Invalid input raises the framework's 422 contract (``message``
        + per-field ``errors``) instead of leaking a raw Pydantic error; an
        unparseable body is reported under the ``body`` key so a client fault
        never surfaces as a server 500.
        """
        import json as json_module

        from pydantic import ValidationError as PydanticValidationError

        from fastplace.errors import ValidationError

        content_type = (self.header("Content-Type") or "").lower()
        if "form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await self.form()
            payload: Any = {key: form.get(key) for key in form.keys()}
        else:
            raw = await self.body()
            try:
                payload = json_module.loads(raw) if raw else {}
            except (json_module.JSONDecodeError, UnicodeDecodeError) as exc:
                # Undecodable/truncated bodies are client faults — a parse
                # error, not misleading per-field validation noise.
                raise ValidationError(
                    "The given data was invalid.",
                    errors={"body": ["Invalid JSON body."]},
                ) from exc
        try:
            return schema.model_validate(payload)
        except PydanticValidationError as exc:
            errors: dict[str, list[str]] = {}
            for item in exc.errors():
                loc = ".".join(str(part) for part in item.get("loc", ()) or ("body",))
                errors.setdefault(loc or "body", []).append(str(item.get("msg", "invalid")))
            raise ValidationError("The given data was invalid.", errors=errors) from exc

    # -- authentication -------------------------------------------------------
    @property
    def user(self) -> Any:
        """The authenticated user, resolved by the auth middleware.

        Stored on the ASGI scope (shared for the whole request lifetime) so
        every ``Request`` wrapper — middleware's and controller's — sees the
        same identity even across BaseHTTP middleware boundaries.
        """
        return self._r.scope.get("fastplace_user")

    def set_user(self, user: Any) -> None:
        self._r.scope["fastplace_user"] = user

    @property
    def is_authenticated(self) -> bool:
        return self._r.scope.get("fastplace_user") is not None

    # -- auth facade helpers (spec §4.16) ---------------------------------
    @property
    def auth_id(self) -> Any:
        """The authenticated user's identifier (provider semantics), or None."""
        user = self.user
        if user is None:
            return None
        from fastplace.auth.providers import user_identifier

        return user_identifier(user)

    @property
    def check(self) -> bool:
        """Auth-facade parity: True when a user is authenticated."""
        return self.is_authenticated

    @property
    def guest(self) -> bool:
        """True when no user is authenticated."""
        return not self.is_authenticated

    # -- bridge protocol --------------------------------------------------------
    @property
    def is_bridge(self) -> bool:
        """True when the SPA asks for a JSON page payload (X-Fastplace-Request)."""
        return (self._r.headers.get("X-Fastplace-Request") or "").lower() == "true"
