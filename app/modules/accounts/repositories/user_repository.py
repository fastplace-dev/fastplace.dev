"""User data access — the accounts module's repository layer."""

from __future__ import annotations

from app.modules.accounts.models.user import User
from fastplace.auth.hashing import Hash


class UserRepository:
    """Owns every User query the auth services issue."""

    async def find_by_email(self, email: str) -> User | None:
        return await User.where(User.email == email).first()

    async def create_user(self, *, name: str, email: str, password: str) -> User:
        return await User.create(name=name, email=email, password_hash=Hash.make(password))
