"""``fastplace make:auth`` — the auth surface scaffolder (spec §4.17)."""

from __future__ import annotations

from pathlib import Path

import typer

from fastplace.cli.generators import (
    _project_root,
    _rewrite_fastplace_dep_for_auth_extras,
    _write,
    console,
    generators_app,
)

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

    # Admin grant — set only by explicit service/CLI code, never fillable:
    # mass assignment must never escalate privileges (OWASP).
    is_admin: bool = Field(default=False)
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

    async def create_user(
        self, *, name: str, email: str, password: str, is_admin: bool = False
    ) -> User:
        # is_admin deliberately bypasses User.create(): the column is not
        # mass-assignable, so admin grants flow only through this explicit
        # keyword — never from a request payload.
        user = User(
            name=name,
            email=email,
            password_hash=Hash.make(password),
            is_admin=is_admin,
        )
        await user.save()
        return user
'''
_USER_SERVICE_TEMPLATE = '''"""Account-level user queries — the accounts module's public service seam.

Controllers and other modules consume THIS class, never the repository
below it (the service layer is the only public edge of a bounded module).
"""

from __future__ import annotations

from app.modules.accounts.repositories.user_repository import UserRepository


class UserService:
    """The one door to user lookups from outside the module."""

    users = UserRepository()

    async def email_in_use(self, email: str, *, excluding_id: object = None) -> bool:
        """True when ANOTHER account already holds the email.

        The caller's own row is never a conflict (profile-update semantics);
        the excluding_id keeps the check honest across it.
        """
        existing = await self.users.find_by_email(email)
        return existing is not None and str(existing.id) != str(excluding_id)
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
from app.modules.accounts.services.mail_views import reset_password_email_html
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
        message = reset_password_message(normalized, url)
        message.html = reset_password_email_html(url)
        await Mail.to(normalized).send(message)
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

        # Binding product rule: the FIRST real signup owns the app — admin
        # by registration count, never by seeded credentials. Later signups
        # stay regular until a developer promotes them (user:create --admin).
        is_first_user = await User.count() == 0
        user = await self.repository.create_user(
            name=name, email=email, password=password, is_admin=is_first_user
        )
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

    async def is_enabled(self, request: Any) -> bool:
        """Whether the signed-in user has a CONFIRMED setup (pending secrets
        never count — the QR was shown but no code was ever verified)."""
        self._require_enabled()
        return getattr(request.user, "two_factor_confirmed_at", None) is not None

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
from app.modules.accounts.services.mail_views import verification_email_html
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
        message = verify_email_message(email, url)
        message.html = verification_email_html(url)
        await Mail.to(email).send(message)
        return url

    async def resend(self, request: Any) -> str | None:
        """Re-mail the signed-in user's link — unless they are already verified.

        An already-verified account gets NO new mail (the notice page's
        resend button is not a way to pester an inbox); the controller
        answers that case with the intended-page redirect / empty 204
        instead. A fresh link's URL comes back (tests read the outbox).
        """
        user = getattr(request, "user", None)
        if user is None:
            raise AuthorizationError()
        if user.email_verified_at is not None:
            return None
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

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        # Whitespace is never a name: strip BEFORE the length constraints
        # run, so a whitespace-only submission fails the required check.
        return value.strip() if isinstance(value, str) else value
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

from pydantic import BaseModel, EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
'''
_RESET_PASSWORD_REQUEST_TEMPLATE = '''"""Reset-password form request — password policy runs in the service."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class ResetPasswordRequest(BaseModel):
    # password min 1 here so the length policy (min_password_length) runs in
    # the service, where the token is NOT yet consumed.
    token: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
'''
_PROFILE_REQUEST_TEMPLATE = '''"""Profile-update form request — unique-ignore-self runs in the controller."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        # Whitespace is never a name: strip BEFORE the length constraints
        # run, so a whitespace-only submission fails the required check.
        return value.strip() if isinstance(value, str) else value
'''
_PASSWORD_UPDATE_REQUEST_TEMPLATE = '''"""Password-change form request — the policy checks run in the controller."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PasswordUpdateRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
'''
_DELETE_PROFILE_REQUEST_TEMPLATE = '''"""Account-deletion form request — the posted password is the confirmation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeleteProfileRequest(BaseModel):
    password: str = Field(min_length=1, max_length=255)
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
from fastplace.http import Controller, Json, Redirect, Request, Response, flash


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
        # resend() answers None for an already-verified user — no new mail
        # goes out; browsers continue to their intended page and JSON
        # clients get the empty 204 the reference contract pins.
        already_verified = await self.verification_service.resend(request) is None
        if already_verified:
            accept = request.header("Accept") or ""
            if not request.is_bridge and "application/json" in accept:
                return Response(status_code=204)
            return Redirect(request.intended(), status_code=303)
        flash(
            request, "verification-link-sent"
        )  # BYTE-EXACT — VerifyEmail renders on exact equality
        return Redirect("/email/verify", status_code=303)
