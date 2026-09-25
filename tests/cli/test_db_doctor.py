"""`db:doctor` — DB stack diagnosis (roadmap spec, data plane)."""

from __future__ import annotations

import os
import re

import pytest
from _isolation import (  # noqa: F401 — autouse + fixture by name
    isolate_project_state,
    park_project_modules,
)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so fix/detail hints never wrap mid-word."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _restored_config():
    """Rebind the process config singleton after each test.

    db:doctor binds the registry to the invoked project (reset_config(root));
    these tmp projects carry no config/ package — later suites would inherit
    an empty-registry singleton. Snapshot and rebind (same pattern as
    test_log_prune).
    """
    import fastplace.config as config_module

    saved = config_module._default_config
    yield
    config_module._default_config = saved


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


def _make_project(tmp_path, monkeypatch, env_text: str = "") -> object:
    """A cwd carrying the project marker + .env; chdir'd into."""
    # The developer shell may export DATABASE_URL — load_dotenv never
    # overrides real env, so clear it or the "missing URL" case would pass.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "asgi.py").write_text("# marker\n")
    if env_text:
        (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_db_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "db:doctor" in result.stdout


def test_db_doctor_help_exits_zero():
    result = runner.invoke(cli_app, ["db:doctor", "--help"])
    assert result.exit_code == 0
    assert "usage" in _out(result).lower()


def test_sqlite_project_all_checks_pass(tmp_path, monkeypatch):
    """A coherent sqlite project with one applied-looking revision: exit 0."""
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text="DATABASE_URL=sqlite+aiosqlite:///./test.sqlite3\n",
    )
    versions = root / "database" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0001_initial.py").write_text(
        '"""initial"""\nrevision = "0001"\ndown_revision = None\n'
    )

    result = runner.invoke(cli_app, ["db:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "driver" in out.lower()
    assert "FAIL" not in out


def test_missing_database_url_fails_with_fix_hint(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)  # no .env at all

    result = runner.invoke(cli_app, ["db:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "DATABASE_URL" in out
    assert ".env" in out  # the fix names where to set it


def test_unknown_scheme_fails_with_hint(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="DATABASE_URL=oracle+thin://u:p@h/db\n")

    result = runner.invoke(cli_app, ["db:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "oracle" in out.lower() or "unknown" in out.lower()


def test_missing_migrations_scaffold_fails_with_configure_hint(tmp_path, monkeypatch):
    _make_project(
        tmp_path,
        monkeypatch,
        env_text="DATABASE_URL=sqlite+aiosqlite:///./test.sqlite3\n",
    )  # no database/migrations tree

    result = runner.invoke(cli_app, ["db:doctor"])

    assert result.exit_code == 1
    assert "db:configure" in _out(result)  # the exact fix command


def test_empty_versions_dir_warns_not_fails(tmp_path, monkeypatch):
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text="DATABASE_URL=sqlite+aiosqlite:///./test.sqlite3\n",
    )
    (root / "database" / "migrations" / "versions").mkdir(parents=True)

    result = runner.invoke(cli_app, ["db:doctor"])

    # Scaffold present but no revisions: a warning, not a failure (exit 0),
    # with the make:migration hint.
    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "make:migration" in out


def test_vector_field_on_non_postgres_fails(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param; pytest resolves the imported fixture by name
    """VectorField declared while DATABASE_URL is sqlite: the silent killer."""
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text="DATABASE_URL=sqlite+aiosqlite:///./test.sqlite3\n",
    )
    versions = root / "database" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0001_initial.py").write_text(
        '"""initial"""\nrevision = "0001"\ndown_revision = None\n'
    )
    # A model with a VectorField column, importable through the app package.
    # Same declaration form as test_ai_ops' VECTOR_MODEL_TEMPLATE: Model
    # supplies the primary key; the annotation carries the column type.
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "models").mkdir(parents=True)
    (root / "app" / "models" / "__init__.py").write_text("")
    (root / "app" / "models" / "db_doctor_docs.py").write_text(
        "from fastplace.orm import Model\n"
        "from fastplace.orm import VectorField\n"
        "\n"
        "\n"
        "class Doc(Model):\n"
        '    __tablename__ = "db_doctor_docs"\n'
        "\n"
        '    body: "list[float] | None" = VectorField(dimensions=8)\n'
    )
    (root / "app" / "models" / "__init__.py").write_text(
        "from .db_doctor_docs import Doc\n"  # noqa: F401
    )

    result = runner.invoke(cli_app, ["db:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "postgres" in out.lower()  # names the requirement


def test_no_vector_fields_passes_quietly(tmp_path, monkeypatch):
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text="DATABASE_URL=sqlite+aiosqlite:///./test.sqlite3\n",
    )
    versions = root / "database" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0001_initial.py").write_text(
        '"""initial"""\nrevision = "0001"\ndown_revision = None\n'
    )

    result = runner.invoke(cli_app, ["db:doctor"])

    assert result.exit_code == 0, _out(result)
    assert "FAIL" not in _out(result)
