"""Reset-password form request — password policy runs in the service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResetPasswordRequest(BaseModel):
    # password min 1 here so the length policy (min_password_length) runs in
    # the service, where the token is NOT yet consumed.
    token: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
