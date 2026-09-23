"""PAT issue request — the management endpoint's body (spec §4.14)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class StoreTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    abilities: list[str] = Field(default_factory=lambda: ["*"])
    expires_at: datetime.datetime | None = None
