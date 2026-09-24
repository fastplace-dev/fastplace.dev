"""Shared autouse isolation for CLI tests that scaffold tmp Fastplace projects.

Import ``isolate_project_state`` from a test module to activate it — a plain
module on purpose, NOT a conftest.py: the fixture's teardown must stay scoped
to the two database-CLI test files that need it, not every test under
tests/cli/ (blast radius).
"""

from __future__ import annotations

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
    import sys

    from fastplace.db import reset_db
    from fastplace.orm import Model

    reset_db()
    saved_path = list(sys.path)
    saved_modules = dict(sys.modules)
    saved_tables = set(Model.metadata.tables)
    parked = {
        name: module
        for name, module in saved_modules.items()
        if name == "app" or name.startswith(("app.", "_fastplace_seeder_", "_fastplace_config_"))
    }
    for name in parked:
        del sys.modules[name]
    yield
    reset_db()
    for key in set(Model.metadata.tables) - saved_tables:
        Model.metadata.remove(Model.metadata.tables[key])
    for name in [m for m in list(sys.modules) if m not in saved_modules]:
        del sys.modules[name]
    sys.modules.update(parked)
    sys.path[:] = saved_path
