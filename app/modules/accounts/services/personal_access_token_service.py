"""PAT issuance and revocation (spec §4.14) — thin service over the store."""

from __future__ import annotations

import datetime
from typing import Any

from app.modules.accounts.repositories.personal_access_token_repository import (
    PersonalAccessTokenRepository,
)
from fastplace.auth.guards import guard
from fastplace.auth.hashing import Hash
from fastplace.auth.tokens import VIA_PAT_SCOPE, _ensure_aware
from fastplace.errors import AuthorizationError, NotFoundError, ValidationError
from fastplace.events import DomainEvent, dispatch

UTC = datetime.UTC


class PersonalAccessTokenService:
    INVALID_CREDENTIALS = "These credentials do not match our records."
    TWO_FACTOR_REQUIRED = "Two-factor authentication is enabled on this account."
    EXPIRY_MESSAGE = "The expiry date must be in the future."
    NOT_FOUND = "No such token."
    SESSION_ONLY = "Personal access tokens may only be managed from a browser session."

    def __init__(self, repository: PersonalAccessTokenRepository | None = None) -> None:
        self.repository = repository if repository is not None else PersonalAccessTokenRepository()

    def _require_session_edge(self, request: Any) -> None:
        """Token management is session-only (audit T2).

        A PAT — however narrowly scoped — must never be able to mint
        further tokens (privilege escalation to "*") or revoke the
        account's other credentials. The token guard stamps
        VIA_PAT_SCOPE; refuse that edge outright.
        """
        if request.scope.get(VIA_PAT_SCOPE):
            raise AuthorizationError(self.SESSION_ONLY)

    async def issue(self, request: Any, data: dict[str, Any]) -> dict[str, Any]:
        """Mint a PAT for the authenticated user; plaintext shown once."""
        self._require_session_edge(request)
        name = str(data.get("name"))
        abilities = [str(a) for a in (data.get("abilities") or ["*"])]
        expires_at = data.get("expires_at")
        if expires_at is not None and _ensure_aware(expires_at) <= datetime.datetime.now(UTC):
            raise ValidationError(errors={"expires_at": [self.EXPIRY_MESSAGE]})
        user_id = request.auth_id
        token = await self.repository.create(
            user_id, name, abilities=abilities, expires_at=expires_at
        )
        await dispatch(DomainEvent("TokenIssued", {"user_id": user_id, "name": name}))
        return {
            "token": token,
            "name": name,
            "abilities": abilities,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
        }

    async def issue_mobile(self, request: Any, data: dict[str, Any]) -> dict[str, Any]:
        """email+password issuance for machine clients (spec §4.14).

        Generic frozen error on any credential miss (enumeration-safe, spec
        §6); a CONFIRMED two-factor account is refused outright — a password
        alone must never defeat the §4.13 challenge on the API edge.
        """
        credentials = {"email": data["email"], "password": data["password"]}
        session_guard = guard()
        user = await session_guard.provider.retrieve_by_credentials(credentials)
        if user is None:
            # Unknown email pays the same scrypt cost a wrong password pays
            # (timing parity, the password-reset precedent).
            from fastplace.auth.passwords import _dummy_digest

            Hash.check(str(credentials["password"]), _dummy_digest())
            raise ValidationError(errors={"email": [self.INVALID_CREDENTIALS]})
        ok = await session_guard.provider.validate_credentials(user, credentials)
        if not ok:
            raise ValidationError(errors={"email": [self.INVALID_CREDENTIALS]})
        if getattr(user, "two_factor_confirmed_at", None) is not None:
            raise ValidationError(errors={"email": [self.TWO_FACTOR_REQUIRED]})
        token = await self.repository.create(user.id, str(data["device_name"]))
        await dispatch(
            DomainEvent("TokenIssued", {"user_id": user.id, "name": str(data["device_name"])})
        )
        return {"token": token, "name": str(data["device_name"])}

    async def revoke(self, request: Any, token_id: Any) -> None:
        """Owner-scoped revoke — unknown id and foreign id are both a 404."""
        self._require_session_edge(request)
        raw = str(token_id or "").strip()
        if not raw.isdigit():
            raise NotFoundError(self.NOT_FOUND)
        removed = await self.repository.revoke(request.auth_id, int(raw))
        if not removed:
            raise NotFoundError(self.NOT_FOUND)
        await dispatch(
            DomainEvent("TokenRevoked", {"user_id": request.auth_id, "token_id": int(raw)})
        )
