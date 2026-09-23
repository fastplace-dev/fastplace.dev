"""Mobile PAT issuance request — email + password + device_name (spec §4.14)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MobileTokenRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    device_name: str = Field(min_length=1, max_length=255)
