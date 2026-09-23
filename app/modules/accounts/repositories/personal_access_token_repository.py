"""Personal-access-token data access (spec §4.14).

Minting, authentication, revocation, and pruning delegate to the framework
store (fastplace.auth.tokens) — ONE implementation of the hash/compare
lifecycle, the remember_store() precedent. The ORM model serves app-facing
reads (listing, prefix lookup). Revocation is a HARD delete: the guard
authenticates through the Core store, which ignores deleted_at — a
soft-deleted row would still authenticate.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import String, cast

from app.modules.accounts.models.personal_access_token import PersonalAccessToken


class PersonalAccessTokenRepository:
    async def create(
        self,
        user_id: Any,
        name: str,
        *,
        abilities: list[str] | None = None,
        expires_at: datetime.datetime | None = None,
    ) -> str:
        """Mint a token; the plaintext ``{id}|{secret}`` is shown exactly once."""
        from fastplace.auth.tokens import create_token

        return await create_token(user_id, name, abilities=abilities, expires_at=expires_at)

    async def find_by_id_prefix(self, user_id: Any, prefix: str) -> list[PersonalAccessToken]:
        """Tokens whose id starts with ``prefix`` — user-scoped, indexed."""
        if not prefix:
            return []
        return await PersonalAccessToken.where(
            PersonalAccessToken.user_id == user_id,
            cast(PersonalAccessToken.id, String).like(f"{prefix}%"),
        ).get()

    async def revoke(self, user_id: Any, token_id: Any) -> bool:
        from fastplace.auth.tokens import pat_store

        return await pat_store().revoke(token_id, user_id)

    async def revoke_all_for_user(self, user_id: Any) -> int:
        from fastplace.auth.tokens import pat_store

        return await pat_store().revoke_all_for_user(user_id)

    async def prune_expired(self) -> int:
        from fastplace.auth.tokens import pat_store

        return await pat_store().prune_expired()

    async def touch_last_used(self, token_id: Any) -> None:
        from fastplace.auth.tokens import pat_store

        await pat_store().touch_last_used(token_id)

    async def _row(self, selector: str) -> PersonalAccessToken | None:
        """Test-support: the raw row behind an id (soft deletes included)."""
        return (
            await PersonalAccessToken.with_deleted()
            .where(PersonalAccessToken.id == int(selector))
            .first()
        )
