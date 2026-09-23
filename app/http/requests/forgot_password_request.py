"""Forgot-password form request — one field, nothing else accepted."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
