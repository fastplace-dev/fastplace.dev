"""Profile-update form request — field-level validation only.

The unique-ignore-self rule runs in SettingsService, not here: pydantic
cannot see the database. The email shape check mirrors the app's other
requests (plain fields, no email-validator dependency — its reserved-TLD
list would reject this app's @example.test fixtures).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: object) -> object:
        # Whitespace is never a name: strip BEFORE the length constraints
        # run, so a whitespace-only submission fails the required check.
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _must_be_an_email_address(cls, value: str) -> str:
        local, _, domain = value.partition("@")
        if not local or "." not in domain:
            raise ValueError("The email field must be a valid email address.")
        return value
