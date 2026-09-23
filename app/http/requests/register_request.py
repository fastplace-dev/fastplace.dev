"""Registration form request — field-level validation only.

The password_confirmation mismatch is adjudicated in RegistrationService
(a pydantic model_validator would surface the error at loc=(), which the
frontend cannot map to a field).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
