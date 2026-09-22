"""User — the account entity behind every guard (spec §5)."""

from __future__ import annotations

import datetime

from fastplace.orm import Field, Model


class User(Model):
    __tablename__ = "users"

    # Never serialized into page props or JSON responses — to_dict() honors
    # __hidden__, so credential material cannot leak through render().
    __hidden__ = {"password_hash"}
    __fillable__ = {"name", "email", "password_hash", "email_verified_at"}

    id: int = Field(primary_key=True)
    name: str = Field(default="")
    email: str = Field(unique=True, index=True)
    password_hash: str = Field(default="")
    email_verified_at: datetime.datetime | None = None
