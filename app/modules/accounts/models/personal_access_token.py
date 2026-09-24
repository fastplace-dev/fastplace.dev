"""PersonalAccessToken — API tokens, hashed at rest (spec §4.14/§5)."""

from __future__ import annotations

import datetime

from fastplace.orm import Field, Model


class PersonalAccessToken(Model):
    __tablename__ = "personal_access_tokens"

    # token_hash is credential material — it must never serialize into page
    # props or JSON responses (to_dict() honors __hidden__).
    __hidden__ = {"token_hash"}
    __fillable__ = {"user_id", "name", "abilities", "expires_at"}

    id: int = Field(primary_key=True)
    user_id: int = Field(index=True)
    name: str = Field(default="")
    token_hash: str = Field(unique=True)
    abilities: list[str] | None = None
    last_used_at: datetime.datetime | None = None
    expires_at: datetime.datetime | None = None
