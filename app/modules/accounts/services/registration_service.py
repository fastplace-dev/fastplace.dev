"""Account registration — validation, creation, the Registered event."""

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
