"""DatabaseManager — async engines, pooling, named connections."""

from __future__ import annotations

import itertools
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


def _parse_replica_urls(raw: Any) -> list[str]:
    """Replica URLs from config — a list, JSON array string, or CSV string."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(url) for url in raw]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        import json

        return [str(url) for url in json.loads(text)]
    return [part.strip() for part in text.split(",") if part.strip()]


_manager: DatabaseManager | None = None

#: Bare scheme → async dialect of record (blueprint §8). Developers write
#: ``mysql://`` / ``postgresql://``; the engine layer gets the driver bound.
_ASYNC_DRIVER_BINDINGS = {
    "mysql": "mysql+asyncmy",
    "mariadb": "mysql+asyncmy",
    "postgresql": "postgresql+asyncpg",
    "postgres": "postgresql+asyncpg",
    "sqlite": "sqlite+aiosqlite",
}


def normalize_database_url(url: str) -> str:
    """Bind a bare scheme to the framework's async driver for it.

    ``mysql://…`` becomes ``mysql+asyncmy://…`` and friends; URLs that
    already name a driver pass through untouched, and schemes with no
    relational binding (``mongodb://`` lives behind the document adapter)
    are left exactly as written.
    """
    scheme, separator, rest = url.partition("://")
    if not separator or "+" in scheme:
        return url
    bound = _ASYNC_DRIVER_BINDINGS.get(scheme.lower())
    return f"{bound}://{rest}" if bound else url


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
        replicas = _parse_replica_urls(config("DATABASE_READ_REPLICAS", default=None))
        if replicas:
            default_conn["replicas"] = replicas
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
        for cfg in self.connections.values():
            cfg["url"] = normalize_database_url(str(cfg["url"]))
            cfg["replicas"] = [
                normalize_database_url(str(url)) for url in _parse_replica_urls(cfg.get("replicas"))
            ]
        self._engines: dict[str, AsyncEngine] = {}
        self._session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}
        self._replica_engines: dict[str, list[AsyncEngine]] = {}
        self._read_cycles: dict[str, Any] = {}

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
            # Serialize connection use. In-memory sqlite needs ONE shared
            # connection for the schema to persist; file-backed sqlite needs
            # one-at-a-time writers because concurrent connections break
            # transactional invariants (check-then-act races) and collide on
            # the database lock. Either way concurrent scopes queue instead of
            # interleaving (StaticPool would hand one connection to both).
            from sqlalchemy.pool.impl import AsyncAdaptedQueuePool

            kwargs["poolclass"] = AsyncAdaptedQueuePool
            kwargs["pool_size"] = 1
            kwargs["max_overflow"] = 0
        else:
            # Server backends reap idle connections (MySQL's wait_timeout is
            # the classic case) — verify liveness on checkout so a pooled
            # socket is never handed out dead.
            kwargs["pool_pre_ping"] = True
            for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
                if key in cfg:
                    kwargs[key] = int(cfg[key])

        engine = create_async_engine(url, **kwargs)
        from fastplace.orm.instrumentation import install_instrumentation

        install_instrumentation(engine)
        return engine

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

    def replica_engines(self, name: str = "default") -> list[AsyncEngine]:
        """Lazily built engines for the connection's read replicas."""
        urls = self.config_for(name).get("replicas") or []
        if urls and name not in self._replica_engines:
            cfg = self.config_for(name)
            self._replica_engines[name] = [self._create_engine({**cfg, "url": url}) for url in urls]
        return self._replica_engines.get(name, [])

    def read_engine(self, name: str = "default") -> AsyncEngine:
        """Round-robin replica engine; the primary when none are configured."""
        engines = self.replica_engines(name)
        if not engines:
            return self.engine(name)
        cycle = self._read_cycles.get(name)
        if cycle is None:
            cycle = itertools.cycle(range(len(engines)))
            self._read_cycles[name] = cycle
        return engines[next(cycle)]

    def read_session(self, name: str = "default") -> AsyncSession:
        """A session on the next read engine (replica when configured)."""
        return async_sessionmaker(
            self.read_engine(name),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )()

    def capabilities(self, name: str = "default") -> Capabilities:
        cfg = self.config_for(name)
        driver = cfg.get("driver") or driver_from_url(cfg["url"])
        return Capabilities(driver)

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()
        self._session_factories.clear()
        for engines in self._replica_engines.values():
            for engine in engines:
                await engine.dispose()
        self._replica_engines.clear()
        self._read_cycles.clear()

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
        self._replica_engines.clear()
        self._read_cycles.clear()
