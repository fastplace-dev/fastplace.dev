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
