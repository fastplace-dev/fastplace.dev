"""DatabaseManager — async engines, pooling, named connections."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fastplace.orm.capabilities import Capabilities, driver_from_url

logger = logging.getLogger("fastplace.db")

_DEFAULT_CONNECTIONS: dict[str, dict[str, Any]] = {
    "default": {
        "driver": "sqlite",
        "url": "sqlite+aiosqlite:///./database.sqlite3",
    }
}

_POOL_KEYS = ("POOL_SIZE", "MAX_OVERFLOW", "POOL_TIMEOUT", "POOL_RECYCLE", "ECHO")

_manager: DatabaseManager | None = None


def get_manager() -> DatabaseManager:
    """Process-wide manager, configured from ``config/database.py`` + ``.env``."""
    global _manager
    if _manager is None:
        _manager = DatabaseManager(_connections_from_config())
    return _manager


def reset_manager() -> None:
    """Drop the singleton (tests and reconfiguration), disposing its engines.

    Best-effort synchronous disposal: async callers should prefer
    ``await db.dispose()``. A blocking sync dispose still frees the pooled
    sqlite connections (and their worker threads) without waiting for GC.
    """
    global _manager
    if _manager is not None:
        _manager._dispose_sync()
    _manager = None


def _connections_from_config() -> dict[str, dict[str, Any]]:
    from fastplace.config import config

    connections: dict[str, dict[str, Any]] = {}

    # Named connections (blueprint §8): declare in config/database.py —
    # DATABASE_CONNECTIONS = {"analytics": {"url": ..., "pool_size": 5}}
    named = config("DATABASE_CONNECTIONS", default=None)
    if isinstance(named, dict):
        for cname, spec in named.items():
            if not isinstance(spec, dict) or "url" not in spec:
                raise ValueError(
                    f"DATABASE_CONNECTIONS[{cname!r}] must be a dict with at least a 'url' entry"
                )
            entry: dict[str, Any] = {
                "driver": spec.get("driver") or driver_from_url(str(spec["url"])),
                "url": spec["url"],
            }
            for key in _POOL_KEYS:
                if key.lower() in spec:
                    entry[key.lower()] = spec[key.lower()]
            connections[str(cname)] = entry

    url = config("DATABASE_URL", default=None)
    driver = config("DATABASE_DRIVER", default=None)
    if url or driver:
        url = url or "sqlite+aiosqlite:///./database.sqlite3"
        default_conn: dict[str, Any] = {
            "driver": driver or driver_from_url(url),
            "url": url,
        }
        for key in _POOL_KEYS:
            value = config(f"DATABASE_{key}", default=None)
            if value is not None:
                default_conn[key.lower()] = value
        connections["default"] = default_conn

    if not connections:
        return dict(_DEFAULT_CONNECTIONS)
    connections.setdefault(
        "default",
        dict(_DEFAULT_CONNECTIONS["default"]),
    )
    return connections


class DatabaseManager:
    """Owns engine creation, pooling, and named connections (blueprint §8)."""

    def __init__(self, connections: dict[str, dict[str, Any]]) -> None:
        self.connections = connections
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}

    def config_for(self, name: str = "default") -> dict[str, Any]:
        if name not in self.connections:
            raise KeyError(
                f"Unknown database connection '{name}'. Configured: {sorted(self.connections)}"
            )
        return self.connections[name]

    def engine(self, name: str = "default") -> AsyncEngine:
        if name not in self._engines:
            cfg = self.config_for(name)
            self._engines[name] = self._create_engine(cfg)
        return self._engines[name]

    def _create_engine(self, cfg: dict[str, Any]) -> AsyncEngine:
        url: str = cfg["url"]
        kwargs: dict[str, Any] = {"echo": bool(cfg.get("echo", False))}

        if url.startswith("sqlite"):
            if ":memory:" in url or url.endswith("://") or url.endswith(":///:memory:"):
                # One shared connection so the in-memory schema persists, but
                # checked out one at a time: concurrent scopes queue instead of
                # interleaving transactions on a single sqlite connection
                # (StaticPool would hand the same connection to both at once).
                from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

                kwargs["poolclass"] = AsyncAdaptedQueuePool
                kwargs["pool_size"] = 1
                kwargs["max_overflow"] = 0
        else:
            for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
                if key in cfg:
                    kwargs[key] = int(cfg[key])

        return create_async_engine(url, **kwargs)

    def session_factory(self, name: str = "default") -> async_sessionmaker[AsyncSession]:
        if name not in self._session_factories:
            self._session_factories[name] = async_sessionmaker(
                self.engine(name),
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._session_factories[name]

    def session(self, name: str = "default") -> AsyncSession:
        return self.session_factory(name)()

    def capabilities(self, name: str = "default") -> Capabilities:
        cfg = self.config_for(name)
        driver = cfg.get("driver") or driver_from_url(cfg["url"])
        return Capabilities(driver)

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()
        self._session_factories.clear()

    def _dispose_sync(self) -> None:
        """Best-effort engine disposal from sync contexts (test teardown).

        aiosqlite connections can only be closed from a running event loop;
        when no loop is running we spin a private one for the async dispose.
        """
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(self.dispose())
            except Exception:  # noqa: BLE001 — teardown must never raise
                logger.debug("async dispose during reset failed", exc_info=True)
            return

        # Called from inside a running loop (discouraged): drop references and
        # warn — the caller should have used `await db.dispose()`.
        logger.warning(
            "reset_manager() called inside a running event loop; engines were "
            "dropped without disposal — prefer `await db.dispose()`."
        )
        self._engines.clear()
        self._session_factories.clear()
