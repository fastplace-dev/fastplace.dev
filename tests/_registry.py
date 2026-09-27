"""Shared registry isolation for suites that evict/re-import model modules.

The declarative base, its metadata, and the mapper registry are
process-global: a ``metadata.clear()``/``clear_mappers()`` teardown strands
every model module another suite already imported — the classes keep living
in ``sys.modules`` while their tables vanish from the shared metadata — and
the damage surfaces far from the cause: phantom models, ``Table ... is
already defined`` collisions, polluted autogenerate scopes. Suites isolate
surgically instead: snapshot the metadata at setup, sweep only the tables
the test added at teardown. Redeclared tablenames replace cleanly at the
base itself (``Model.__init_subclass__``), so re-imported modules need no
global reset either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest


def metadata_baseline() -> set[str]:
    """Table keys registered on the shared metadata right now."""
    from fastplace.orm.model import Model

    return set(Model.metadata.tables)


def sweep_added_tables(baseline: set[str]) -> None:
    """Take only the post-baseline tables off the shared metadata."""
    from fastplace.orm.model import Model

    for key in list(Model.metadata.tables):
        if key not in baseline:
            Model.metadata.remove(Model.metadata.tables[key])


def dispose_all_models() -> None:
    """Unmap every model class the process currently holds.

    The mapper registry is as global as the metadata: a model class one
    suite declared (say a test's ``Comment``) keeps living next to another
    suite's same-named class, and a relationship string such as
    ``"Comment"`` then resolves across them — or fails to, leaving a
    half-configured mapper that poisons every later ``configure_mappers()``
    in the process. Disposal walks the same per-class teardown the base
    itself performs on redeclaration (mapper, registry entry,
    instrumentation, manager), so nothing is stranded — the owning module
    re-imports cleanly and redeclares.

    Unlike ``clear_mappers()`` this never leaves a disposed manager inside
    ``registry._managers``, which is what turned the old nuclear reset into
    ``'NoneType' object has no attribute 'configured'`` two suites later.

    Each class's Table comes off the shared metadata with it: disposal
    strips ``__table__`` from the class, and the boot sandbox attributes
    tables to projects through that attribute — a Table left registered
    without its class can no longer be classified as foreign, so the next
    ``project_boot_sandbox`` diffs it into another project's autogenerate
    revision instead of evicting it.
    """
    from fastplace.orm.model import Model
    from fastplace.orm.registry import all_models

    registry = Model.registry
    metadata = Model.metadata
    for cls in list(all_models()):
        table = getattr(cls, "__table__", None)
        manager = getattr(cls, "_sa_class_manager", None)
        if manager is not None:
            registry._dispose_manager_and_mapper(manager)  # noqa: SLF001
            registry._managers.pop(manager, None)  # noqa: SLF001
        else:  # pragma: no cover — an uninstrumented subclass has no manager
            registry._dispose_cls(cls)  # noqa: SLF001
        if table is not None and table.key in metadata.tables:
            metadata.remove(table)


@pytest.fixture()
def swept_registry() -> Iterator[None]:
    """Snapshot-and-sweep the shared metadata around one test."""
    baseline = metadata_baseline()
    yield
    sweep_added_tables(baseline)
