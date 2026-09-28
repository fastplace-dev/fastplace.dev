"""supp-1-G9 — ``make:test`` repairs plain scaffolds into a runnable suite.

A plain project has no ``tests/conftest.py`` and no pytest configuration:
the old generator emitted async stubs nothing could execute. The repaired
command ensures pytest tooling in pyproject.toml (once, never clobbering),
emits sync stubs under ``tests/unit/``, and emits app-driven async stubs
under ``tests/feature/`` with ``--feature``. The app conftest itself only
lands on scaffold-shaped projects — elsewhere its imports would shadow the
plugin's working fixtures with ModuleNotFoundError.
"""

from __future__ import annotations

import pytest

from fastplace.cli import app as cli_app


def _invoke(monkeypatch, tmp_path, *args: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, list(args))


def test_make_test_enables_tooling_without_scaffold_shape(tmp_path, monkeypatch):
    """A non-scaffold project gains the pytest tooling (the stub must be
    runnable) but NOT the app conftest — that template imports routes.web
    and the accounts model, and on a project without them it would shadow
    the plugin's working fixtures with a ModuleNotFoundError."""
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output

    assert not (tmp_path / "tests" / "conftest.py").exists(), (
        "conftest without routes/web.py would break every test via import errors"
    )
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "[tool.pytest.ini_options]" in pyproject
    assert 'asyncio_mode = "auto"' in pyproject
    assert "pytest>=8" in pyproject


def test_scaffold_shaped_project_still_gets_the_app_conftest(tmp_path, monkeypatch):
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "web.py").write_text("")
    user_model = tmp_path / "app" / "modules" / "accounts" / "models"
    user_model.mkdir(parents=True)
    (user_model / "user.py").write_text("")
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output

    conftest = tmp_path / "tests" / "conftest.py"
    assert conftest.is_file(), "the scaffold shape earns the app conftest"
    source = conftest.read_text()
    assert "purge_app_modules" in source
    assert "_fresh_singletons" in source


def test_unit_stub_is_a_sync_def(tmp_path, monkeypatch):
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output
    source = (tmp_path / "tests" / "unit" / "test_invoice.py").read_text()
    assert "def test_invoice(" in source
    assert "async def test_invoice(" not in source


def test_test_suffix_never_doubles_in_the_file_name(tmp_path, monkeypatch):
    """`InvoiceTest` names the test; the emitted file is test_invoice.py."""
    result = _invoke(monkeypatch, tmp_path, "make:test", "InvoiceTest")
    assert result.exit_code == 0, result.output
    source = (tmp_path / "tests" / "unit" / "test_invoice.py").read_text()
    assert "def test_invoice(" in source
    assert "test_invoice_test" not in source
    assert not (tmp_path / "tests" / "unit" / "test_invoice_test.py").exists()


def test_feature_flag_targets_tests_feature_with_client(tmp_path, monkeypatch):
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice", "--feature")
    assert result.exit_code == 0, result.output
    feature = tmp_path / "tests" / "feature" / "test_invoice.py"
    assert feature.is_file()
    source = feature.read_text()
    assert "async def test_invoice(" in source
    assert "client" in source
    assert not (tmp_path / "tests" / "http" / "test_invoice.py").exists()


def test_feature_stub_demos_the_fluent_assertions(tmp_path, monkeypatch):
    """The emitted stub teaches the assertion style the docs document."""
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice", "--feature")
    assert result.exit_code == 0, result.output
    source = (tmp_path / "tests" / "feature" / "test_invoice.py").read_text()
    assert "assert_ok()" in source
    assert "response.status_code ==" not in source


def test_existing_conftest_is_never_clobbered(tmp_path, monkeypatch):
    conftest = tmp_path / "tests" / "conftest.py"
    conftest.parent.mkdir(parents=True)
    conftest.write_text("# SENTINEL — hand-written bootstrap must survive\n")
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output
    assert "SENTINEL" in conftest.read_text()


def test_second_run_is_idempotent(tmp_path, monkeypatch):
    for _ in range(2):
        result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
        assert result.exit_code == 0, result.output
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert pyproject.count("[tool.pytest.ini_options]") == 1
    assert pyproject.count("dev = [") == 1


def test_make_test_works_without_a_pyproject(tmp_path, monkeypatch):
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "tests" / "unit" / "test_invoice.py").is_file()
    import tomllib

    data = tomllib.loads((tmp_path / "pyproject.toml").read_text())
    assert data["project"]["name"]  # slugified cwd, valid PEP 621 name
    assert "ini_options" in data["tool"]["pytest"]


@pytest.mark.parametrize("argv", [["make:test", "Invoice"], ["make:test", "Invoice", "--feature"]])
def test_emitted_files_are_valid_python(tmp_path, monkeypatch, argv):
    result = _invoke(monkeypatch, tmp_path, *argv)
    assert result.exit_code == 0, result.output
    for path in tmp_path.rglob("test_*.py"):
        compile(path.read_text(), str(path), "exec")


def test_pyproject_with_other_tool_tables_is_not_corrupted(tmp_path, monkeypatch):
    """Tooling tables the project already declares are never appended twice.

    A pyproject with ``[project.optional-dependencies]`` and ``[tool.ruff]``
    but no pytest block must end up valid TOML — a duplicated table header
    makes pip/uv/mypy refuse the whole project.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "\n"
        "[project.optional-dependencies]\n"
        'cli = ["typer>=0.12"]\n'
        "\n"
        "[tool.ruff]\n"
        "line-length = 88\n"
    )
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output

    import tomllib

    content = (tmp_path / "pyproject.toml").read_text()
    data = tomllib.loads(content)  # must parse — raises TOMLDecodeError otherwise
    assert data["tool"]["pytest"]["ini_options"] == {
        "asyncio_mode": "auto",
        "testpaths": ["tests"],
    }
    # The pre-existing tables survive untouched, declared exactly once.
    assert data["project"]["optional-dependencies"]["cli"] == ["typer>=0.12"]
    assert data["tool"]["ruff"]["line-length"] == 88
    assert content.count("[project.optional-dependencies]") == 1
    assert content.count("[tool.ruff]") == 1


def test_existing_tables_are_not_duplicated_per_section(tmp_path, monkeypatch):
    """A project that already has ``[tool.mypy]`` keeps it; the rest lands."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.mypy]\ncheck_untyped_defs = true\n'
    )
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output

    import tomllib

    content = (tmp_path / "pyproject.toml").read_text()
    data = tomllib.loads(content)
    assert data["tool"]["mypy"] == {"check_untyped_defs": True}
    assert content.count("[tool.mypy]") == 1
    assert "[tool.pytest.ini_options]" in content
    assert "[project.optional-dependencies]" in content
    assert "[tool.ruff]" in content


def test_unparseable_pyproject_is_left_alone(tmp_path, monkeypatch):
    """Broken TOML is never appended to — the file stays byte-identical."""
    broken = 'name = "demo"\n[project\n'
    (tmp_path / "pyproject.toml").write_text(broken)
    result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "pyproject.toml").read_text() == broken


def test_bootstrap_pyproject_name_is_slugified(tmp_path, monkeypatch):
    """A cwd like ``My App`` must not become an invalid PEP 621 name."""
    project = tmp_path / "My App"
    project.mkdir()
    result = _invoke(monkeypatch, project, "make:test", "Invoice")
    assert result.exit_code == 0, result.output

    import tomllib

    data = tomllib.loads((project / "pyproject.toml").read_text())
    assert data["project"]["name"] == "my-app"
