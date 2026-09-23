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
from fastplace.auth.two_factor import verify_code
from fastplace.errors import ValidationError


class TwoFactorService:
    """Challenge + management flows over the encrypted users columns."""

    INVALID_CODE_MESSAGE = "The provided two factor authentication code is invalid."

    users = UserRepository()

    def _invalid(self, field: str = "code") -> ValidationError:
        return ValidationError(errors={field: [self.INVALID_CODE_MESSAGE]})

    async def verify_challenge(
        self, request: Any, *, code: str, recovery_code: str
    ) -> bool | None:
        """Fulfill a parked challenge. None = no challenge; False = wrong code."""
        challenge_user = request.session.get(TWO_FACTOR_CHALLENGE_KEY)
        if challenge_user is None:
            return None
        user = await self.users.find_by_id(challenge_user)
        if (
            user is None
            or user.two_factor_secret is None
            or user.two_factor_confirmed_at is None
        ):
            return False

        ok = False
        if code:
            ok = verify_code(decrypt(user.two_factor_secret), code)
        elif recovery_code and user.two_factor_recovery_codes:
            stored = json.loads(decrypt(user.two_factor_recovery_codes))
            match = next(
                (c for c in stored if hmac.compare_digest(c, recovery_code)), None
            )
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
