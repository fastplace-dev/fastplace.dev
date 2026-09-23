"""Password reset — forgot-password mail and token-gated redemption (§4.10)."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import quote

from app.modules.accounts.repositories.user_repository import UserRepository
from fastplace.auth.hashing import Hash
from fastplace.auth.passwords import _dummy_digest, throttle_seconds, token_store
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
        """Redeem a reset token: validate, burn, rehash, revoke, announce.

        Shipped in Task 11 (declared here so the service lands complete).
        """
        raise NotImplementedError  # replaced in Task 11
