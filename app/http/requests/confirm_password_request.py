"""Confirm-password form request — the single field, nothing else.

No min_length: a missing/empty password must reach the service, which owns
the frozen "The password field is required." contract string (R12) — the
framework's validation layer has no translation for it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConfirmPasswordRequest(BaseModel):
    password: str = Field(default="", max_length=255)
