"""PasskeyGuard — passkeys as an authentication METHOD over SessionGuard.

Not a new guard driver: a successful login assertion resolves the user
through the existing provider and establishes identity via the existing
SessionGuard.login(), so session regeneration, remember-me, and two-factor
interplay stay exactly as they are. A passkey replaces the password
factor — never the second factor.

Ceremonies share one error surface: every verification failure raises the
same generic ValidationError, with no signal of why (enumeration defense).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from sqlalchemy.exc import IntegrityError

from fastplace.auth.guards import (
    TWO_FACTOR_CHALLENGE_KEY,
    TWO_FACTOR_REMEMBER_KEY,
    SessionGuard,
    guard,
)
from fastplace.auth.passkeys import PasskeyStore, passkey_store
from fastplace.auth.providers import user_identifier
from fastplace.auth.webauthn import (
    ChallengeStore,
    PasskeyConfig,
    assertion_options,
    registration_options,
    verify_assertion,
    verify_registration,
)
from fastplace.errors import ConfigurationError, ThrottleRequestsError, ValidationError
from fastplace.events import DomainEvent, dispatch
from fastplace.ratelimit import RateLimiter

VERIFY_FAILED = "Unable to verify this passkey."
PASSWORD_CONFIRMED_KEY = "password_confirmed_at"


def _verify_failed() -> ValidationError:
    return ValidationError(errors={"credential": [VERIFY_FAILED]})


def _login_keys(request: Any, credential: Any) -> tuple[str, str]:
    """Throttle keys for one login attempt: narrowed bucket + IP backstop.

    Keying the lockout on the socket IP alone hands any attacker a switch
    that turns passkey login off for every user behind a shared egress IP
    (reverse proxy, carrier CGNAT): mint a guest session, POST garbage
    until the bucket fills, and the whole IP is locked — repeatable
    forever. Folding the presented credential id into the primary key
    (sha1, mirroring the password-login key) narrows the lockout to
    credentials the attacker can already name. The second key is a loose
    per-IP counter at a much higher ceiling, so wide scans rotating fresh
    credential ids still hit a wall.
    """
    ip = getattr(request, "ip", None) or ""
    credential_id = None
    if isinstance(credential, dict):
        candidate = credential.get("id")
        if isinstance(candidate, str):
            credential_id = candidate
    narrowed = hashlib.sha1(f"{credential_id or ''}|{ip}".encode()).hexdigest()
    return f"passkey-login:{narrowed}", f"passkey-login-ip:{ip}"


def _ip_backstop_max(config: PasskeyConfig) -> int:
    """Loose per-IP ceiling — an order of magnitude above one credential's lock."""
    return config.login_max_attempts * 10


