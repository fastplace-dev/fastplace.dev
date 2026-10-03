"""The in-process project boot sandbox — no project code survives the boot.

A sandboxed boot exists so one process can run a project's code (route
introspection, migrate compare) without that project hijacking the
process afterward: once the ``with`` block exits, importing ``asgi``,
``routes`` or ``app`` must never resolve to the project that was booted —
not for a host embedding the boot, and not for the next suite in a
long-lived pytest process.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastplace.orm.registry import project_boot_sandbox


def _mini_project(root: Path, marker: str) -> Path:
    (root / "routes").mkdir(parents=True)
    (root / "routes" / "__init__.py").write_text(f"MARKER = {marker!r}\n")
    return root


def test_boot_leaves_no_project_modules_cached(tmp_path):
    root = _mini_project(tmp_path, "booted")

    with project_boot_sandbox(root):
        import routes  # noqa: F401 — the boot itself imports project code

        assert sys.modules["routes"].MARKER == "booted"

    booted = sys.modules.get("routes")
    assert booted is None or getattr(booted, "MARKER", None) != "booted"
    # and the boot's sys.path entry left with it
    assert str(root) not in sys.path


def test_sequential_boots_leave_nothing_behind(tmp_path):
    first = _mini_project(tmp_path / "first", "alpha")
    second = _mini_project(tmp_path / "second", "beta")

    with project_boot_sandbox(first):
        import routes  # noqa: F401

        assert routes.MARKER == "alpha"

    with project_boot_sandbox(second):
        import routes  # noqa: F401

        assert routes.MARKER == "beta"

    assert getattr(sys.modules.get("routes"), "MARKER", None) not in ("alpha", "beta")


def test_persist_boot_keeps_model_modules_available(tmp_path):
    """``persist=True`` persists model registrations, and callers resolve the
    imported classes through ``sys.modules`` after the block (``ai:embed
    --check`` picks the current project's class that way) — so the project's
    ``app.*`` modules stay cached. Router entries are different: ``routes``
    and ``asgi`` never outlive the boot, persist or not, or the process's
    next ``import routes`` resolves some introspected project's router."""
    (tmp_path / "app" / "models").mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "models" / "__init__.py").write_text("")
    (tmp_path / "app" / "models" / "document.py").write_text("MARKER = 'kept'\n")
    _mini_project(tmp_path, "router")

    with project_boot_sandbox(tmp_path, persist=True):
        from app.models import document  # noqa: F401

        import routes  # noqa: F401 — the boot's own router imports

        assert routes.MARKER == "router"
        assert document.MARKER == "kept"

    doc = sys.modules.get("app.models.document")
    assert getattr(doc, "MARKER", None) == "kept"
    booted_routes = sys.modules.get("routes")
    assert booted_routes is None or getattr(booted_routes, "MARKER", None) != "router"


def test_dispose_drops_the_table_with_the_class():
    """``dispose_all_models`` leaves no orphans: the Table goes with the class.

    Disposal strips ``__table__`` from the class, and the sandbox's foreign
    classification keys on exactly that attribute — a Table left registered
    without its class can no longer be attributed to a project, so the next
    boot diffs it into a foreign autogenerate revision instead of evicting it.
    """
    from fastplace.orm import Field, Model
    from fastplace.orm.model import Model as ModelBase
    from tests._registry import dispose_all_models

    class Note(Model):
        __tablename__ = "sandbox_dispose_notes"

        title: str = Field()

    assert "sandbox_dispose_notes" in ModelBase.metadata.tables

    dispose_all_models()

    assert "sandbox_dispose_notes" not in ModelBase.metadata.tables


def test_orphan_table_is_evicted_on_entry(tmp_path):
    """A Table with no live model class is evicted like a foreign table.

    The class registry holds model subclasses weakly, so an owning class can
    be garbage-collected while the global metadata keeps its Table — the
    Table becomes unattributable to any project. Class-keyed entry eviction
    can no longer see it, and the next boot would diff it into an
    autogenerate revision and a ``migrate:check`` compare (the intermittent
    CLI-suite leak). Everything a *live* model owns is exempt; the sweep
    takes only the class-less remainder.
    """
    from sqlalchemy import Column, Integer, Table

    from fastplace.orm.model import Model as ModelBase

    Table(
        "sandbox_orphan_docs",
        ModelBase.metadata,
        Column("id", Integer, primary_key=True),
    )
    try:
        assert "sandbox_orphan_docs" in ModelBase.metadata.tables

        with project_boot_sandbox(tmp_path, persist=True):
            assert "sandbox_orphan_docs" not in ModelBase.metadata.tables

        # persist=True keeps the eviction in place: the orphan is nobody's
        # project table, so the caller's project owns the metadata without it.
        assert "sandbox_orphan_docs" not in ModelBase.metadata.tables
    finally:
        if "sandbox_orphan_docs" in ModelBase.metadata.tables:
            ModelBase.metadata.remove(ModelBase.metadata.tables["sandbox_orphan_docs"])


def test_orphan_table_is_restored_after_non_persist_boot(tmp_path):
    """``persist=False`` restores what it evicted — orphans included.

    The sandbox never permanently destroys a Table it found at entry, even
    one whose owning class is gone: the boot's exit re-adds the snapshot so
    the surrounding process state (a test fixture's saved registration) is
    exactly what it was before the block.
    """
    from sqlalchemy import Column, Integer, Table

    from fastplace.orm.model import Model as ModelBase

    Table(
        "sandbox_orphan_posts",
        ModelBase.metadata,
        Column("id", Integer, primary_key=True),
    )

    with project_boot_sandbox(tmp_path):
        assert "sandbox_orphan_posts" not in ModelBase.metadata.tables

    assert "sandbox_orphan_posts" in ModelBase.metadata.tables
    ModelBase.metadata.remove(ModelBase.metadata.tables["sandbox_orphan_posts"])
