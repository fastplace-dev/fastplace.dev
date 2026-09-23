"""Credential login/logout — the service layer over the session guard."""

from __future__ import annotations

from typing import Any

from fastplace.auth.guards import guard
from fastplace.errors import ValidationError


class AuthService:
    """Controllers never touch guards directly; they come through here."""

    async def login(self, request: Any, data: dict[str, Any]) -> None:
        credentials = {
            "email": str(data.get("email") or "").strip().lower(),
            "password": str(data.get("password") or ""),
        }
        remember = bool(data.get("remember"))

        ok = await guard().attempt(request, credentials, remember=remember)
        if not ok:
            # The frozen frontend contract: failed credentials are a 422 with
            # the message under errors.email — never a 401.
            raise ValidationError(errors={"email": ["These credentials do not match our records."]})

    async def logout(self, request: Any) -> None:
        await guard().logout(request)
