"""Email verification — APP_KEY-signed links carried by the mail subsystem (§4.11)."""

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
