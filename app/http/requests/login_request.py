"""Login form request — field-level validation only (no cross-field rules)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    remember: str = Field(default="", max_length=8)
