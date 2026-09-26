"""Shared isolation helpers for CLI tests that scaffold tmp Fastplace projects.

Import ``isolate_project_state`` (autouse, db-CLI files only),
``park_project_modules``, ``park_app_modules``, or ``ensure_modules`` from
a test module — a plain module on purpose, NOT a conftest.py: the teardown
must stay scoped to the test files that opt in, not every test under
tests/cli/ (blast radius).
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator, Mapping
from types import ModuleType

import pytest


@pytest.fixture(autouse=True)
def isolate_project_state():
    """Give every test a clean db facade, model metadata, and module cache.

    Seeders import project models (``app.*``) through normal package
    resolution, so any foreign cached ``app`` package — the repo's own
    project or an earlier tmp project — shadows this test's tree, and a
    cached ``Post`` class writes rows into a previous project's engine.
    Foreign ``app.*`` entries are parked for the test's duration and the
    pre-test snapshot is restored afterward (unlike ``tests/orm/conftest.py``
    which blanket-clears: these tests run before ``tests/http``, whose
    repo-owned ``app.*`` must stay cached or a re-import collides in the
    global @Job registry).
    """
    from fastplace.db import reset_db
    from fastplace.orm import Model

    reset_db()
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    # Table snapshot as a mapping, not a set: CLI introspection (collect_models)
    # evicts foreign tables for its sandbox and — persist mode — leaves them
    # out, so teardown must also put back what the test REMOVED, or later
    # suites (tests/http's db.create_all builds schema from this metadata)
    # silently lose the repo project's tables.
    saved_tables = dict(Model.metadata.tables)
    parked = {
        name: module
        for name, module in saved_modules.items()
        if name == "app" or name.startswith(("app.", "_fastplace_seeder_", "_fastplace_config_"))
    }
    for name in parked:
        del sys.modules[name]
    yield
    reset_db()
    # Additions first (a same-named tmp table must go before the snapshot's
    # owner can return), then restore removals. FacadeDict is immutable, so
    # re-registration goes through the internal entry point MetaData itself
    # uses when a Table is first defined.
    for key in set(Model.metadata.tables) - set(saved_tables):
        Model.metadata.remove(Model.metadata.tables[key])
    for key, table in saved_tables.items():
        if key not in Model.metadata.tables:
            Model.metadata._add_table(key, table.schema, table)  # noqa: SLF001 — no public re-add
    for name in [m for m in list(sys.modules) if m not in saved_modules]:
        del sys.modules[name]
    sys.modules.update(parked)
    sys.path[:] = saved_path


@contextlib.contextmanager
def ensure_modules(modules: Mapping[str, ModuleType]) -> Iterator[None]:
    """Re-cache a fixed set of project modules for the block; state as found.

    Entries from ``modules`` that a mid-session eviction removed from
    ``sys.modules`` are re-inserted (keeping a later import a cache hit
    instead of a re-execution that would collide with still-registered
    tables), and on exit only what this block inserted is popped — and only
    while the cached object is still the inserted one. Modules the block did
    NOT insert (pulled in mid-test by an import-all walk, say) stay cached:
    later suites in the same process rely on them, and evicting one would
    leave its tables registered for a re-import to collide with. Use this
    for repo-root fixtures; ``park_app_modules`` is the right tool when the
    test adopts a foreign project's ``app.*`` entries.
    """
    healed = {name: module for name, module in modules.items() if name not in sys.modules}
    sys.modules.update(healed)
    try:
        yield
    finally:
        for name, module in healed.items():
            if sys.modules.get(name) is module:
                sys.modules.pop(name, None)


@contextlib.contextmanager
def park_app_modules() -> Iterator[None]:
    """Park cached ``app.*`` modules for the block; sweep and restore after.

    Tests that trigger project imports (CLI boots, the registration
    importers behind ``event:list``/``ai:tools``/``ai:vectors``) mutate the
    ``app.*`` slice of ``sys.modules`` in three ways: the importer evicts
    foreign-root modules, adopts the fixture project's own modules, and a
    popped child (``app.auth.gates``) can leave dead parents (``app``,
    ``app.auth``) behind. Teardown therefore sweeps the whole ``app.*``
    prefix — adopted and dead entries alike — then restores the pre-block
    snapshot, so later suites find exactly what was there before (the state
    ``tests/http`` depends on, per ``isolate_project_state``'s docstring).
    """
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }
    try:
        yield
    finally:
        for name in [n for n in list(sys.modules) if n == "app" or n.startswith("app.")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture
def park_project_modules():
    """Fixture form of ``park_app_modules`` for tests that request it by name."""
    with park_app_modules():
        yield
