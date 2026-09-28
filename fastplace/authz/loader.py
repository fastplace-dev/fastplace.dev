"""Boot-time import of the project's gate registrations (spec §4.15/§4.17)."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

_GATES_MODULE = "app.auth.gates"


def _import_by_path(gates_file: Path) -> None:
    """Load ``gates.py`` under its canonical dotted name, straight from disk.

    Used when the dotted import cannot see the project's ``app`` package —
    a foreign regular ``app`` package elsewhere on sys.path shadows the
    project's (PEP 420 namespace) ``app``, and regular packages win that
    contest no matter where the project root sits on the path.
    """
    spec = importlib.util.spec_from_file_location(_GATES_MODULE, gates_file)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load gate registrations from {gates_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_GATES_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A half-registered broken module must not poison later imports.
        sys.modules.pop(_GATES_MODULE, None)
        raise


def _evict_stale_gates_module(gates_file: Path) -> None:
    """Drop a cached gates module bound to a different project's file.

    sys.modules is process-global while gates.py is project-local: without
    this, a second boot in one process would re-register — or silently
    keep answering with — the first project's gates.
    """
    cached = sys.modules.get(_GATES_MODULE)
    if cached is None:
        return
    cached_file = getattr(cached, "__file__", None)
    if cached_file is None or Path(cached_file).resolve() != gates_file.resolve():
        del sys.modules[_GATES_MODULE]


def import_gates(project_root: str | Path | None = None) -> bool:
    """Import ``app/auth/gates.py`` so its gate registrations run.

    Returns whether the module existed. Absence is normal (projects without
    authorization); a present-but-broken module raises — fail loud at boot,
    not on the first denied request.
    """
    root = Path(project_root) if project_root else Path.cwd()
    gates_file = root / "app" / "auth" / "gates.py"
    if not gates_file.is_file():
        return False

    _evict_stale_gates_module(gates_file)

    root_str = str(root)
    # Scope the path entry to this call (the import_jobs precedent: leaving
    # it behind would hijack the next `import app` elsewhere in the process).
    inserted = root_str not in sys.path
    if inserted:
        sys.path.insert(0, root_str)
    try:
        try:
            importlib.import_module(_GATES_MODULE)
        except ModuleNotFoundError as exc:
            if exc.name not in ("app", "app.auth", _GATES_MODULE):
                raise  # broken gates module — fail loud at boot
            # The project's app package is unreachable by name (shadowed or
            # not a package at all); load the file directly so boot still
            # registers the gates. Content errors inside it keep raising.
            _import_by_path(gates_file)
    finally:
        if inserted:
            sys.path.remove(root_str)
    return True
