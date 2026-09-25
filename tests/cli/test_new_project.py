"""``fastplace new`` — the modular-monolith project scaffolder (blueprint §3).

The generated skeleton must be the canonical layout the rest of the CLI and
the kernel already assume: module-first ``app/``, thin ``routes/``, config
modules, SQLite-by-default env, and a bootable ASGI entry point.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fastplace.cli import app as cli_app

_DIRECTORIES = (
    "app/http/controllers",
    "app/http/requests",
    "app/http/middleware",
    "app/modules",
    "app/ai/agents",
    "app/ai/tools",
    "app/ai/vectors",
    "app/jobs",
    "app/models",
    "database/migrations",
    "database/seeders",
    "resources/js/components",
    "resources/js/hooks",
    "resources/js/layouts",
    "resources/js/pages",
    "resources/css",
    "routes",
    "config",
    "public",
    "storage",
)

_FILES = (
    "app/http/controllers/__init__.py",
    "app/http/controllers/home_controller.py",
    "app/http/requests/__init__.py",
    "app/http/middleware/__init__.py",
    "app/modules/__init__.py",
    "app/ai/agents/__init__.py",
    "app/ai/tools/__init__.py",
    "app/ai/vectors/__init__.py",
    "app/jobs/__init__.py",
    "app/models/__init__.py",
    "resources/js/main.jsx",
    "resources/js/layouts/AppLayout.jsx",
    "resources/js/pages/Home/Index.jsx",
    "resources/css/app.css",
    "routes/__init__.py",
    "routes/web.py",
    "routes/api.py",
    "routes/ai.py",
    "config/__init__.py",
    "config/app.py",
    "config/database.py",
    "config/ai.py",
    "config/auth.py",
    "public/.gitkeep",
    "storage/.gitkeep",
    "database/seeders/.gitkeep",
    ".env",
    ".env.example",
    ".gitignore",
    "index.html",
    "asgi.py",
    "pyproject.toml",
    "package.json",
    "vite.config.js",
    "README.md",
)


def _invoke(tmp_path: Path, monkeypatch, name: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_app, ["new", name])
    return result, tmp_path


def test_new_scaffolds_the_canonical_tree(tmp_path, monkeypatch):
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    project = root / "blog"
    for rel in _DIRECTORIES:
        assert (project / rel).is_dir(), f"missing directory {rel}"
    for rel in _FILES:
        assert (project / rel).is_file(), f"missing file {rel}"


def test_new_slugifies_the_project_name(tmp_path, monkeypatch):
    result, root = _invoke(tmp_path, monkeypatch, "My Blog App")
    assert result.exit_code == 0, result.output
    assert (root / "my-blog-app").is_dir()


def test_new_env_defaults_to_sqlite_and_generates_a_key(tmp_path, monkeypatch):
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    env = (root / "blog" / ".env").read_text()
    example = (root / "blog" / ".env.example").read_text()

    assert "sqlite+aiosqlite" in env
    assert "sqlite+aiosqlite" in example
    # .env carries a generated signing key; the committed example stays empty.
    key_line = next(line for line in env.splitlines() if line.startswith("APP_KEY="))
    assert len(key_line.removeprefix("APP_KEY=")) >= 32
    assert "APP_KEY=\n" in example or example.endswith("APP_KEY=")
    # Config defaults mirror the canonical project shape.
    assert 'APP_ENV = "local"' in (root / "blog" / "config" / "app.py").read_text()


def test_new_refuses_a_non_empty_directory(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "blog"
    target.mkdir()
    (target / "keep.txt").write_text("precious")
    result = CliRunner().invoke(cli_app, ["new", "blog"])

    assert result.exit_code != 0
    assert (target / "keep.txt").read_text() == "precious"
    # Refusal happens before any scaffolding.
    assert not (target / "asgi.py").exists()


def test_new_refuses_unsafe_names(tmp_path, monkeypatch):
    result, _ = _invoke(tmp_path, monkeypatch, "../escape")
    assert result.exit_code != 0
    assert "name" in result.output.lower()


def test_new_prints_next_steps(tmp_path, monkeypatch):
    result, _ = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output
    for step in ("cd blog", "pip install", "npm install", "fastplace migrate", "fastplace run dev"):
        assert step in result.output


def test_new_web_route_serves_the_home_page(tmp_path, monkeypatch):
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    web = (root / "blog" / "routes" / "web.py").read_text()
    assert "router" in web
    assert "HomeController" in web


def test_scaffolded_project_boots(tmp_path, monkeypatch):
    """The strongest guarantee: ``create_app()`` imports routes/config and
    builds the ASGI app inside the generated tree, in a clean interpreter."""
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    project = root / "blog"

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastplace.http import create_app; app = create_app(); print('BOOT_OK', len(app.routes))",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "BOOT_OK" in proc.stdout


def test_new_preconfigures_the_migration_environment(tmp_path, monkeypatch):
    """``fastplace migrate`` must work on a fresh scaffold with no extra
    setup — the Alembic env comes pre-configured (like ``db:configure``)."""
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    project = root / "blog"

    assert (project / "database" / "migrations" / "env.py").is_file()
    assert (project / "database" / "migrations" / "script.py.mako").is_file()
    assert (project / "database" / "migrations" / "versions").is_dir()

    from fastplace.orm.migrations import MigrationsManager

    assert MigrationsManager(project).configured


def test_scaffolded_pyproject_declares_fastplace(tmp_path, monkeypatch):
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    pyproject = (root / "blog" / "pyproject.toml").read_text()
    package_json = (root / "blog" / "package.json").read_text()
    vite_config = (root / "blog" / "vite.config.js").read_text()

    assert "fastplace" in pyproject
    assert '"build"' in package_json
    assert "resources/js/main.jsx" in vite_config


@pytest.mark.parametrize(
    "rel",
    [
        "routes/web.py",
        "routes/api.py",
        "asgi.py",
        "config/app.py",
        "config/database.py",
        "config/ai.py",
        "config/auth.py",
        "app/http/controllers/home_controller.py",
    ],
)
def test_scaffolded_python_parses(tmp_path, monkeypatch, rel):
    """Every generated Python file must at least be syntactically valid."""
    import ast

    _, root = _invoke(tmp_path, monkeypatch, "blog")
    source = (root / "blog" / rel).read_text()
    ast.parse(source)


def test_scaffolded_vite_config_is_valid_javascript(tmp_path, monkeypatch):
    """The vite template's doubled braces are for .format(); a missing format
    call ships literal {{ }} — invalid JS that kills `npm run dev`."""
    import shutil
    import subprocess

    _, root = _invoke(tmp_path, monkeypatch, "blog")
    vite = root / "blog" / "vite.config.js"
    if shutil.which("node") is None:  # pragma: no cover — dev/CI always has node
        source = vite.read_text()
        assert "{{" not in source and "}}" not in source
        return

    proc = subprocess.run(
        ["node", "--check", str(vite)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr


def test_new_refuses_when_target_exists_as_a_file(tmp_path, monkeypatch):
    """A plain file at the target must produce a clean refusal — not an
    unhandled NotADirectoryError traceback."""
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "blog").write_text("i am a file")
    result = CliRunner().invoke(cli_app, ["new", "blog"])

    assert result.exit_code != 0
    assert "blog" in result.output
    # A clean CLI error, not a crash: the exception is Typer's exit path.
    assert not isinstance(result.exception, NotADirectoryError)


def test_new_refuses_overlong_names(tmp_path, monkeypatch):
    """Names beyond filesystem component limits must fail cleanly up front."""
    result, _ = _invoke(tmp_path, monkeypatch, "a" * 300)
    assert result.exit_code != 0
    assert not isinstance(result.exception, OSError)


def test_new_dependency_carries_version_floor_when_installed(tmp_path, monkeypatch):
    """From a published install (no framework checkout on disk) the scaffolded
    app must pin a floor — ``fastplace>=<version>`` — never a bare specifier:
    a bare dependency silently tracks future breaking releases, and the npm
    side already caret-pins its published fallback (``^0.1.0``)."""
    from fastplace.cli import generators

    monkeypatch.setattr(generators, "_framework_checkout", lambda: None)
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    pyproject = (root / "blog" / "pyproject.toml").read_text()
    assert '"fastplace>=' in pyproject, pyproject
    # A bare specifier or a leaked local path is the regression.
    assert '"fastplace"' not in pyproject
    assert "file://" not in pyproject


def test_scaffolded_project_is_locally_installable(tmp_path, monkeypatch):
    """Running from the framework checkout, the generated project must wire
    local paths so both `pip install -e .` and `npm install` can resolve:
    a setuptools packages=[] block (flat-layout apps are not distributions)
    plus file: dependencies for the unpublished packages."""
    import fastplace

    checkout = Path(fastplace.__file__).resolve().parents[1]
    from_source_checkout = (checkout / "packages" / "react").is_dir()

    _, root = _invoke(tmp_path, monkeypatch, "blog")
    pyproject = (root / "blog" / "pyproject.toml").read_text()
    package_json = (root / "blog" / "package.json").read_text()

    # The flat-layout discovery error ("Multiple top-level packages discovered")
    # is disabled explicitly — the app is not a distribution.
    assert "[tool.setuptools]" in pyproject
    if from_source_checkout:
        assert f"fastplace @ file://{checkout}" in pyproject
        assert '"@fastplace/react": "file:' in package_json
    else:  # pragma: no cover — CI/dev always runs from the checkout
        assert '"fastplace>=' in pyproject
        assert '"@fastplace/react": "^' in package_json
