"""Password reset — forgot-password mail and token-gated redemption (§4.10)."""

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
from fastplace.auth.tokens import pat_store
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
        await pat_store().revoke_all_for_user(user.id)  # spec §6: reset kills tokens too
        store = request.scope.get(SESSION_STORE_SCOPE)
        if store is not None:
            await store.destroy_for_user(user.id)  # every session — no except_session_id
        await dispatch(DomainEvent("PasswordReset", {"user_id": user.id, "email": user.email}))