'''
_TWO_FACTOR_API_CONTROLLER_TEMPLATE = '''"""Two-factor management endpoints — thin, JSON-only (spec §4.13)."""

from __future__ import annotations

import json

from app.modules.accounts.services.two_factor_service import TwoFactorService
from fastplace.http import Controller, Json, Redirect, Request


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

    async def status(self, request: Request):
        """Recovery target after the password-confirmation wall: the frozen
        frontend GETs this URL mid-enable-journey. Browser visitors (the
        bridge fetch or a plain navigation) are walked back to the security
        page — the fetch layer follows the 303 and swaps in the page payload,
        so re-clicking Enable now runs against a confirmed session. A JSON
        API client gets the boolean instead."""
        enabled = await self.two_factor_service.is_enabled(request)
        accept = request.header("Accept") or ""
        if request.is_bridge or "application/json" not in accept:
            return Redirect("/settings/security", status_code=303)
        return Json({"enabled": enabled})

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
from app.http.controllers.settings_api_controller import SettingsApiController
from app.http.controllers.two_factor_api_controller import TwoFactorApiController
from app.http.shared_props import register_flash_props
from fastplace.http import Router

# Boot-time shared-props registration: the one-shot session flash surfaces
# as props.flash (the sonner/useFlashToast pair reads flash.toast). share()
# dedupes, so repeated imports stay a single registration.
register_flash_props()

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
    # Redemption is a GET any browser can be pointed at — it shares the
    # resend limiter (spec's 6/min; fastplace's D is SECONDS, R8).
    middleware=["auth", "throttle:6,60"],
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
# The status GET shares the URL: after the password-confirmation wall parks
# an enable POST, the frontend recovers by GETting this — a 404 here kills
# the whole enable journey.
router.get(
    "/user/two-factor-authentication",
    TwoFactorApiController,
    "status",
    name="auth.two_factor.status",
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

# Account settings (spec §4.14) — the write targets the shipped settings UI
# posts to, plus the /settings alias into the section. The section's GET
# pages live in routes/web.py.
router.get(
    "/settings",
    SettingsApiController,
    "index",
    name="settings.index",
    middleware=["auth"],
)
router.patch(
    "/settings/profile",
    SettingsApiController,
    "update_profile",
    name="settings.profile.update",
    # A changed email re-enters verification, so the section stays gated on it.
    middleware=["auth", "verified", "throttle:6,60"],
)
router.put(
    "/settings/password",
    SettingsApiController,
    "update_password",
    name="settings.password.update",
    middleware=["auth", "throttle:6,60"],  # spec's 6/min — D is seconds (R8)
)
router.delete(
    "/settings/profile",
    SettingsApiController,
    "destroy",
    name="settings.profile.destroy",
    # The posted password IS the confirmation — the DeleteUser dialog sends it.
    middleware=["auth"],
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
    # The first registered account (is_admin) passes every check. Grant is
    # data, not identity: promote via `fastplace user:create --admin` or a
    # direct DB edit.
    if user is not None and getattr(user, "is_admin", False):
        return True
    return None
'''


_ACCOUNTS_MODELS_INIT_TEMPLATE = """from app.modules.accounts.models.user import User

__all__ = ["User"]
"""

_MAIL_JOB_TEMPLATE = '''"""Mail delivery job + the Registered verification listener.

``mail_send`` is the queue entry point (queued only when MAIL_DRIVER=smtp
and QUEUE_DRIVER=saq — see fastplace.mail.Mail.send); the listener runs
in-process (``Registered`` has no same-named @Job, so dispatch never
auto-enqueues it).
"""

from __future__ import annotations

import logging

from fastplace.errors import ConfigurationError
from fastplace.events import DomainEvent, listen
from fastplace.mail import Mail, message_from_dict
from fastplace.queue import Job

logger = logging.getLogger(__name__)


@Job(name="mail_send")
async def mail_send(message: dict) -> None:
    """Deliver one queued MailMessage payload (param name matters: saq
    hijacks handler kwargs named timeout/ttl/kwargs)."""
    await Mail.deliver(message_from_dict(message))


async def send_registration_verification(event: DomainEvent) -> None:
    """Mail the verification link for a freshly registered account."""
    from app.modules.accounts.services.verification_service import VerificationService

    email = str(event.payload["email"])
    try:
        await VerificationService().send_link(int(event.payload["user_id"]), email)
    except ConfigurationError:
        # Empty APP_KEY is the documented dev default (config/app.py) and the
        # account is already committed by the time this listener runs — skip
        # the signed link rather than fail the whole registration.
        logger.warning("APP_KEY is not set — skipping verification email for %s", email)


# listen() is a plain function, NOT a decorator factory — explicit call.
listen("Registered", send_registration_verification)
'''

_SHARED_PROPS_TEMPLATE = '''"""Shared page props — the session flash bag mapped to ``props.flash``.

The bridge toast loop (sonner + ``useFlashToast``) reads ``props.flash.toast``;
this registration turns the one-shot ``flash(request, ...)`` channel into
that shape. ``page_payload()`` still consumes the message afterwards (its
``props.status`` string channel keeps working), so nothing double-fires.
"""

from __future__ import annotations

from typing import Any

from fastplace.http.flash import FLASH_SESSION_KEY
from fastplace.http.render import share
from fastplace.http.request import Request


def flash_props(request: Request) -> dict[str, Any] | None:
    """Contribute ``flash.toast`` while a one-shot flash is pending."""
    try:
        session = request.session
    except Exception:
        return None
    if not isinstance(session, dict):
        return None
    message = session.get(FLASH_SESSION_KEY)
    if not message:
        return None
    return {"flash": {"toast": {"type": "success", "message": str(message)}}}


def register_flash_props() -> None:
    """The boot hook — routes/auth.py calls this once at import."""
    share(flash_props)
'''

_SETTINGS_API_CONTROLLER_TEMPLATE = '''"""Settings write endpoints — profile, password, account deletion (§4.14).

The shipped settings UI posts to these three targets; the controllers stay
thin and the rules live in the requests and the guard/provider primitives.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.http.requests.delete_profile_request import DeleteProfileRequest
from app.http.requests.password_update_request import PasswordUpdateRequest
from app.http.requests.profile_request import ProfileRequest
from app.modules.accounts.services.password_policy import min_password_length
from app.modules.accounts.services.user_service import UserService
from app.modules.accounts.services.verification_service import VerificationService
from fastplace.auth.guards import SESSION_STORE_SCOPE, guard
from fastplace.auth.hashing import Hash
from fastplace.errors import ValidationError
from fastplace.http import Controller, Redirect, Request, flash


class SettingsApiController(Controller):
    # The service seam, not the repository: controllers stay outside the
    # module boundary that lint:modules enforces.
    users = UserService()
    verification_service = VerificationService()

    async def index(self, request: Request):
        # The settings section has no page of its own — /settings is an alias.
        return Redirect("/settings/profile", status_code=303)

    async def update_profile(self, request: Request):
        data = (await request.validate(ProfileRequest)).model_dump()
        user = request.user
        email = str(data["email"]).strip().lower()

        # Unique-ignore-self: only ANOTHER account's email is a conflict.
        if await self.users.email_in_use(email, excluding_id=user.id):
            raise ValidationError(errors={"email": ["The email has already been taken."]})

        if email != str(user.email).strip().lower():
            try:
                # The new address is unverified until its link is clicked — the
                # verification pipeline re-runs exactly like a fresh signup.
                await user.update(name=data["name"], email=email, email_verified_at=None)
            except IntegrityError:
                # The find-first above can lose a race to a concurrent
                # registration; the UNIQUE constraint settles it with the
                # same friendly 422 instead of a 500.
                raise ValidationError(
                    errors={"email": ["The email has already been taken."]}
                ) from None
            await self.verification_service.send_link(user.id, email)
        else:
            await user.update(name=data["name"])
        flash(request, "Profile updated.")
        return Redirect("/settings/profile", status_code=303)

    async def update_password(self, request: Request):
        data = (await request.validate(PasswordUpdateRequest)).model_dump()
        user = request.user
        if not await guard().provider.validate_credentials(
            user, {"password": data["current_password"]}
        ):
            raise ValidationError(errors={"current_password": ["The password is incorrect."]})

        errors: dict[str, list[str]] = {}
        minimum = min_password_length()
        if len(data["password"]) < minimum:
            errors.setdefault("password", []).append(
                f"The password must be at least {minimum} characters."
            )
        if data["password"] != data["password_confirmation"]:
            errors.setdefault("password", []).append("The password confirmation does not match.")
        if errors:
            raise ValidationError(errors=errors)

        await user.update(password_hash=Hash.make(data["password"]))
        # A rotated password must kill every credential a thief already
        # holds: the guard sweeps the OTHER sessions (the current one keeps
        # its payload) and rotates the remember token — the same posture
        # the password-reset flow takes; regenerate() retires this
        # session's old ID on the next persist, so a copied cookie dies.
        await guard().logout_other_devices(request, data["current_password"])
        request.session.regenerate()
        flash(request, "Password updated.")
        return Redirect("/settings/security", status_code=303)

    async def destroy(self, request: Request):
        data = (await request.validate(DeleteProfileRequest)).model_dump()
        user = request.user
        if not await guard().provider.validate_credentials(user, {"password": data["password"]}):
            raise ValidationError(errors={"password": ["The password is incorrect."]})

        store = request.scope.get(SESSION_STORE_SCOPE)
        if store is not None:
            await store.destroy_for_user(user.id)  # every device, not just this one
        await guard().logout(request)  # expires this session's backing row + cookie
        # force_delete, NOT delete(): the base model stamps deleted_at, and a
        # tombstone keeps the unique email locked — re-registering it would
        # hit the constraint and 500.
        await user.force_delete()
        return Redirect("/", status_code=303)
'''

_MAIL_VIEWS_TEMPLATE = '''"""HTML bodies for the auth emails — the transports are multipart-aware.

The plain-text channel stays authoritative (its bare URL is the
break-out-of-anything fallback); the HTML body wraps the same URL in a
branded-light button for clients that render markup.
"""

from __future__ import annotations

from html import escape

_BUTTON_STYLE = (
    "display:inline-block;padding:12px 24px;background:#4f46e5;color:#ffffff;"
    "border-radius:8px;text-decoration:none;font-family:sans-serif;"
    "font-size:14px;font-weight:600"
)


def _html_document(title: str, body_html: str, url: str, button_label: str) -> str:
    return "\\n".join(
        [
            '<div style="font-family:sans-serif;max-width:480px;margin:0 auto">',
            f'<h2 style="font-size:18px;margin:24px 0 12px">{escape(title)}</h2>',
            body_html,
            f'<p style="margin:24px 0"><a href="{escape(url, quote=True)}" '
            f'style="{_BUTTON_STYLE}">{escape(button_label)}</a></p>',
            # Some clients strip buttons AND styling — the URL itself, always.
            f'<p style="word-break:break-all;color:#6b7280;font-size:12px">{escape(url)}</p>',
            "</div>",
        ]
    )


def verification_email_html(url: str) -> str:
    return _html_document(
        "Verify your email address",
        "<p>Confirm your email address to finish setting up your account.</p>",
        url,
        "Verify Email",
    )


def reset_password_email_html(url: str) -> str:
    return _html_document(
        "Reset your password",
        "<p>You requested a password reset. This link can be used once.</p>",
        url,
        "Reset Password",
    )
'''

_MAIL_CONFIG_TEMPLATE = '''"""Mail configuration defaults (env vars always win)."""

MAIL_DRIVER = "log"  # log | memory | smtp
MAIL_LOG_PATH = "storage/logs/mail.log"
MAIL_FROM_ADDRESS = "hello@example.com"
MAIL_FROM_NAME = "Fastplace"

# SMTP driver values (queued through SAQ when QUEUE_DRIVER=saq):
MAIL_HOST = "127.0.0.1"
MAIL_PORT = "2525"
MAIL_USERNAME = ""
MAIL_PASSWORD = ""
MAIL_ENCRYPTION = "tls"
'''

_DATABASE_SEEDER_TEMPLATE = '''"""DatabaseSeeder — a working example: one login-able demo account.

``fastplace db:seed`` runs the module-level ``run()`` of every
database/seeders/*.py file. Delete or extend freely — nothing in the
framework depends on this seeder.
"""

from __future__ import annotations

from app.modules.accounts.repositories.user_repository import UserRepository


async def run() -> None:
    """Create the demo account when its email is still free."""
    repository = UserRepository()
    if await repository.find_by_email("demo@example.com") is not None:
        return
    await repository.create_user(
        name="Demo User",
        email="demo@example.com",
        password="secret123",
    )
'''

_CI_WORKFLOW_TEMPLATE = """name: tests

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
      - run: npm ci
      - run: npm run lint:check
      - run: npm run format:check
      - run: npm run types
      - run: npm run test

  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: fastplace lint:modules
      - run: pytest -q
"""

_DEPENDABOT_TEMPLATE = """version: 2
updates:
  - package-ecosystem: npm
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
"""

_TESTS_CONFTEST_TEMPLATE = '''"""Test bootstrap — the real application per test, over a throwaway database.

Every fixture boots the actual routers and middleware stack against a fresh
sqlite file, so feature tests exercise the app exactly like production does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# The project root (this file lives in tests/) — app.* and routes.* import
# from here, never from an installed package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def purge_app_modules() -> None:
    """Drop every app/routes module so the next import re-registers fresh."""
    import sqlalchemy

    from fastplace.orm.model import Model

    for name in [
        m
        for m in list(sys.modules)
        if m == "app" or m.startswith(("app.", "routes.", "_fastplace_seeder_"))
    ]:
        del sys.modules[name]
    Model.metadata.clear()
    sqlalchemy.orm.clear_mappers()
    from fastplace.ai import reset_tool_registry
    from fastplace.db import reset_db

    reset_db()
    reset_tool_registry()


@pytest.fixture(autouse=True)
def _fresh_app_modules():
    purge_app_modules()
    yield
    purge_app_modules()


@pytest.fixture(autouse=True)
def _fresh_singletons(monkeypatch):
    """Fresh auth/mail/cache state + a fixed signing key for every test."""
    from fastplace.auth.passwords import reset_token_store
    from fastplace.auth.remember import reset_remember_store
    from fastplace.cache import reset_cache
    from fastplace.events import reset_listeners
    from fastplace.http.render import reset_shared_props
    from fastplace.mail import clear_mail_outbox
    from fastplace.queue import reset_registry

    monkeypatch.setenv("APP_KEY", "test-app-key-not-for-production-use")
    monkeypatch.setenv("MAIL_DRIVER", "memory")
    reset_cache()
    reset_remember_store()
    reset_token_store()
    reset_listeners()
    reset_registry()
    reset_shared_props()  # share() lives in the framework package — purge_app_modules cannot see it
    clear_mail_outbox()
    yield
    reset_cache()
    reset_remember_store()
    reset_token_store()
    reset_listeners()
    reset_registry()
    reset_shared_props()
    clear_mail_outbox()


@pytest.fixture()
async def app(monkeypatch, tmp_path):
    """The full application over a fresh database — real routers, real middleware."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")

    from app.modules.accounts.models.user import User  # noqa: F401 — registers the table
    from fastplace.db import db
    from fastplace.http import get_app
    from fastplace.http.kernel import _middleware_from_config
    from routes.ai import router as ai_router
    from routes.api import router as api_router
    from routes.auth import router as auth_router
    from routes.web import router as web_router

    await db.create_all()
    # Framework passkey surface (AUTH_PASSKEYS) rides the auth router,
    # exactly as create_app mounts it — a disabled flag is a no-op.
    from fastplace.auth.passkeys_routes import mount_passkey_routes

    middleware = _middleware_from_config(PROJECT_ROOT)
    return get_app(
        routes=web_router,
        auth_routes=mount_passkey_routes(auth_router),
        api_routes=api_router,
        ai_routes=ai_router,
        middleware=middleware,
        config={"APP_ENV": "local"},
    )


@pytest.fixture()
async def client(app):
    """Browser-playing HTTP client: session + CSRF token rotation included."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        token: list[str | None] = [None]

        async def attach_csrf(request: httpx.Request) -> None:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and token[0]:
                request.headers.setdefault("X-Fastplace-CSRF-Token", token[0])

        async def capture_csrf(response: httpx.Response) -> None:
            fresh = response.headers.get("X-Fastplace-CSRF-Token")
            if fresh:
                token[0] = fresh

        client.event_hooks["request"].append(attach_csrf)
        client.event_hooks["response"].append(capture_csrf)
        yield client


@pytest.fixture()
def user_factory():
    """Create users straight through the repository — no HTTP round trip."""
    from app.modules.accounts.repositories.user_repository import UserRepository

    async def _make(
        *,
        name: str = "Test User",
        email: str = "user@example.test",
        password: str = "secret123",
    ):
        return await UserRepository().create_user(name=name, email=email, password=password)

    return _make
'''

_TESTS_FEATURE_AUTH_TEMPLATE = '''"""Starter auth feature suite — register, login, verify, the settings flash,
and the two-factor enable journey.

Delete or extend freely: every flow here runs against the real application
over a throwaway sqlite database (see tests/conftest.py).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastplace.mail import clear_mail_outbox, mail_outbox

REGISTER_PAYLOAD = {
    "name": "Firoz",
    "email": "firoz@example.com",
    "password": "secret123",
    "password_confirmation": "secret123",
}


async def _register(client) -> None:
    import app.jobs.mail  # noqa: F401  (registers the verification listener)

    await client.get("/")  # mints the session + CSRF token
    response = await client.post("/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 303, response.text


def _verification_path() -> str:
    """The signed /email/verify path+query out of the registration mail."""
    message = mail_outbox()[0]
    match = re.search(r"https?://\\S+/email/verify/\\S+", message.text)
    assert match is not None, message.text
    split = urlsplit(match.group(0))
    return f"{split.path}?{split.query}"


async def test_registration_mails_the_verification_link(client):
    await _register(client)
    outbox = mail_outbox()
    assert outbox[0].to == REGISTER_PAYLOAD["email"]
    assert "/email/verify/" in outbox[0].text


async def test_login_rejects_wrong_credentials(client, user_factory):
    await user_factory(email=REGISTER_PAYLOAD["email"])
    await client.get("/")  # mint the session + CSRF token first
    response = await client.post(
        "/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "wrong-password"},
    )
    assert response.status_code == 422
    assert "These credentials do not match our records." in response.text


async def test_verification_gates_then_opens_the_dashboard(client):
    await _register(client)

    gated = await client.get("/dashboard")
    assert gated.status_code == 302  # unverified accounts cannot reach it yet

    verified = await client.get(_verification_path())
    assert verified.status_code == 303

    dashboard = await client.get("/dashboard")
    assert dashboard.status_code == 200


async def test_resend_after_verification_mails_nothing(client):
    await _register(client)
    await client.get(_verification_path())
    clear_mail_outbox()

    # JSON contract: already verified = no mail, empty 204.
    json_response = await client.post(
        "/email/verification-notification",
        headers={"Accept": "application/json"},
    )
    assert json_response.status_code == 204
    assert mail_outbox() == []


async def test_profile_update_flashes_the_toast(client):
    await _register(client)
    await client.get(_verification_path())

    updated = await client.patch(
        "/settings/profile",
        json={"name": "Renamed", "email": REGISTER_PAYLOAD["email"]},
    )
    assert updated.status_code == 303, updated.text

    page = await client.get(
        "/settings/profile", headers={"X-Fastplace-Request": "true"}
    )
    flash = page.json()["props"].get("flash", {})
    assert flash.get("toast", {}).get("message") == "Profile updated."


async def test_two_factor_enable_journey(client):
    """The full settings/security 2FA loop over HTTP: the management surface
    parks on the password-confirmation wall, the status GET recovers onto
    the security page (no 404), and a pyotp-confirmed enable ends with live
    recovery codes. pyotp is a core framework dependency — no extra install."""
    import pyotp

    await _register(client)
    await client.get(_verification_path())

    # Unconfirmed password: the first enable POST parks on the wall.
    parked = await client.post("/user/two-factor-authentication")
    assert parked.status_code == 302
    assert "/user/confirm-password" in parked.headers["location"]

    confirmed = await client.post(
        "/user/confirm-password",
        json={"password": REGISTER_PAYLOAD["password"]},
    )
    assert confirmed.status_code == 303

    # The recovery GET the frozen frontend issues mid-journey: a browser-ish
    # visitor is walked back to the security page, never a 404.
    status = await client.get("/user/two-factor-authentication")
    assert status.status_code == 303
    assert status.headers["location"].endswith("/settings/security")

    # A JSON API client gets the boolean instead of the walk-back.
    api_status = await client.get(
        "/user/two-factor-authentication", headers={"Accept": "application/json"}
    )
    assert api_status.status_code == 200
    assert api_status.json() == {"enabled": False}

    enabled = await client.post("/user/two-factor-authentication")
    assert enabled.status_code == 200

    secret = (await client.get("/user/two-factor-secret-key")).json()["secretKey"]
    qr = await client.get("/user/two-factor-qr-code")
    assert qr.status_code == 200
    assert "svg" in qr.json()

    code = pyotp.TOTP(secret).now()
    ok = await client.post(
        "/user/confirmed-two-factor-authentication", json={"code": code}
    )
    assert ok.status_code == 200, ok.text

    # Confirmed now: the status flips and the recovery codes are live.
    flipped = await client.get(
        "/user/two-factor-authentication", headers={"Accept": "application/json"}
    )
    assert flipped.json() == {"enabled": True}
    codes = (await client.get("/user/two-factor-recovery-codes")).json()
    assert isinstance(codes, list) and len(codes) > 0
'''

# The single source of truth for the auth scaffold's file surface — shared
# by `fastplace new --auth` and `fastplace make:auth` so the two paths can
# never diverge. (relative path, template text) pairs, in write order.
AUTH_FILES: list[tuple[str, str]] = [
    ("app/modules/accounts/models/user.py", _USER_MODEL_TEMPLATE),
    # The models package must re-export User: config/auth.py points the ORM
    # provider at the dotted path app.modules.accounts.models.User, and an
    # empty __init__ 500s every authenticated request after registration.
    ("app/modules/accounts/models/__init__.py", _ACCOUNTS_MODELS_INIT_TEMPLATE),
    ("app/modules/accounts/repositories/user_repository.py", _USER_REPOSITORY_TEMPLATE),
    ("app/modules/accounts/services/auth_service.py", _AUTH_SERVICE_TEMPLATE),
    ("app/modules/accounts/services/password_policy.py", _PASSWORD_POLICY_TEMPLATE),
    (
        "app/modules/accounts/services/password_reset_service.py",
        _PASSWORD_RESET_SERVICE_TEMPLATE,
    ),
    (
        "app/modules/accounts/services/registration_service.py",
        _REGISTRATION_SERVICE_TEMPLATE,
    ),
    ("app/modules/accounts/services/two_factor_service.py", _TWO_FACTOR_SERVICE_TEMPLATE),
    ("app/modules/accounts/services/user_service.py", _USER_SERVICE_TEMPLATE),
    ("app/modules/accounts/services/verification_service.py", _VERIFICATION_SERVICE_TEMPLATE),
    ("app/http/requests/login_request.py", _LOGIN_REQUEST_TEMPLATE),
    ("app/http/requests/register_request.py", _REGISTER_REQUEST_TEMPLATE),
    ("app/http/requests/confirm_password_request.py", _CONFIRM_PASSWORD_REQUEST_TEMPLATE),
    ("app/http/requests/forgot_password_request.py", _FORGOT_PASSWORD_REQUEST_TEMPLATE),
    ("app/http/requests/reset_password_request.py", _RESET_PASSWORD_REQUEST_TEMPLATE),
    # The settings write surface (§4.14) — the shipped UI posts to these.
    ("app/http/requests/profile_request.py", _PROFILE_REQUEST_TEMPLATE),
    ("app/http/requests/password_update_request.py", _PASSWORD_UPDATE_REQUEST_TEMPLATE),
    ("app/http/requests/delete_profile_request.py", _DELETE_PROFILE_REQUEST_TEMPLATE),
    ("app/http/controllers/auth_api_controller.py", _AUTH_API_CONTROLLER_TEMPLATE),
    ("app/http/controllers/two_factor_api_controller.py", _TWO_FACTOR_API_CONTROLLER_TEMPLATE),
    ("app/http/controllers/settings_api_controller.py", _SETTINGS_API_CONTROLLER_TEMPLATE),
    # The flash bag mapped onto props.flash for the bridge toast loop.
    ("app/http/shared_props.py", _SHARED_PROPS_TEMPLATE),
    ("routes/auth.py", _AUTH_ROUTES_TEMPLATE),
    ("app/auth/gates.py", _GATES_TEMPLATE),
    # The kernel imports app/jobs at boot (import_jobs) — without this file
    # the Registered event has no listener and no verification mail sends.
    ("app/jobs/mail.py", _MAIL_JOB_TEMPLATE),
    # Multipart mail bodies — the transports render both channels.
    ("app/modules/accounts/services/mail_views.py", _MAIL_VIEWS_TEMPLATE),
    # MAIL_* defaults so the transports boot without env archaeology.
    ("config/mail.py", _MAIL_CONFIG_TEMPLATE),
    # A working db:seed example (``fastplace db:seed`` runs every seeder's run()).
    ("database/seeders/database_seeder.py", _DATABASE_SEEDER_TEMPLATE),
    # The emitted test suite — the app boots for real over a throwaway DB.
    ("tests/conftest.py", _TESTS_CONFTEST_TEMPLATE),
    ("tests/feature/test_auth_flow.py", _TESTS_FEATURE_AUTH_TEMPLATE),
    # CI + dependency automation for the generated project.
    (".github/workflows/tests.yml", _CI_WORKFLOW_TEMPLATE),
    (".github/dependabot.yml", _DEPENDABOT_TEMPLATE),
]

# Package markers so pkgutil/import_gates discovery finds the new code —
# and so the emitted `app` is a REGULAR package: a regular package anywhere
# on sys.path beats a namespace portion earlier on it, so without these the
# framework checkout's own app/ would shadow the project's during tests run
# with the checkout on PYTHONPATH.
AUTH_PACKAGE_MARKERS: tuple[str, ...] = (
    "app/__init__.py",
    "app/http/__init__.py",
    "app/modules/__init__.py",
    "app/modules/accounts/__init__.py",
    "app/modules/accounts/repositories/__init__.py",
    "app/modules/accounts/services/__init__.py",
    "app/auth/__init__.py",
    "app/jobs/__init__.py",
)


def _augment_pyproject(root: Path) -> None:
    """Add test tooling, the dev extra, and lint config to pyproject.toml.

    Idempotent and guarded: a pyproject without a dependencies array gains
    only the email-validator line; one without the pytest block gains the
    whole tooling tail; anything already present is left untouched.
    """
    path = root / "pyproject.toml"
    if not path.is_file():
        return
    content = path.read_text()
    if '"email-validator' not in content and "dependencies = [" in content:
        content = content.replace(
            "dependencies = [",
            'dependencies = [\n    "email-validator>=2.0",',
            1,
        )
    if "[tool.pytest.ini_options]" not in content:
        content = content.rstrip("\n") + "\n" + _PYPROJECT_TOOLING_TEMPLATE
    path.write_text(content)


_PYPROJECT_TOOLING_TEMPLATE = """
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
]
# Production database option — SQLite is the zero-config default; switch with
# `pip install -e ".[mysql]"` and point DB_CONNECTION at mysql.
mysql = [
    "asyncmy>=0.2.9",
    "cryptography>=42",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.12"
check_untyped_defs = true
"""


def _augment_env_files(root: Path) -> None:
    """Append the MAIL_* and locale keys to .env / .env.example once."""
    block = (
        "\n# Mail (driver: log | memory | smtp)\n"
        "MAIL_DRIVER=log\n"
        "MAIL_FROM_ADDRESS=hello@example.com\n"
        "MAIL_FROM_NAME=Fastplace\n"
        "MAIL_HOST=127.0.0.1\n"
        "MAIL_PORT=2525\n"
        "MAIL_USERNAME=\n"
        "MAIL_PASSWORD=\n"
        "MAIL_ENCRYPTION=tls\n"
        "\n# Localization\n"
        "APP_LOCALE=en\n"
        "APP_FALLBACK_LOCALE=en\n"
    )
    for name in (".env", ".env.example"):
        path = root / name
        if not path.is_file() or "MAIL_DRIVER" in path.read_text():
            continue
        with path.open("a") as handle:
            handle.write(block)


def _augment_readme(root: Path) -> None:
    """Swap the parked-targets section for the now-real settings docs."""
    path = root / "README.md"
    if not path.is_file():
        return
    content = path.read_text()
    # Guard the EXACT needle the rewrite splices on: a README that merely
    # mentions the phrase (no heading) skips the rewrite instead of raising.
    heading = "## Parked form targets"
    if heading not in content:
        return
    start = content.index(heading)
    # The section runs to the next heading (or the end of the file).
    rest = content[start + len(heading) :]
    next_heading = rest.find("\n## ")
    replacement = (
        "## Settings flows\n\n"
        "The starter ships the settings UI **and** its backends:\n\n"
        "- PATCH `/settings/profile` — rename or change email "
        "(a change re-enters verification)\n"
        "- PUT `/settings/password` — password change, current password "
        "required, throttled\n"
        "- DELETE `/settings/profile` — account deletion, password "
        "confirmation required\n\n"
        "Passkeys are framework-routed too (register/list/delete under "
        "`/user/passkeys`, passwordless sign-in at `/passkeys/login`) "
        "whenever AUTH_PASSKEYS is enabled and your dependency is "
        "'fastplace[queue,webauthn]'.\n\n"
        "Run the emitted test suite (`pytest`) to see every flow exercised."
    )
    content = content[:start] + replacement + ("" if next_heading == -1 else rest[next_heading:])
    path.write_text(content)


def _augment_index_html(root: Path) -> None:
    """Point <head> at the favicon and touch icon the corpus ships."""
    path = root / "index.html"
    if not path.is_file() or 'rel="icon"' in path.read_text():
        return
    content = path.read_text()
    if "</head>" not in content:
        return
    # Match the indentation of the closing </head> line itself.
    close_at = content.index("</head>")
    line_start = content.rfind("\n", 0, close_at) + 1
    indent = content[line_start:close_at]
    links = (
        f'{indent}<link rel="icon" type="image/x-icon" href="/favicon.ico" />\n'
        f'{indent}<link rel="apple-touch-icon" href="/apple-touch-icon.png" />\n'
    )
    path.write_text(content[:line_start] + links + content[line_start:])


def write_auth_surface(root: Path) -> None:
    """Write every auth-scaffold file (non-clobbering) under ``root``."""
    for rel, template in AUTH_FILES:
        _write(root / rel, template, root)
    for rel in AUTH_PACKAGE_MARKERS:
        _write(root / rel, "", root)
    # The base project files (pyproject/.env/README/index.html) already exist
    # by now on both paths — `fastplace new` writes them before installing
    # auth, and make:auth runs inside an existing project.
    _augment_pyproject(root)
    _augment_env_files(root)
    _augment_readme(root)
    _augment_index_html(root)
    # The passkey surface rides the webauthn extra and the mail listener on
    # the queue extra — flip the dependency to carry both and say so. A
    # pyproject without a fastplace dep line skips silently (nothing to
    # rewrite). The ready panel below already carries the one install
    # command, so this notice stays informational only. The brackets in the
    # dep name are escaped so Rich doesn't parse them as markup.
    if _rewrite_fastplace_dep_for_auth_extras(root):
        console.print(
            "[green]Passkeys + mail queue enabled[/] — dependency set to "
            "fastplace\\[queue,webauthn]"
        )


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

    write_auth_surface(root)

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
    console.print("  2. open /register — the FIRST account you create becomes the admin")
    console.print("  3. point AUTH_PROVIDERS at the ORM User (the default in config/auth.py)")
    console.print("  4. tune config/mail.py — the MAIL_* keys are already in .env")
    console.print("  5. set AUTH_SHARED_ABILITIES in config/auth.py for useCan() props")
