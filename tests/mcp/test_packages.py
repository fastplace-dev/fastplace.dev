"""Package scanners behind ``application-info``.

Python side resolves names from ``pyproject.toml`` against installed
distributions; the JS side resolves direct dependencies from
``package-lock.json`` so reported versions are what actually shipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastplace.mcp.packages import (
    requirement_name,
    scan_js_packages,
    scan_python_packages,
)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "demo"
dependencies = [
    "fastplace>=0.4",
    "sqlalchemy[asyncio]>=2.0",
    "pydantic-settings>=2.0,<3",
]

[project.optional-dependencies]
mcp = ["mcp>=2.0"]

[tool.ruff]
line-length = 100
"""
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-web",
                "dependencies": {"react": "^18.2.0"},
                "devDependencies": {"vite": "^5.0.0", "typescript": "^5.4.0"},
            }
        )
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo-web"},
                    "node_modules/react": {"version": "18.3.1"},
                    "node_modules/vite": {"version": "5.4.10"},
                    # typescript intentionally absent from the lock — fallback path
                    "node_modules/transitive-helper": {"version": "1.0.0"},
                },
            }
        )
    )
    return tmp_path


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("fastplace>=0.4", "fastplace"),
        ("sqlalchemy[asyncio]>=2.0", "sqlalchemy"),
        ("pydantic-settings>=2.0,<3", "pydantic-settings"),
        ("mcp >= 2.0", "mcp"),
        ("uvicorn[standard]", "uvicorn"),
    ],
)
def test_requirement_name(requirement: str, expected: str) -> None:
    assert requirement_name(requirement) == expected


def test_scan_python_packages_uses_pyproject(project: Path) -> None:
    packages = scan_python_packages(project)

    # fastplace is installed in this environment; sqlalchemy too.
    assert packages["fastplace"].count(".") == 2
    assert "sqlalchemy" in packages
    assert packages["mcp"] != ""
    # Dev-only tool config sections are not dependencies.
    assert "ruff" not in packages


def test_scan_python_packages_reports_unresolved(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        'name = "x"\n[project]\ndependencies = ["definitely-not-a-real-package-xyz>=1.0"]\n'
    )

    packages = scan_python_packages(tmp_path)

    assert packages["definitely-not-a-real-package-xyz"] == "not installed"


def test_scan_python_packages_empty_project(tmp_path: Path) -> None:
    assert scan_python_packages(tmp_path) == {}


def test_scan_js_packages_uses_lockfile_versions(project: Path) -> None:
    packages = scan_js_packages(project)

    # Lock-resolved, not the declared range.
    assert packages["react"] == "18.3.1"
    assert packages["vite"] == "5.4.10"
    # Direct dependency missing from the lock falls back to its range.
    assert packages["typescript"] == "^5.4.0"
    # Transitive packages never appear — direct deps only.
    assert "transitive-helper" not in packages


def test_scan_js_packages_without_lockfile(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.2.0"}}))

    packages = scan_js_packages(tmp_path)

    assert packages == {"react": "^18.2.0"}


def test_scan_js_packages_no_js_project(tmp_path: Path) -> None:
    assert scan_js_packages(tmp_path) == {}
