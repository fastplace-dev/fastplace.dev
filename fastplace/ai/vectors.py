"""Vector store registry — alternate backends behind one capability-aware API.

The default ``pgvector`` store delegates to the ORM's native, capability-
gated ``Model.vector_search()`` (PostgreSQL + pgvector). Applications can
register alternate backends from ``app/ai/vectors/`` and select one with
``AI_VECTOR_STORE`` — agent tools and services keep calling the same
``search`` interface either way (blueprint §9).
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastplace.config import config
from fastplace.errors import ConfigurationError


@runtime_checkable
class VectorStore(Protocol):
    """The similarity-search interface every vector backend implements."""

    async def search(
        self, model_cls: Any, embedding: list[float], limit: int = 10
    ) -> list[Any]: ...


#: name → store registry; "pgvector" ships pre-registered.
vector_registry: dict[str, VectorStore] = {}


def register_vector_store(name: str):  # noqa: ANN201 — decorator returning a class
    """Class decorator registering a backend under a stable dispatch name."""

    def decorator(cls):
        existing = vector_registry.get(name)
        if existing is not None:
            existing_cls = existing.__class__
            same_source = (existing_cls.__module__, existing_cls.__qualname__) == (
                cls.__module__,
                cls.__qualname__,
            )
            if not same_source:
                raise ValueError(
                    f"vector store name collision: '{name}' is already registered by "
                    f"{existing_cls.__module__}.{existing_cls.__qualname__}"
                )
            # Re-import of the same store module (app factories reload) —
            # keep the live instance instead of crashing on a fresh class
            # object and don't clobber the registered singleton.
            return cls
        vector_registry[name] = cls()
        return cls

    return decorator


def vector_store(name: str) -> VectorStore:
    """Look up a registered store by name."""
    store = vector_registry.get(name)
    if store is None:
        known = ", ".join(sorted(vector_registry)) or "none registered"
        raise ConfigurationError(
            f"unknown vector store '{name}' (registered: {known}) — "
            "register it in app/ai/vectors/ or fix AI_VECTOR_STORE"
        )
    return store


def active_vector_store() -> VectorStore:
    """The configured store (``AI_VECTOR_STORE``, default ``pgvector``)."""
    return vector_store(config("AI_VECTOR_STORE", default="pgvector"))


def reset_vector_registry() -> None:
    """Reset to the shipped default — test isolation."""
    vector_registry.clear()
    vector_registry["pgvector"] = PgVectorStore()


def _is_under(origin: str | None, root: Path) -> bool:
    """True when a module's file really lives under ``root``.

    Containment is resolved-path based, not substring based: a sibling
    project like ``/work/api-v2`` must not match ``/work/api``.
    """
    if not origin:
        return False
    try:
        return Path(origin).resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def import_vector_stores(project_root: str | Path | None = None) -> list[str]:
    """Import every module under ``app/ai/vectors/`` so registrations run."""
    root = Path(project_root) if project_root else Path.cwd()
    vectors_dir = root / "app" / "ai" / "vectors"
    if not vectors_dir.is_dir():
        return []
    root_str = str(root)
    import sys

    # Scope both the path entry and the module-cache eviction to this call:
    # leaving either behind would hijack the next `import app` elsewhere.
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    _evict_stale_app_modules(root)
    try:
        package = importlib.import_module("app.ai.vectors")
        for module_info in pkgutil.iter_modules(package.__path__):
            if not module_info.name.startswith("_"):
                importlib.import_module(f"app.ai.vectors.{module_info.name}")
    except ModuleNotFoundError as exc:
        if exc.name not in ("app", "app.ai", "app.ai.vectors"):
            raise
    finally:
        if inserted:
            sys.path.remove(root_str)
        # The import ran for its registration side effect; leaving this
        # project's `app` cached would hijack the next `import app`
        # elsewhere in the process.
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                module = sys.modules.get(name)
                if _is_under(getattr(module, "__file__", None), root):
                    sys.modules.pop(name, None)
    return sorted(vector_registry)


def _evict_stale_app_modules(root: Path) -> None:
    """Drop cached ``app`` packages bound to a different project root.

    Without this, a previously imported project's ``app`` shadows the one
    under ``root`` and its vector registrations silently never run.
    """
    import sys

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            module = sys.modules.get(name)
            if not _is_under(getattr(module, "__file__", None), root):
                sys.modules.pop(name, None)


class PgVectorStore:
    """The default backend — the ORM's native pgvector path.

    Capability gating stays in ``Model.vector_search()``: on a backend
    without pgvector it raises :class:`SearchCapabilityMissing` rather than
    silently degrading (blueprint §8).
    """

    async def search(self, model_cls: Any, embedding: list[float], limit: int = 10) -> list[Any]:
        return await model_cls.vector_search(embedding, limit=limit)


# shipped default
vector_registry["pgvector"] = PgVectorStore()
