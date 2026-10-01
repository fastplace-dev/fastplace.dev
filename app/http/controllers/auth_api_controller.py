"""Credential endpoints — thin controllers, all logic in the services."""

from __future__ import annotations

import json

from app.http.requests.confirm_password_request import ConfirmPasswordRequest
from app.http.requests.forgot_password_request import ForgotPasswordRequest
from app.http.requests.login_request import LoginRequest
from app.http.requests.register_request import RegisterRequest
from app.http.requests.reset_password_request import ResetPasswordRequest
from app.modules.accounts.services.auth_service import AuthService
from app.modules.accounts.services.password_reset_service import PasswordResetService
from app.modules.accounts.services.registration_service import RegistrationService
from app.modules.accounts.services.two_factor_service import TwoFactorService
from app.modules.accounts.services.verification_service import VerificationService
from fastplace.http import Controller, Json, Redirect, Request, flash


class AuthApiController(Controller):
    auth_service = AuthService()
    registration_service = RegistrationService()
    password_reset_service = PasswordResetService()
    verification_service = VerificationService()
    two_factor_service = TwoFactorService()

    async def login(self, request: Request):
        data = (await request.validate(LoginRequest)).model_dump()
        completed = await self.auth_service.login(request, data)
        if not completed:
            # Valid credentials + confirmed 2FA: browsers and the bridge
            # follow the 303 onto the challenge page (the SPA swaps); a
            # JSON API client gets the two_factor flag (R7).
            accept = request.header("Accept") or ""
            if request.is_bridge or "application/json" not in accept:
                return Redirect("/two-factor-challenge", status_code=303)
            return Json({"two_factor": True})
        return Redirect(request.intended(), status_code=303)

    async def register(self, request: Request):
        data = (await request.validate(RegisterRequest)).model_dump()
        await self.registration_service.register(request, data)
        return Redirect(request.intended(), status_code=303)

    async def logout(self, request: Request):
        await self.auth_service.logout(request)
        return Redirect("/login", status_code=303)

    async def confirm_password(self, request: Request):
        data = (await request.validate(ConfirmPasswordRequest)).model_dump()
        await self.auth_service.confirm_password(request, data["password"])
        return Redirect(request.intended(), status_code=303)

    async def two_factor_challenge(self, request: Request):
        # The frozen page posts {code} or {recovery_code} with no other
        # fields — a raw JSON read (not a Form request class) matches the
        # XOR shape; the 422 is service-raised with the frozen string.
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Undecodable body ≙ nothing submitted (validate()'s decode
            # precedent) — the frozen 422 below, never a 500.
            body = {}
        if not isinstance(body, dict):
            body = {}  # valid JSON of another shape carries no fields either
        code = str(body.get("code") or "").strip()
        recovery_code = str(body.get("recovery_code") or "").strip()
        outcome = await self.two_factor_service.verify_challenge(
            request, code=code, recovery_code=recovery_code
        )
        if outcome is None:
            return Redirect("/login", status_code=303)
        # A failed attempt raised the frozen 422 inside the service, keyed
        # by the submitted field — the controller only sees success here.
        return Redirect(request.intended(), status_code=303)

    async def forgot_password(self, request: Request):
        data = (await request.validate(ForgotPasswordRequest)).model_dump()
        message = await self.password_reset_service.send_reset_link(data["email"])
        flash(request, message)
        return Redirect("/forgot-password", status_code=303)

    async def reset_password(self, request: Request):
        data = (await request.validate(ResetPasswordRequest)).model_dump()
        await self.password_reset_service.reset(request, data)
        flash(request, PasswordResetService.RESET_MESSAGE)
        return Redirect("/login", status_code=303)

    async def verify_email(self, request: Request):
        await self.verification_service.fulfill(
            request, request.param("id"), request.param("hash"), request.query("expires", "")
        )
        return Redirect(request.intended(), status_code=303)

    async def verification_notification(self, request: Request):
        await self.verification_service.resend(request)
        flash(
            request, "verification-link-sent"
        )  # BYTE-EXACT — VerifyEmail renders on exact equality
        return Redirect("/email/verify", status_code=303)
