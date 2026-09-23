"""Credential login/logout — the service layer over the session guard."""

from __future__ import annotations

import time
from typing import Any

from fastplace.auth.guards import guard
from fastplace.errors import ValidationError


class AuthService:
    """Controllers never touch guards directly; they come through here."""

    async def login(self, request: Any, data: dict[str, Any]) -> bool:
        """Attempt login. True = completed; False = 2FA challenge parked."""
        credentials = {
            "email": str(data.get("email") or "").strip().lower(),
            "password": str(data.get("password") or ""),
        }
        remember = bool(data.get("remember"))

        ok = await guard().attempt(request, credentials, remember=remember)
        if not ok and not guard().pending_two_factor(request):
            # The frozen frontend contract: failed credentials are a 422 with
            # the message under errors.email — never a 401.
            raise ValidationError(errors={"email": ["These credentials do not match our records."]})
        return ok

    async def logout(self, request: Any) -> None:
        await guard().logout(request)

    async def confirm_password(self, request: Any, password: str) -> None:
        """Verify the current password; stamp the confirmation window open.

        The password.confirm route middleware reads the stamp this writes
        (``password_confirmed_at``, int UNIX seconds) and enforces the
        PASSWORD_TIMEOUT window around the sensitive pages.
        """
        if not password.strip():
            # R12's frozen contract string — the frontend mock pins it, so
            # the service raises it (no translation layer exists).
            raise ValidationError(errors={"password": ["The password field is required."]})
        user = getattr(request, "user", None)
        if user is None or not await guard().provider.validate_credentials(
            user, {"password": password}
        ):
            raise ValidationError(errors={"password": ["The password is incorrect."]})
        request.session["password_confirmed_at"] = int(time.time())
