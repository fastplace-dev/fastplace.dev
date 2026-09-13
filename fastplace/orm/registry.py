"""Model registry — discovery for migrations, seeders, and the shell."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any


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
