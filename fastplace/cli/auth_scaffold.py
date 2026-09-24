"""``fastplace make:auth`` — the auth surface scaffolder (spec §4.17)."""

from __future__ import annotations

import typer

from fastplace.cli.generators import _project_root, _write, console, generators_app

# Each constant below embeds the exact current contents of its source file
# (see the plan's table) — the scaffold and the reference implementation
# stay one paste apart, never two edits apart.

_USER_MODEL_TEMPLATE = '''"""User — the account entity behind every guard (spec §5)."""

from __future__ import annotations

import datetime

from fastplace.orm import Field, Model


class User(Model):
    __tablename__ = "users"

    # Never serialized into page props or JSON responses — to_dict() honors
    # __hidden__, so credential material cannot leak through render(). The
    # two-factor columns are encrypted at rest AND hidden from serialization.
    __hidden__ = {"password_hash", "two_factor_secret", "two_factor_recovery_codes"}
    __fillable__ = {"name", "email", "password_hash", "email_verified_at"}

    id: int = Field(primary_key=True)
    name: str = Field(default="")
    email: str = Field(unique=True, index=True)
    password_hash: str = Field(default="")
    email_verified_at: datetime.datetime | None = None

    # Encrypted at rest (fastplace.auth.encryption) — Text, not VARCHAR(255):
    # the sealed recovery-code JSON runs past 255 characters.
    two_factor_secret: str | None = Field(text=True, default=None)
    two_factor_recovery_codes: str | None = Field(text=True, default=None)
    two_factor_confirmed_at: datetime.datetime | None = None
'''
_USER_REPOSITORY_TEMPLATE = '''"""User data access — the accounts module's repository layer."""

from __future__ import annotations

from app.modules.accounts.models.user import User
from fastplace.auth.hashing import Hash


class UserRepository:
    """Owns every User query the auth services issue."""

    async def find_by_email(self, email: str) -> User | None:
        return await User.where(User.email == email).first()

    async def find_by_id(self, user_id) -> User | None:
        return await User.find(user_id)

    async def create_user(self, *, name: str, email: str, password: str) -> User:
        return await User.create(name=name, email=email, password_hash=Hash.make(password))
'''
_AUTH_SERVICE_TEMPLATE = '''"""Credential login/logout — the service layer over the session guard."""

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
'''
# Raw literal: the source's regex (\d+) would otherwise trip W605/SyntaxWarning.
_PASSWORD_POLICY_TEMPLATE = r'''"""Password policy — one source of truth for the PASSWORD_RULES dialects.

``config/auth.py`` speaks the server dialect (``min:8``); the ported React
pages consume the frontend dialect (``minlength: 8;``) for the HTML
``passwordrules`` attribute. Phase 2 honors only the ``min:N`` clause —
richer clauses arrive with the settings UI.
"""

from __future__ import annotations

import re

from fastplace.config import config

DEFAULT_RULES = "min:8"


def configured_rules() -> str:
    """The raw PASSWORD_RULES string (server dialect)."""
    return str(config("PASSWORD_RULES", default=DEFAULT_RULES) or DEFAULT_RULES)


def min_password_length(rules: str | None = None) -> int:
    """The ``min:N`` clause as an int (default 8 when absent or unparsable)."""
    raw = configured_rules() if rules is None else rules
    match = re.search(r"(?:^|,)min:(\d+)", raw)
    return int(match.group(1)) if match else 8


def frontend_rules() -> str:
    """PASSWORD_RULES translated to the bridge pages' dialect (spec §4.9)."""
    return f"minlength: {min_password_length()};"
'''
_PASSWORD_RESET_SERVICE_TEMPLATE = '''"""Password reset — forgot-password mail and token-gated redemption (§4.10)."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

from app.modules.accounts.repositories.user_repository import UserRepository
from app.modules.accounts.services.password_policy import min_password_length
from fastplace.auth.guards import SESSION_STORE_SCOPE
from fastplace.auth.hashing import Hash
from fastplace.auth.passwords import _dummy_digest, throttle_seconds, token_store
from fastplace.auth.remember import remember_store
from fastplace.errors import ValidationError
from fastplace.events import DomainEvent, dispatch
from fastplace.http import build_absolute_url
from fastplace.mail import Mail
from fastplace.mail.notifications import reset_password_message
from fastplace.ratelimit import RateLimiter


class PasswordResetService:
    """Forgot-password and reset — enumeration-safe, token-gated (spec §4.10)."""

    SENT_MESSAGE = "We have emailed your password reset link."
    RESET_MESSAGE = "Your password has been reset."

    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository if repository is not None else UserRepository()

    async def send_reset_link(self, email: str) -> str:
        """Throttle per address, then issue + mail — always the same answer."""
        normalized = str(email or "").strip().lower()
        limiter = RateLimiter()  # fresh: the limiter binds the cache at construction
        key = hashlib.sha1(normalized.encode("utf-8")).hexdigest()  # email ALONE — no ip
        if await limiter.too_many_attempts(key, 1):
            return self.SENT_MESSAGE  # throttled — same answer, no work, no signal
        await limiter.hit(key, throttle_seconds())

        user = await self.repository.find_by_email(normalized)
        if user is None:
            # Equal work: the unknown-email path pays the same scrypt cost a
            # wrong password pays on login (timing parity, spec §6).
            Hash.check(normalized, _dummy_digest())
            return self.SENT_MESSAGE

        raw = await token_store().issue(normalized)
        url = build_absolute_url(f"/reset-password/{raw}?email={quote(normalized, safe='')}")
        await Mail.to(normalized).send(reset_password_message(normalized, url))
        await dispatch(DomainEvent("PasswordResetLinkSent", {"email": normalized}))
        return self.SENT_MESSAGE

    async def reset(self, request: Any, data: dict[str, Any]) -> None:
        """Redeem a reset token: validate, burn, rehash, revoke, announce."""
        email = str(data.get("email") or "").strip().lower()
        token = str(data.get("token") or "")
        password = str(data.get("password") or "")
        confirmation = str(data.get("password_confirmation") or "")

        errors: dict[str, list[str]] = {}
        minimum = min_password_length()
        if len(password) < minimum:
            errors.setdefault("password", []).append(
                f"The password must be at least {minimum} characters."
            )
        if password != confirmation:
            errors.setdefault("password", []).append("The password confirmation does not match.")
        user = await self.repository.find_by_email(email)
        # peek (non-consuming) so a validation failure never burns the token;
        # every miss path pays the dummy scrypt (timing parity, spec §6).
        valid = await token_store().peek(email, token)
        if user is None or not valid:
            errors.setdefault("email", []).append(
                "We could not find a user with that email address."
            )
        if errors:
            raise ValidationError(errors=errors)

        if not await token_store().consume(email, token):  # lost the race
            raise ValidationError(
                errors={"email": ["We could not find a user with that email address."]}
            )

        await user.update(password_hash=Hash.make(password))
        await remember_store().revoke_all_for_user(user.id)
        store = request.scope.get(SESSION_STORE_SCOPE)
        if store is not None:
            await store.destroy_for_user(user.id)  # every session — no except_session_id
        await dispatch(DomainEvent("PasswordReset", {"user_id": user.id, "email": user.email}))
'''
_REGISTRATION_SERVICE_TEMPLATE = '''"""Account registration — validation, creation, the Registered event."""

from __future__ import annotations

from typing import Any

from app.modules.accounts.models.user import User
from app.modules.accounts.repositories.user_repository import UserRepository
from app.modules.accounts.services.password_policy import min_password_length
from fastplace.auth.guards import guard
from fastplace.errors import ValidationError
from fastplace.events import DomainEvent, dispatch


class RegistrationService:
    """Registers an account: validates, creates, dispatches, logs in (spec §4.5).

    No ``model_validator`` on the request object — a pydantic model_validator's
    mismatch error lands at ``loc=()``, which the frontend maps to no field;
    this service owns the mismatch and files it under ``errors["password"]``.
    """

    def __init__(self, repository: UserRepository | None = None) -> None:
        self.repository = repository if repository is not None else UserRepository()

    def _min_password_length(self) -> int:
        # Phase 2 honors the "min:N" clause of PASSWORD_RULES (default min:8);
        # richer clauses arrive with the settings UI.
        return min_password_length()

    async def register(self, request: Any, data: dict[str, Any]) -> User:
        name = str(data.get("name") or "").strip()
        email = str(data.get("email") or "").strip().lower()
        password = str(data.get("password") or "")
        confirmation = str(data.get("password_confirmation") or "")

        errors: dict[str, list[str]] = {}
        if await self.repository.find_by_email(email) is not None:
            errors.setdefault("email", []).append("The email has already been taken.")
        minimum = self._min_password_length()
        if len(password) < minimum:
            errors.setdefault("password", []).append(
                f"The password must be at least {minimum} characters."
            )
        if password != confirmation:
            errors.setdefault("password", []).append("The password confirmation does not match.")
        if errors:
            raise ValidationError(errors=errors)

        user = await self.repository.create_user(name=name, email=email, password=password)
        await dispatch(DomainEvent("Registered", {"user_id": user.id, "email": user.email}))
        await guard().login(request, user)
        return user
'''
_TWO_FACTOR_SERVICE_TEMPLATE = '''"""Two-factor orchestration — challenge fulfillment + settings management.

The framework owns the crypto primitives (fastplace.auth.two_factor /
encryption); this service owns the account workflow: verifying challenges,
consuming recovery codes, and the enable/confirm/disable lifecycle.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

from app.modules.accounts.repositories.user_repository import UserRepository
from fastplace.auth.encryption import decrypt, encrypt
from fastplace.auth.guards import TWO_FACTOR_CHALLENGE_KEY, TWO_FACTOR_REMEMBER_KEY, guard
from fastplace.auth.two_factor import generate_recovery_codes, generate_secret, verify_code
from fastplace.errors import ValidationError


class TwoFactorService:
    """Challenge + management flows over the encrypted users columns."""

    INVALID_CODE_MESSAGE = "The provided two factor authentication code is invalid."

    users = UserRepository()

    def _invalid(self, field: str = "code") -> ValidationError:
        return ValidationError(errors={field: [self.INVALID_CODE_MESSAGE]})

    async def verify_challenge(self, request: Any, *, code: str, recovery_code: str) -> bool | None:
        """Fulfill a parked challenge. None = no challenge; False = wrong code."""
        challenge_user = request.session.get(TWO_FACTOR_CHALLENGE_KEY)
        if challenge_user is None:
            return None
        user = await self.users.find_by_id(challenge_user)
        if user is None or user.two_factor_secret is None or user.two_factor_confirmed_at is None:
            return False

        ok = False
        if code:
            ok = verify_code(decrypt(user.two_factor_secret), code)
        elif recovery_code and user.two_factor_recovery_codes:
            stored = json.loads(decrypt(user.two_factor_recovery_codes))
            match = next((c for c in stored if hmac.compare_digest(c, recovery_code)), None)
            if match is not None:
                # Single-use: the redeemed code leaves the store immediately.
                stored.remove(match)
                user.two_factor_recovery_codes = encrypt(json.dumps(stored))
                await user.save()
                ok = True

        if not ok:
            return False
        remember = bool(request.session.get(TWO_FACTOR_REMEMBER_KEY))
        await guard().login_using_id(request, user.id, remember=remember)
        return True

    # ------------------------------------------------------------------
    # Settings management (spec §4.13) — every route behind auth + a recent
    # password confirmation. R10: with TWO_FACTOR_ENABLED falsy each method
    # hides behind a 404 instead of leaking that the surface exists.
    # ------------------------------------------------------------------

    def _require_enabled(self) -> None:
        from fastplace.config import config
        from fastplace.errors import NotFoundError

        if not config("TWO_FACTOR_ENABLED", default=True):
            raise NotFoundError("Two-factor authentication is not enabled.")

    async def enable(self, request: Any) -> None:
        """Generate + store a pending secret and a fresh code batch."""
        self._require_enabled()
        user = request.user
        user.two_factor_secret = encrypt(generate_secret())
        user.two_factor_recovery_codes = encrypt(json.dumps(generate_recovery_codes()))
        user.two_factor_confirmed_at = None
        await user.save()

    async def confirm(self, request: Any, code: str) -> None:
        """Complete the setup: a valid TOTP code stamps confirmed_at.

        The flag check runs before any code verdict, and an absent/invalid
        code shares the one frozen 422 (verify_code rejects an empty string).
        """
        self._require_enabled()
        import datetime

        user = request.user
        if not user.two_factor_secret or not verify_code(decrypt(user.two_factor_secret), code):
            raise self._invalid()
        user.two_factor_confirmed_at = datetime.datetime.now(datetime.UTC)
        await user.save()

    async def disable(self, request: Any) -> None:
        """Wipe all three columns."""
        self._require_enabled()
        user = request.user
        user.two_factor_secret = None
        user.two_factor_recovery_codes = None
        user.two_factor_confirmed_at = None
        await user.save()

    async def regenerate_recovery_codes(self, request: Any) -> list[str]:
        self._require_enabled()
        user = request.user
        codes = generate_recovery_codes()
        user.two_factor_recovery_codes = encrypt(json.dumps(codes))
        await user.save()
        return codes

    async def recovery_codes(self, request: Any) -> list[str]:
        self._require_enabled()
        raw = request.user.two_factor_recovery_codes
        return json.loads(decrypt(raw)) if raw else []

    async def qr_code_payload(self, request: Any) -> dict[str, str]:
        self._require_enabled()
        if not request.user.two_factor_secret:
            raise self._invalid()
        from fastplace.auth.two_factor import otp_auth_uri, qr_code_svg

        uri = otp_auth_uri(decrypt(request.user.two_factor_secret), request.user.email)
        return {"svg": qr_code_svg(uri), "url": uri}

    async def secret_key_payload(self, request: Any) -> dict[str, str]:
        self._require_enabled()
        if not request.user.two_factor_secret:
            raise self._invalid()
        return {"secretKey": decrypt(request.user.two_factor_secret)}
'''
_VERIFICATION_SERVICE_TEMPLATE = '''"""Email verification — APP_KEY-signed links carried by the mail subsystem (§4.11)."""

from __future__ import annotations

import datetime
from typing import Any

from app.modules.accounts.models.user import User
from fastplace.auth.signing import sign, verify
from fastplace.errors import AuthorizationError
from fastplace.events import DomainEvent, dispatch
from fastplace.http import build_absolute_url
from fastplace.mail import Mail
from fastplace.mail.notifications import verify_email_message

#: Seconds a verification link stays valid.
VERIFICATION_TTL = 3600


class VerificationService:
    """Signs, mails, and redeems email-verification links."""

    def verification_url(self, user_id: Any, email: str) -> str:
        """The signed /email/verify/{id}/{hash}?expires=... link."""
        signature, expires = sign(f"{user_id}|{email}", ttl=VERIFICATION_TTL)
        return build_absolute_url(f"/email/verify/{user_id}/{signature}?expires={expires}")

    async def send_link(self, user_id: Any, email: str) -> str:
        """Mail the verification link; returns the URL (tests read it from the outbox)."""
        url = self.verification_url(user_id, email)
        await Mail.to(email).send(verify_email_message(email, url))
        return url

    async def resend(self, request: Any) -> str:
        """Re-mail the signed-in user's link (the notice page's resend button)."""
        user = getattr(request, "user", None)
        if user is None:
            raise AuthorizationError()
        return await self.send_link(user.id, user.email)

    async def fulfill(self, request: Any, user_id: str, signature: str, expires: str) -> None:
        """Redeem a verification click — 403 on any mismatch, idempotent on replay."""
        user = getattr(request, "user", None)
        if user is None or str(user.id) != str(user_id):
            raise AuthorizationError()
        # Path params arrive as STRINGS; verify() returns False for a non-int
        # expires, so that case lands here too.
        if not verify(f"{user.id}|{user.email}", signature, expires):
            raise AuthorizationError()

        from fastplace.db import db  # function-level: fresh binding per call

        async with db.manager.engine("default").begin() as conn:
            result = await conn.execute(
                User.__table__.update()
                .where(
                    User.__table__.c.id == user.id,
                    User.__table__.c.email_verified_at.is_(None),
                )
                .values(email_verified_at=datetime.datetime.now(datetime.UTC))
            )
        if result.rowcount == 1:
            await dispatch(DomainEvent("Verified", {"user_id": user.id, "email": user.email}))
        # rowcount == 0 → replay of an already-verified click: idempotent, no error.
'''
_LOGIN_REQUEST_TEMPLATE = '''"""Login form request — field-level validation only (no cross-field rules)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    remember: str = Field(default="", max_length=8)
'''
_REGISTER_REQUEST_TEMPLATE = '''"""Registration form request — field-level validation only.

The password_confirmation mismatch is adjudicated in RegistrationService
(a pydantic model_validator would surface the error at loc=(), which the
frontend cannot map to a field).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
'''
_CONFIRM_PASSWORD_REQUEST_TEMPLATE = '''"""Confirm-password form request — the single field, nothing else.

No min_length: a missing/empty password must reach the service, which owns
the frozen "The password field is required." contract string (R12) — the
framework's validation layer has no translation for it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConfirmPasswordRequest(BaseModel):
    password: str = Field(default="", max_length=255)
'''
_FORGOT_PASSWORD_REQUEST_TEMPLATE = '''"""Forgot-password form request — one field, nothing else accepted."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
'''
_RESET_PASSWORD_REQUEST_TEMPLATE = '''"""Reset-password form request — password policy runs in the service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResetPasswordRequest(BaseModel):
    # password min 1 here so the length policy (min_password_length) runs in
    # the service, where the token is NOT yet consumed.
    token: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
'''
_AUTH_API_CONTROLLER_TEMPLATE = '''"""Credential endpoints — thin controllers, all logic in the services."""

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
        if outcome is False:
            # The error key tracks what was submitted: a recovery-code
            # attempt fails under recovery_code, everything else under code.
            field = "recovery_code" if recovery_code and not code else "code"
            raise self.two_factor_service._invalid(field)
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
'''
_TWO_FACTOR_API_CONTROLLER_TEMPLATE = '''"""Two-factor management endpoints — thin, JSON-only (spec §4.13)."""

from __future__ import annotations

import json

from app.modules.accounts.services.two_factor_service import TwoFactorService
from fastplace.http import Controller, Json, Request


class TwoFactorApiController(Controller):
    two_factor_service = TwoFactorService()

    async def enable(self, request: Request):
        await self.two_factor_service.enable(request)
        return Json({"ok": True})

    async def confirm(self, request: Request):
        # The frozen page posts {code} — a raw JSON read matches (the
        # challenge endpoint's precedent). An undecodable or non-dict body
        # flows through as no code, and the service owns the verdict: R10's
        # flag check runs before it, then any absent/invalid code gets the
        # one frozen 422.
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = str(body.get("code") or "").strip()
        await self.two_factor_service.confirm(request, code)
        return Json({"ok": True})

    async def disable(self, request: Request):
        await self.two_factor_service.disable(request)
        return Json({"ok": True})

    async def regenerate_recovery_codes(self, request: Request):
        return Json(await self.two_factor_service.regenerate_recovery_codes(request))

    async def recovery_codes(self, request: Request):
        return Json(await self.two_factor_service.recovery_codes(request))

    async def qr_code(self, request: Request):
        return Json(await self.two_factor_service.qr_code_payload(request))

    async def secret_key(self, request: Request):
        return Json(await self.two_factor_service.secret_key_payload(request))
'''
_AUTH_ROUTES_TEMPLATE = '''"""Credential + verification endpoints (spec §4.5/§4.11) — the GET pages live in web.py."""

from app.http.controllers.auth_api_controller import AuthApiController
from app.http.controllers.two_factor_api_controller import TwoFactorApiController
from fastplace.http import Router

router = Router()

# The ".store" suffix avoids colliding with the GET routes' auth.login /
# auth.register names in routes/web.py. "guest" on the POSTs mirrors the GET
# pages — a logged-in client posting /login is bounced, not re-logged.
router.post(
    "/login",
    AuthApiController,
    "login",
    name="auth.login.store",
    middleware=["guest", "throttle:5,60"],
)
router.post(
    "/register",
    AuthApiController,
    "register",
    name="auth.register.store",
    middleware=["guest", "throttle:5,60"],
)
router.post(
    "/logout",
    AuthApiController,
    "logout",
    name="auth.logout",
    middleware=["auth"],
)
router.post(
    "/forgot-password",
    AuthApiController,
    "forgot_password",
    name="auth.forgot_password.store",
    middleware=["throttle:5,60"],  # NO guest — a logged-in user may reset too
)
router.post(
    "/reset-password",
    AuthApiController,
    "reset_password",
    name="auth.reset_password.store",
    middleware=["throttle:5,60"],
)

# Email verification — the notice page lives in web.py; these are the
# signed-link redemption and the resend (spec §4.11).
router.get(
    "/email/verify/{id}/{hash}",
    AuthApiController,
    "verify_email",
    name="auth.verify_email.fulfill",
    middleware=["auth"],
)
router.post(
    "/email/verification-notification",
    AuthApiController,
    "verification_notification",
    name="auth.verification_notification.store",
    # Spec §4.19's throttle:6,1 — fastplace's D is SECONDS (R8).
    middleware=["auth", "throttle:6,60"],
)

# Password confirmation (spec §4.12) — the confirmation window the
# password.confirm route middleware enforces.
router.post(
    "/user/confirm-password",
    AuthApiController,
    "confirm_password",
    name="auth.confirm_password.store",
    middleware=["auth", "throttle:6,60"],  # spec's 6/min — D is seconds (R8)
)

# Two-factor challenge fulfillment (spec §4.13) — the login interrupt's
# second half. Guest: the parked session is anonymous by design.
router.post(
    "/two-factor-challenge",
    AuthApiController,
    "two_factor_challenge",
    name="auth.two_factor_challenge.store",
    middleware=["guest", "throttle:5,60"],
)

# Two-factor management (spec §4.13) — every route behind auth + a recent
# password confirmation (the first routes to name the alias). The confirm
# URL is the frozen frontend's /user/confirmed-two-factor-authentication
# (R1), and the GET on the recovery-codes URL serves the hook's bare-array
# fetch (R8: GET + POST share one URL).
_TWO_FACTOR_MIDDLEWARE = ["auth", "password.confirm"]
router.post(
    "/user/two-factor-authentication",
    TwoFactorApiController,
    "enable",
    name="auth.two_factor.enable",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.delete(
    "/user/two-factor-authentication",
    TwoFactorApiController,
    "disable",
    name="auth.two_factor.disable",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.post(
    "/user/confirmed-two-factor-authentication",
    TwoFactorApiController,
    "confirm",
    name="auth.two_factor.confirm",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.post(
    "/user/two-factor-recovery-codes",
    TwoFactorApiController,
    "regenerate_recovery_codes",
    name="auth.two_factor.regenerate",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.get(
    "/user/two-factor-recovery-codes",
    TwoFactorApiController,
    "recovery_codes",
    name="auth.two_factor.recovery_codes",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.get(
    "/user/two-factor-qr-code",
    TwoFactorApiController,
    "qr_code",
    name="auth.two_factor.qr_code",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
router.get(
    "/user/two-factor-secret-key",
    TwoFactorApiController,
    "secret_key",
    name="auth.two_factor.secret_key",
    middleware=_TWO_FACTOR_MIDDLEWARE,
)
'''

