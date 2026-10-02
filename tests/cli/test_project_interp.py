"""Project-interpreter resolution for CLI-spawned children.

``fastplace`` may legitimately run from an environment that is not the
app's — a global ``uv tool install fastplace`` shim or a pipx install.
Every child the CLI spawns that must import the *project's* code
(uvicorn importing asgi.py, pytest importing the suite, the make:auth
bootstrap importing the fresh app) therefore resolves its interpreter
from the project root first and only falls back to the interpreter
running this CLI. Covers the helper itself plus the dev/serve/test/
scaffold spawn wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing as testing_mod
from fastplace.cli._interp import project_fastplace_bin, project_python

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)

runner = CliRunner()


def _make_venv(root: Path, *, windows: bool = False) -> tuple[Path, Path]:
    """A fake .venv layout; returns (script_dir, python path)."""
    parent = root / ".venv" / ("Scripts" if windows else "bin")
    parent.mkdir(parents=True)
    python = parent / ("python.exe" if windows else "python")
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    return parent, python


# --- project_python -------------------------------------------------------------


def test_project_python_prefers_project_venv(tmp_path):
    _, venv_python = _make_venv(tmp_path)
    assert project_python(tmp_path) == str(venv_python)


def test_project_python_falls_back_to_running_interpreter(tmp_path):
    assert project_python(tmp_path) == sys.executable


def test_project_python_windows_layout(tmp_path, monkeypatch):
    import fastplace.cli._interp as interp

    _, venv_python = _make_venv(tmp_path, windows=True)
    monkeypatch.setattr(interp, "_IS_WINDOWS", True)
    assert project_python(tmp_path) == str(venv_python)


# --- project_fastplace_bin -------------------------------------------------------


def test_project_fastplace_bin_prefers_venv_sibling(tmp_path):
    parent, _ = _make_venv(tmp_path)
    sibling = parent / "fastplace"
    sibling.write_text("#!/bin/sh\nexit 0\n")
    sibling.chmod(0o755)
    assert project_fastplace_bin(tmp_path) == str(sibling)


def test_project_fastplace_bin_falls_back_to_path(tmp_path, monkeypatch):
    # Move the running interpreter away from any real sibling script first —
    # this dev venv ships a fastplace next to its python.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python"))
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/local/bin/fastplace" if name == "fastplace" else None,
    )
    assert project_fastplace_bin(tmp_path) == "/usr/local/bin/fastplace"


def test_project_fastplace_bin_none_when_unresolved(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python"))
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert project_fastplace_bin(tmp_path) is None


# --- run dev / serve wiring -------------------------------------------------------


def test_run_dev_backend_runs_project_venv_python(spawned):
    _, venv_python = _make_venv(spawned.root)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert backend[0] == str(venv_python)


def test_run_dev_without_venv_keeps_running_interpreter(spawned):
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    # sys.executable is the fixture-faked interpreter — the historical behavior.
    assert backend[0] == sys.executable


def test_run_dev_lint_watcher_runs_project_venv_fastplace(spawned):
    parent, _ = _make_venv(spawned.root)
    venv_fastplace = parent / "fastplace"
    venv_fastplace.write_text("#!/bin/sh\nexit 0\n")
    venv_fastplace.chmod(0o755)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite"])
    assert result.exit_code == 0, result.output
    watcher = [cmd for cmd in spawned.commands if "lint:watch" in cmd][0]
    assert watcher == [str(venv_fastplace), "lint:watch"]


def test_serve_backend_runs_project_venv_python(spawned):
    _, venv_python = _make_venv(spawned.root)
    (spawned.root / ".env").write_text("APP_HOST=127.0.0.2\nAPP_PORT=8124\nAPP_WORKERS=1\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert serve_cmd[0] == str(venv_python)


# --- test:coverage / test:watch wiring ---------------------------------------------


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def test_test_coverage_runs_project_venv_python(tmp_path, monkeypatch):
    import json

    _, venv_python = _make_venv(tmp_path)
    (tmp_path / "asgi.py").write_text("")
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "coverage.json").write_text(json.dumps({"totals": {"percent_covered": 0}}))
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(testing_mod, "_subprocess_run", fake_run)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["test:coverage"])
    assert result.exit_code == 0, result.stdout
    assert calls[0][0] == str(venv_python)


def test_watch_once_runs_project_venv_python(tmp_path, monkeypatch):
    _, venv_python = _make_venv(tmp_path)
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text("APP_ENV=local\n")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        testing_mod, "_subprocess_run", lambda argv, **kw: calls.append(list(argv)) or _Proc()
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["test:watch", "--once"])
    assert result.exit_code == 0, result.stdout
    pytest_cmd = [c for c in calls if "pytest" in c][0]
    assert pytest_cmd[0] == str(venv_python)


# --- fastplace new scaffold bootstrap -----------------------------------------------


def test_users_migration_bootstrap_runs_project_venv_python(tmp_path, monkeypatch):
    from fastplace.cli import generators as generators_mod

    _, venv_python = _make_venv(tmp_path)
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = "database/migrations/2026_01_01_000000_create_users_table.py\n"
        stderr = ""

    def fake_run(argv, *args, **kwargs):  # noqa: ANN002, ANN003
        calls.append([str(part) for part in argv])
        return _Proc()

    monkeypatch.setattr(generators_mod.subprocess, "run", fake_run)
    made, error = generators_mod._bootstrap_users_migration(tmp_path)
    assert error == ""
    assert made.endswith("create_users_table.py")
    assert calls[0][0] == str(venv_python)


def test_users_migration_bootstrap_reports_failure(tmp_path, monkeypatch):
    from fastplace.cli import generators as generators_mod

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(generators_mod.subprocess, "run", lambda argv, **kw: _Proc())
    made, error = generators_mod._bootstrap_users_migration(tmp_path)
    assert made == ""
    assert error == "boom"
