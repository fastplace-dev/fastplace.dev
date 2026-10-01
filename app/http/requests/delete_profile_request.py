"""Account-deletion form request — the posted password is the confirmation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DeleteProfileRequest(BaseModel):
    password: str = Field(min_length=1, max_length=255)