_USER_SEEDER_TEMPLATE = '''"""User seeder — the first account for a fresh project."""

from __future__ import annotations

import datetime
import os

from app.modules.accounts.models.user import User
from fastplace.auth.hashing import Hash


async def run() -> None:
    email = os.environ.get("SEED_USER_EMAIL", "admin@example.com")
    password = os.environ.get("SEED_USER_PASSWORD", "password")
    if await User.where(User.email == email).first() is not None:
        return
    await User.create(
        name="Admin",
        email=email,
        password_hash=Hash.make(password),
        email_verified_at=datetime.datetime.now(datetime.UTC),
    )
'''

_GATES_TEMPLATE = '''"""Project gate registrations — imported by the kernel at boot (spec §4.15).

Every ability the ``can:`` middleware or ``authorize()`` names must be
defined here (or bound through a policy); unknown abilities fail loud.
"""

from __future__ import annotations

from fastplace.authz import gate


@gate.define("view-dashboard")
async def view_dashboard(user, *args):
    # Replace with real rules — the starter allows every signed-in user.
    return user is not None


@gate.before
async def superuser(user, ability, *args):
    # Example global hook: the seeded admin passes every check. Delete or
    # tighten for your project.
    if user is not None and getattr(user, "email", None) == "admin@example.com":
        return True
    return None
'''


