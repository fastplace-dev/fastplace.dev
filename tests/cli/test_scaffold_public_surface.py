"""Scaffold output stays on the framework's public API surface (upg-G4).

Everything ``fastplace new --auth`` and ``make:auth`` writes becomes
user-owned application code. The versioning guide declares underscore names
internal and may change them in any release — the scaffold must not copy
those names into apps, or every internal refactor breaks every published
starter kit. Same for module paths the public packages do not re-export:
application code imports ``fastplace.http`` / ``fastplace.orm``, never the
files underneath them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Module paths that are implementation details of the public packages —
# their symbols are reachable through the package root instead
# (fastplace.orm re-exports Model; fastplace.http re-exports share).
_PRIVATE_MODULE_PATHS = frozenset(
    {
        "fastplace.orm.model",
        "fastplace.orm.capabilities",
        "fastplace.http.flash",
        "fastplace.http.render",
        "fastplace.http.request",
    }
)


def _invoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    from typer.testing import CliRunner

    from fastplace.cli import app as cli_app

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, ["new", "blog", *args])


def _fastplace_imports(project: Path) -> list[tuple[str, str, Path]]:
    """Every ``from fastplace... import name`` in the emitted app, with file."""
    found: list[tuple[str, str, Path]] = []
    for path in sorted(project.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == "fastplace" or node.module.startswith("fastplace."):
                for alias in node.names:
                    found.append((node.module, alias.name, path))
    return found


@pytest.mark.parametrize("flag", ["--auth", "--no-auth"])
def test_scaffold_emits_only_public_fastplace_imports(tmp_path, monkeypatch, flag):
    result = _invoke(tmp_path, monkeypatch, flag)
    assert result.exit_code == 0, result.output

    imports = _fastplace_imports(tmp_path / "blog")
    assert imports, "expected the scaffold to import fastplace somewhere"

    private_names = [
        (module, name, path.relative_to(tmp_path))
        for module, name, path in imports
        if name.startswith("_")
    ]
    assert not private_names, f"underscore-private imports copied into user code: {private_names}"

    private_paths = [
        (module, name, path.relative_to(tmp_path))
        for module, name, path in imports
        if module in _PRIVATE_MODULE_PATHS
    ]
    assert not private_paths, f"non-re-exported module paths copied into user code: {private_paths}"
