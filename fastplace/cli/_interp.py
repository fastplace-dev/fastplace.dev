"""Project-interpreter resolution for CLI-spawned children.

``fastplace`` may run from an environment that is not the app's — a
global ``uv tool install fastplace`` shim, a pipx install. Children it
spawns that must import the *project's* code (uvicorn importing
asgi.py, pytest importing the suite, the make:auth bootstrap importing
the fresh app) resolve their interpreter from the project root first;
the interpreter running this CLI stays the fallback, which is also the
historical behavior whenever a project has no ``.venv``.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_IS_WINDOWS = os.name == "nt"


def _venv_dirs() -> tuple[str, str]:
    """(script dir, python executable name) inside a ``.venv``, per platform."""
    if _IS_WINDOWS:
        return "Scripts", "python.exe"
    return "bin", "python"


def project_python(root: Path) -> str:
    """Interpreter for children that must import the project's env."""
    script_dir, exe = _venv_dirs()
    candidate = root / ".venv" / script_dir / exe
    if candidate.exists():
        return str(candidate)
    return sys.executable


def project_fastplace_bin(root: Path) -> str | None:
    """This project's ``fastplace`` console script, else one on PATH."""
    exe = "fastplace.exe" if _IS_WINDOWS else "fastplace"
    sibling = Path(project_python(root)).with_name(exe)
    if sibling.exists():
        return str(sibling)
    return shutil.which("fastplace")


def central_pycache_prefix(root: Path) -> Path:
    """Central bytecode-cache location for a project.

    ``storage/`` is the framework's designated runtime-output home (logs,
    cache, uploads) and is gitignored by the scaffold, so the mirrored
    ``.pyc`` tree lands outside version control without a new ignore rule.
    """
    return root / "storage" / "cache" / "pycache"


def with_pycache_prefix(root: Path, env: dict[str, str]) -> dict[str, str]:
    """Child env with ``PYTHONPYCACHEPREFIX`` defaulted to the central cache.

    Keeps the source tree free of ``__pycache__`` dirs while preserving
    bytecode caching (no cold-import recompilation penalty). An explicit
    user export always wins — the default must never veto a deliberate
    setting. Returns a copy; the caller's dict stays untouched.
    """
    if env.get("PYTHONPYCACHEPREFIX"):
        return env
    return {**env, "PYTHONPYCACHEPREFIX": str(central_pycache_prefix(root))}
