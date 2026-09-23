"""Two-factor orchestration — challenge fulfillment + settings management.

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
