"""Account settings writes — profile, password, deletion (spec §4.14).

The settings controllers stay thin; this service owns the rules: the
unique-ignore-self email check, the re-verification pipeline an email
change re-enters, the password policy + device sweep a rotation takes,
and the full-session teardown deletion performs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from app.modules.accounts.models.user import User
from app.modules.accounts.repositories.user_repository import UserRepository
from app.modules.accounts.services.password_policy import min_password_length
from app.modules.accounts.services.verification_service import VerificationService
from fastplace.auth.guards import SESSION_STORE_SCOPE, guard
from fastplace.auth.hashing import Hash
from fastplace.errors import ValidationError


class SettingsService:
    """The one door to account-settings mutations from a controller."""

    def __init__(self) -> None:
        self.users = UserRepository()
        self.verification_service = VerificationService()

    async def email_in_use(self, email: str, *, excluding_id: object = None) -> bool:
        """True when ANOTHER account already holds the email.

        The caller's own row is never a conflict (profile-update
        semantics); excluding_id keeps the check honest across it.
        """
        existing = await self.users.find_by_email(email)
        return existing is not None and str(existing.id) != str(excluding_id)

    async def update_profile(self, user: User, data: dict[str, Any]) -> bool:
        """Apply name/email. True when the email changed (mail was sent).

        A new address is unverified until its link is clicked — the
        verification pipeline re-runs exactly like a fresh signup.
        """
        email = str(data["email"]).strip().lower()
        if await self.email_in_use(email, excluding_id=user.id):
            raise ValidationError(errors={"email": ["The email has already been taken."]})

        if email != str(user.email).strip().lower():
            try:
                await user.update(name=data["name"], email=email, email_verified_at=None)
            except IntegrityError:
                # The find-first above can lose a race to a concurrent
                # registration; the UNIQUE constraint settles it with the
                # same friendly 422 instead of a 500.
                raise ValidationError(
                    errors={"email": ["The email has already been taken."]}
                ) from None
            await self.verification_service.send_link(user.id, email)
            return True
        await user.update(name=data["name"])
        return False

    async def update_password(self, request: Any, user: User, data: dict[str, Any]) -> None:
        """Rotate the password after credential + policy checks.

        A rotated password must kill every credential a thief already
        holds: the guard sweeps the OTHER sessions (the current one keeps
        its payload) and rotates the remember token — the same posture
        the password-reset flow takes; regenerate() retires this
        session's old ID on the next persist, so a copied cookie dies.
        """
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
        await guard().logout_other_devices(request, data["current_password"])
        request.session.regenerate()

    async def delete_account(self, request: Any, user: User, password: str) -> None:
        """Destroy the account after a password check, sessions first."""
        if not await guard().provider.validate_credentials(user, {"password": password}):
            raise ValidationError(errors={"password": ["The password is incorrect."]})

        store = request.scope.get(SESSION_STORE_SCOPE)
        if store is not None:
            await store.destroy_for_user(user.id)  # every device, not just this one
        await guard().logout(request)  # expires this session's backing row + cookie
        # force_delete, NOT delete(): the base model stamps deleted_at, and a
        # tombstone keeps the unique email locked — re-registering it would
        # hit the constraint and 500.
        await user.force_delete()
