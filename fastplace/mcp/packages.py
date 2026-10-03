"""Package scanners behind ``application-info``.

Python dependencies are read from ``pyproject.toml`` and resolved against the
installed environment; JS dependencies come from ``package.json`` with
versions resolved from ``package-lock.json`` (direct dependencies only —
transitive packages are noise for an agent writing app code).
"""

from __future__ import annotations

import json
import re
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Any

_FIRST_REQUIREMENT_TOKEN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def requirement_name(requirement: str) -> str:
    """``sqlalchemy[asyncio]>=2.0`` → ``sqlalchemy``."""
    match = _FIRST_REQUIREMENT_TOKEN.match(requirement.strip())
    return match.group(1) if match else requirement.strip()


def _project_dependencies(project: Path) -> list[str]:
    pyproject = project / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []

    table: dict[str, Any] = data.get("project", {})
    deps = list(table.get("dependencies", []))
    for extra in (table.get("optional-dependencies") or {}).values():
        deps.extend(extra)
    return deps


def scan_python_packages(project: Path) -> dict[str, str]:
    """Direct Python dependencies with installed versions."""
    result: dict[str, str] = {}
    for requirement in _project_dependencies(project):
        name = requirement_name(requirement)
        if not name:
            continue
        try:
            result[name] = dist_version(name)
        except PackageNotFoundError:
            result[name] = "not installed"
    return result


def _js_manifest(project: Path) -> dict[str, Any]:
    manifest = project / "package.json"
    if not manifest.exists():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _lock_versions(project: Path) -> dict[str, str]:
    lock = project / "package-lock.json"
    if not lock.exists():
        return {}
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    resolved: dict[str, str] = {}
    for key, entry in (data.get("packages") or {}).items():
        if key.startswith("node_modules/") and "/" not in key[len("node_modules/") :]:
            name = key[len("node_modules/") :]
            if isinstance(entry, dict) and entry.get("version"):
                resolved[name] = str(entry["version"])
    return resolved


def scan_js_packages(project: Path) -> dict[str, str]:
    """Direct JS dependencies, lock-resolved where the lockfile has them."""
    manifest = _js_manifest(project)
    if not manifest:
        return {}

    direct: list[str] = []
    for section in ("dependencies", "devDependencies"):
        section_deps = manifest.get(section) or {}
        direct.extend(section_deps.keys())

    lock = _lock_versions(project)
    declared: dict[str, Any] = {}
    for section in ("dependencies", "devDependencies"):
        declared.update(manifest.get(section) or {})

    return {name: lock.get(name, str(declared[name])) for name in direct if name in declared}