class PasskeyGuard:
    """Manage (register/list/delete), usernameless login, and the
    password-confirmation alternative — all over the session guard."""

    def __init__(
        self,
        store: PasskeyStore | None = None,
        config: PasskeyConfig | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._store = store or passkey_store()
        self._config = config
        self._limiter = limiter or RateLimiter()

    async def _config_or_raise(self) -> PasskeyConfig:
        if self._config is None:
            self._config = PasskeyConfig.from_config()
        return self._config

    def _challenges(self, request: Any, config: PasskeyConfig) -> ChallengeStore:
        return ChallengeStore(request, ttl=config.challenge_ttl)

    # -- manage ---------------------------------------------------------

    async def registration_options(self, request: Any, user: Any) -> dict[str, Any]:
        config = await self._config_or_raise()
        existing = [row.credential_id for row in await self._store.rows_for(user_identifier(user))]
        payload = registration_options(config, user, exclude_ids=existing)
        self._challenges(request, config).issue("register", payload["challenge"])
        return payload["options"]

    async def register(self, request: Any, user: Any, name: str, credential: dict) -> int:
        config = await self._config_or_raise()
        challenge = self._challenges(request, config).consume("register")
        identifier = user_identifier(user)
        if not challenge:
            raise _verify_failed()
        try:
            material = verify_registration(config, credential, challenge)
        except Exception:
            raise _verify_failed() from None
        try:
            row_id = await self._store.create(identifier, name.strip() or "Passkey", material)
        except IntegrityError:  # unique(credential_id) — concurrent double-register
            # The driver's own text varies ("Duplicate entry" on MySQL), so
            # the exception type is the only portable race signal.
            raise ValidationError(
                errors={"credential": ["That passkey is already registered."]}
            ) from None
        await dispatch(
            DomainEvent("passkey.registered", {"user_id": identifier, "id": row_id, "name": name})
        )
        return row_id

    async def list_for(self, user: Any) -> list[dict[str, Any]]:
        return await self._store.list_for(user_identifier(user))

    async def delete(self, request: Any, user: Any, credential_id: int) -> bool:
        identifier = user_identifier(user)
        removed = await self._store.delete(identifier, credential_id)
        if removed:
            await dispatch(
                DomainEvent("passkey.deleted", {"user_id": identifier, "id": credential_id})
            )
        return removed

    # -- login ----------------------------------------------------------

    async def login_options(self, request: Any) -> dict[str, Any]:
        config = await self._config_or_raise()
        payload = assertion_options(config, allow_ids=[])  # discoverable credentials
        self._challenges(request, config).issue("login", payload["challenge"])
        return payload["options"]

    async def login(self, request: Any, credential: dict) -> Any | None:
        """The user when fully logged in; None when a 2FA challenge parked."""
        config = await self._config_or_raise()
        narrow_key, ip_key = _login_keys(request, credential)
        for key, ceiling in (
            (narrow_key, config.login_max_attempts),
            (ip_key, _ip_backstop_max(config)),
        ):
            if await self._limiter.too_many_attempts(key, ceiling):
                retry_after = max(1, await self._limiter.available_in(key))
                raise ThrottleRequestsError(retry_after=retry_after)
        await self._limiter.hit(narrow_key, decay=_login_decay())
        await self._limiter.hit(ip_key, decay=_login_decay())

        challenge = self._challenges(request, config).consume("login")
        row = None
        if challenge is not None:
            credential_id = credential.get("id")
            if isinstance(credential_id, str):
                row = await self._store.get_by_credential_id(credential_id)
        if challenge is None or row is None:
            raise _verify_failed()
        try:
            verified = verify_assertion(
                config,
                credential,
                challenge,
                stored_public_key=row.public_key,
                stored_sign_count=int(row.sign_count),
                require_uv=config.user_verification == "required",
            )
        except Exception:
            raise _verify_failed() from None
        if row.sign_count > 0 and verified.new_sign_count <= int(row.sign_count):
            raise _verify_failed()  # counter never advanced — possible clone

        # Resolve through the SAME provider the session guard uses, so a
        # custom provider in config resolves passkey logins identically.
        # Passkeys establish sessions — a token-only default guard is a
        # configuration error, not a silent skip.
        session_guard = guard()
        if not isinstance(session_guard, SessionGuard):
            raise ConfigurationError(
                "Passkey login requires the session guard driver — passkeys "
                "establish server-side sessions, not tokens."
            )
        user = await session_guard.provider.resolve(row.user_id)
        if user is None:
            raise _verify_failed()

        await self._store.touch(row.credential_id, verified.new_sign_count, verified.backup_state)

        # A CONFIRMED two-factor user stops short of login — the passkey
        # replaced the password factor only; park the challenge exactly as
        # SessionGuard.attempt() does and let the caller steer to the
        # challenge page.
        if getattr(user, "two_factor_confirmed_at", None) is not None:
            request.session[TWO_FACTOR_CHALLENGE_KEY] = user_identifier(user)
            request.session[TWO_FACTOR_REMEMBER_KEY] = False
            await self._limiter.clear(narrow_key)
            await self._limiter.clear(ip_key)
            # No passkey.login event here: it marks session establishment,
            # and the parked handoff has none yet — mirroring the password
            # path, where Login waits for the TOTP factor too.
            return None

        await session_guard.login(request, user)
        # Success forgives the failure counts — both buckets.
        await self._limiter.clear(narrow_key)
        await self._limiter.clear(ip_key)
        await dispatch(DomainEvent("passkey.login", {"user_id": user_identifier(user)}))
        return user

    # -- confirm ----------------------------------------------------------

    async def confirm_options(self, request: Any, user: Any) -> dict[str, Any]:
        config = await self._config_or_raise()
        existing = [row.credential_id for row in await self._store.rows_for(user_identifier(user))]
        payload = assertion_options(config, allow_ids=existing)
        self._challenges(request, config).issue("confirm", payload["challenge"])
        return payload["options"]

    async def confirm(self, request: Any, user: Any, credential: dict) -> bool:
        """Prove identity with a passkey; stamps ``password_confirmed_at``.

        UV is ALWAYS required here regardless of config — a confirmation
        must prove a biometric/PIN presence, not mere presence.
        """
        config = await self._config_or_raise()
        challenge = self._challenges(request, config).consume("confirm")
        identifier = user_identifier(user)
        row = None
        if challenge is not None:
            credential_id = credential.get("id")
            if isinstance(credential_id, str):
                candidate = await self._store.get_by_credential_id(credential_id)
                if candidate is not None and candidate.user_id == identifier:
                    row = candidate  # owner-scoped — another user's id misses
        if challenge is None or row is None:
            raise _verify_failed()
        try:
            verified = verify_assertion(
                config,
                credential,
                challenge,
                stored_public_key=row.public_key,
                stored_sign_count=int(row.sign_count),
                require_uv=True,
            )
        except Exception:
            raise _verify_failed() from None
        if row.sign_count > 0 and verified.new_sign_count <= int(row.sign_count):
            raise _verify_failed()
        await self._store.touch(row.credential_id, verified.new_sign_count, verified.backup_state)
        request.session[PASSWORD_CONFIRMED_KEY] = int(time.time())
        await dispatch(DomainEvent("passkey.confirm", {"user_id": identifier, "id": row.id}))
        return True


_passkey_guard_instance: PasskeyGuard | None = None


def passkey_guard() -> PasskeyGuard:
    """Process singleton, mirroring ``guard()``/``passkey_store()``."""
    global _passkey_guard_instance
    if _passkey_guard_instance is None:
        _passkey_guard_instance = PasskeyGuard()
    return _passkey_guard_instance


def _login_decay() -> int:
    from fastplace.config import config

    return int(config("AUTH_LOGIN_DECAY", default=60) or 60)