@generators_app.command("make:auth")
def make_auth(
    migration: bool = typer.Option(
        True,
        "--migration/--no-migration",
        help="Also autogenerate the users-table migration.",
    ),
) -> None:
    """Scaffold the auth surface (spec §4.17) — new files only."""
    root = _project_root()

    _write(root / "app/modules/accounts/models/user.py", _USER_MODEL_TEMPLATE, root)
    _write(
        root / "app/modules/accounts/repositories/user_repository.py",
        _USER_REPOSITORY_TEMPLATE,
        root,
    )
    for name, template in (
        ("auth_service.py", _AUTH_SERVICE_TEMPLATE),
        ("password_policy.py", _PASSWORD_POLICY_TEMPLATE),
        ("password_reset_service.py", _PASSWORD_RESET_SERVICE_TEMPLATE),
        ("registration_service.py", _REGISTRATION_SERVICE_TEMPLATE),
        ("two_factor_service.py", _TWO_FACTOR_SERVICE_TEMPLATE),
        ("verification_service.py", _VERIFICATION_SERVICE_TEMPLATE),
    ):
        _write(root / "app/modules/accounts/services" / name, template, root)
    for name, template in (
        ("login_request.py", _LOGIN_REQUEST_TEMPLATE),
        ("register_request.py", _REGISTER_REQUEST_TEMPLATE),
        ("confirm_password_request.py", _CONFIRM_PASSWORD_REQUEST_TEMPLATE),
        ("forgot_password_request.py", _FORGOT_PASSWORD_REQUEST_TEMPLATE),
        ("reset_password_request.py", _RESET_PASSWORD_REQUEST_TEMPLATE),
    ):
        _write(root / "app/http/requests" / name, template, root)
    _write(
        root / "app/http/controllers/auth_api_controller.py", _AUTH_API_CONTROLLER_TEMPLATE, root
    )
    _write(
        root / "app/http/controllers/two_factor_api_controller.py",
        _TWO_FACTOR_API_CONTROLLER_TEMPLATE,
        root,
    )
    _write(root / "routes/auth.py", _AUTH_ROUTES_TEMPLATE, root)
    _write(root / "database/seeders/user_seeder.py", _USER_SEEDER_TEMPLATE, root)
    _write(root / "app/auth/gates.py", _GATES_TEMPLATE, root)

    # Package markers so pkgutil/import_gates discovery finds the new code.
    for marker in (
        root / "app/modules/accounts/__init__.py",
        root / "app/modules/accounts/models/__init__.py",
        root / "app/modules/accounts/repositories/__init__.py",
        root / "app/modules/accounts/services/__init__.py",
        root / "app/auth/__init__.py",
    ):
        _write(marker, "", root)

    if migration:
        from fastplace.cli.database import _manager

        manager = _manager()
        if not manager.configured:
            manager.scaffold()  # make:auth works on a fresh project too
        revision = manager.make("create_users_table")
        if revision is not None:
            console.print(f"[green]created[/] {revision.relative_to(root)}")

    console.print("\n[bold]Auth scaffold complete.[/] Manual steps left:")
    console.print("  1. [cyan]fastplace migrate[/] — create the users table")
    console.print("  2. [cyan]fastplace db:seed[/] — seed the first user")
    console.print("  3. point AUTH_PROVIDERS at the ORM User (the default in config/auth.py)")
    console.print("  4. add MAIL_* keys to .env for reset/verification mail")
    console.print("  5. set AUTH_SHARED_ABILITIES in config/auth.py for useCan() props")
