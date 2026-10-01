"""Password-change form request — the policy checks run in the service."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PasswordUpdateRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    password_confirmation: str = Field(min_length=1, max_length=255)
