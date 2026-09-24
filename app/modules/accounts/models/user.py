"""User — the account entity behind every guard (spec §5)."""

from __future__ import annotations

import datetime

from fastplace.orm import Field, Model


class User(Model):
    __tablename__ = "users"

    # Never serialized into page props or JSON responses — to_dict() honors
    # __hidden__, so credential material cannot leak through render(). The
    # two-factor columns are encrypted at rest AND hidden from serialization.
    __hidden__ = {
        "password_hash",
        "two_factor_secret",
        "two_factor_recovery_codes",
        "two_factor_accepted_step",
    }
    __fillable__ = {"name", "email", "password_hash", "email_verified_at"}

    id: int = Field(primary_key=True)
    name: str = Field(default="")
    email: str = Field(unique=True, index=True)
    password_hash: str = Field(default="")
    email_verified_at: datetime.datetime | None = None

    # Encrypted at rest (fastplace.auth.encryption) — Text, not VARCHAR(255):
    # the sealed recovery-code JSON runs past 255 characters.
    two_factor_secret: str | None = Field(text=True, default=None)
    two_factor_recovery_codes: str | None = Field(text=True, default=None)
    two_factor_confirmed_at: datetime.datetime | None = None
    # Last TOTP timestep accepted for this user — the single-use high-water
    # mark. A plain counter, not credential material: it never needs the
    # encryption the secret/codes columns carry, but it stays out of
    # serialized payloads alongside them.
    two_factor_accepted_step: int | None = None
