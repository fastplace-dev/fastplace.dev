"""Cache isolation — ``company:{id}:`` prefixed keys over any CacheStore.

Wrap the application's store once (composition, not a subclass — the store
 underneath can be Redis, memory, or anything implementing the protocol)::

    cache = CompanyCacheStore(existing_store)

Keys are prefixed with the bound company; without a bound company every
operation fails closed. ``flush()`` is refused outright: a company-scoped
wrapper cannot know what else lives in the shared physical store, and
"forget everyone's cache" must never be one tenant's side effect.
"""

from __future__ import annotations

from typing import Any

from fastplace_tenancy.context import require_company_context


class CompanyCacheStore:
    """A CacheStore whose keys are namespaced to the bound company."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def _key(self, key: str) -> str:
        company_id = require_company_context()
        return f"company:{company_id}:{key}"

    async def get(self, key: str) -> Any:
        return await self._inner.get(self._key(key))

    async def put(self, key: str, value: Any, ttl: int | float | None = None) -> None:
        await self._inner.put(self._key(key), value, ttl)

    async def forget(self, key: str) -> None:
        await self._inner.forget(self._key(key))

    async def remember(
        self,
        key: str,
        ttl: int | float | None = None,
        factory: Any = lambda: None,
    ) -> Any:
        # Same optionality as the CacheStore protocol — remember(key) is a
        # legal call and must stay one through the wrapper.
        return await self._inner.remember(self._key(key), ttl, factory)

    async def increment(self, key: str, ttl: int | float | None = None) -> int:
        return await self._inner.increment(self._key(key), ttl)

    async def ttl(self, key: str) -> float | None:
        return await self._inner.ttl(self._key(key))

    def lock(self, name: str, *, ttl: int | float) -> Any:
        """A lock whose physical name carries the company prefix — two
        tenants locking the same logical name never contend with each other."""
        return self._inner.lock(self._key(name), ttl=ttl)

    async def flush(self) -> None:
        raise NotImplementedError(
            "flush() is refused on a company-scoped store — it would clear "
            "every tenant's entries in the shared physical store; flush the "
            "underlying store explicitly if that is truly intended"
        )
