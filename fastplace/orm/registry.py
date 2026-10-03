"""Model registry — discovery for migrations, seeders, and the shell."""

from __future__ import annotations

import importlib
import pkgutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Top-level module names a Fastplace project owns on ``sys.path``. They —
#: and only they — are snapshotted around an in-process project boot so one
#: project's code never shadows another's later in the process.
PROJECT_NAMESPACES = frozenset({"asgi", "routes", "app"})


def module_is_local(module: Any, root: Path) -> bool:
    """True when a cached project-namespace module's source lives under root.

    A module from a *different* project (its file sits outside root) must be
    evicted before the boot or it shadows this project's code — a cached
    foreign ``app`` package hides this project's ``app.modules.<name>`` from
    ``import_all_models`` entirely. A module whose file already lives under
    root belongs to the very project being booted — evicting it would force a
    re-execution that redefines its declarative classes against tables that
    are still registered. Namespace-ish entries without a file count as
    foreign: popping them is always safe.
    """
    file = getattr(module, "__file__", None)
    if not file:
        return False
    try:
        return Path(file).resolve().is_relative_to(root.resolve())
    except OSError:  # pragma: no cover — unresolvable paths on odd mounts
        return False


@contextmanager
def project_boot_sandbox(root: Path, *, persist: bool = False) -> Iterator[None]:
    """Boot a project in-process without leaving its modules or tables behind.

    Cached modules in the project namespaces are snapshotted and restored,
    so booting one project never leaves it hijacking ``import
    asgi``/``routes``/``app`` for the rest of the process — and nothing the
    boot imported survives it either, the project's own modules included: a
    long-lived process (a host embedding the boot, the pytest process) that
    imports its own ``routes`` afterward must not resolve this project's
    cache. Re-importing on a later boot is safe — a redeclared model
    replaces its superseded declaration instead of colliding with it.
    ``persist=True`` is the carve-out for model imports: the caller uses the
    registered classes after the block and resolves them through ``sys.modules``,
    so the project's ``app.*`` modules stay cached — ``routes``/``asgi``
    never do. The
    ORM's metadata is just as global: two projects that both
    define a table (every auth scaffold ships ``users``) would collide on
    the second boot with ``Table 'users' is already defined for this
    MetaData instance``. Tables owned by *foreign* app models are therefore
    evicted on entry, and on exit what the boot itself registered is
    dropped and the evicted tables restored — each cross-project boot maps
    against a clean slate and leaves one behind.

    ``persist=True`` is for imports whose registrations the caller uses
    *after* the block (``collect_models`` feeding ``migrate:check``'s
    metadata compare, ``MigrationsManager.make``'s autogenerate): the
    freshly imported tables stay registered and the evicted foreign ones
    stay out — the caller's project now owns the metadata, at least for the
    keys both defined.
    """
    from fastplace.orm.model import Model

    metadata = Model.metadata

    def _class_is_foreign(cls: Any) -> bool:
        if cls.__module__.split(".", 1)[0] != "app" or getattr(cls, "__table__", None) is None:
            return False
        module = sys.modules.get(cls.__module__)
        return module is None or not module_is_local(module, root)

    # Tables owned by a *foreign* project's models — e.g. a host repo's own
    # app living in the process while a scaffolded project boots.
    classes = all_models()
    evicted = {cls.__table__.key: cls.__table__ for cls in classes if _class_is_foreign(cls)}
    # A Table whose owning class is gone is unattributable: the class
    # registry holds model subclasses weakly, so a class can be
    # garbage-collected while the global metadata keeps its Table — and
    # class-keyed eviction can no longer see it, so it would diff into the
    # next boot's autogenerate revision and migrate:check compare. Every
    # Table a live model owns is exempt; the sweep takes only the class-less
    # remainder. (``dispose_all_models`` prevents the same state for
    # deliberate disposal; this covers the GC path.)
    owned = {cls.__table__.key for cls in classes if getattr(cls, "__table__", None) is not None}
    for key in metadata.tables:
        if key not in evicted and key not in owned:
            evicted[key] = metadata.tables[key]
    for key in evicted:
        if key in metadata.tables:
            metadata.remove(metadata.tables[key])
    baseline = set(metadata.tables)
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name.split(".", 1)[0] in PROJECT_NAMESPACES and not module_is_local(module, root)
    }
    for name in saved_modules:
        sys.modules.pop(name)
    root_str = str(root)
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        yield
    finally:
        if inserted:
            sys.path.remove(root_str)
        # Only the foreign namespaces were popped on entry — drop exactly
        # what came back for those, never the host project's own cache.
        for name in [
            n
            for n, m in sys.modules.items()
            if n.split(".", 1)[0] in PROJECT_NAMESPACES and not module_is_local(m, root)
        ]:
            sys.modules.pop(name)
        # Router entries never outlive the boot, persist or not: the
        # process's next ``import routes`` must resolve its own project, not
        # whatever this boot introspected. Model modules are the exception a
        # persist caller relies on — class resolution through sys.modules
        # after the block — so their entries are re-asserted after the
        # foreign snapshot comes back (the booted project wins its names).
        if persist:
            survivors = {
                n: m
                for n, m in sys.modules.items()
                if n.split(".", 1)[0] == "app" and module_is_local(m, root)
            }
        for name in [
            n
            for n, m in sys.modules.items()
            if n.split(".", 1)[0] in PROJECT_NAMESPACES - {"app"} and module_is_local(m, root)
        ]:
            sys.modules.pop(name)
        sys.modules.update(saved_modules)
        if persist:
            sys.modules.update(survivors)
        else:
            for name in [
                n
                for n, m in sys.modules.items()
                if n.split(".", 1)[0] in PROJECT_NAMESPACES and module_is_local(m, root)
            ]:
                sys.modules.pop(name)
        if not persist:
            # Drop what this boot registered beyond the baseline…
            for key in list(metadata.tables):
                if key not in baseline:
                    metadata.remove(metadata.tables[key])
            # …and put the foreign project's tables back. FacadeDict is immutable,
            # so re-registration goes through the same internal entry point
            # MetaData itself uses when a Table is first defined.
            for key, table in evicted.items():
                if key not in metadata.tables:
                    metadata._add_table(key, table.schema, table)  # noqa: SLF001 — no public re-add


def import_all_models(project_root: str | Path | None = None) -> list[type[Any]]:
    """Import every model module under ``app/modules/**/models`` and ``app/models``."""
    root = Path(project_root) if project_root else Path.cwd()
    packages = []

    modules_dir = root / "app" / "modules"
    if modules_dir.is_dir():
        for module_dir in sorted(modules_dir.iterdir()):
            models_dir = module_dir / "models"
            if models_dir.is_dir():
                packages.append(f"app.modules.{module_dir.name}.models")

    shared_dir = root / "app" / "models"
    if shared_dir.is_dir():
        packages.append("app.models")

    for package_name in packages:
        try:
            package = importlib.import_module(package_name)
        except ModuleNotFoundError:
            continue
        for module_info in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package_name}.{module_info.name}")

    return all_models()


def all_models() -> list[type[Any]]:
    """Every mapped Model subclass, recursively."""
    from fastplace.orm import Model

    seen: set[type] = set()
    found: list[type] = []
    stack = [Model]
    while stack:
        klass = stack.pop()
        for subclass in klass.__subclasses__():
            if subclass in seen:
                continue
            seen.add(subclass)
            stack.append(subclass)
            if not getattr(subclass, "__abstract__", False):
                found.append(subclass)
    return found
