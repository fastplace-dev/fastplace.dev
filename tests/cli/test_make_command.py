"""``fastplace make:command`` + the app-commands bootstrap loader (spec #56).

The loader mounts ``app/commands/*_command.py`` at import of the root CLI,
so invocation tests run through the real bootstrap in a clean interpreter
(cwd=tmp project) — the same subprocess pattern test_provisioning.py uses.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()

_BOOTSTRAP = "from fastplace.cli import app; app()"

# Calls load_app_commands directly and prints its return value — used for
# the return-contract and no-op assertions.
_LOADER_SNIPPET = (
    "from pathlib import Path\n"
    "import typer\n"
    "from fastplace.cli import load_app_commands\n"
    "print(load_app_commands(typer.Typer(), Path.cwd()))\n"
)


def _generate(tmp_path: Path, monkeypatch, name: str = "Deploy") -> Path:
    """Scaffold a command module into the tmp project; return its root."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["make:command", name])
    assert result.exit_code == 0, result.output
    return tmp_path


def _run_cli(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real CLI bootstrap in a clean interpreter inside ``project``."""
    return subprocess.run(
        [sys.executable, "-c", _BOOTSTRAP, *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _run_snippet(project: Path, code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- make:command generator ---------------------------------------------------


def test_make_command_scaffolds_the_stub(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch, "Deploy")

    module = root / "app" / "commands" / "deploy_command.py"
    assert module.exists(), "expected app/commands/<snake>_command.py"
    source = module.read_text()
    assert 'command_app = typer.Typer(help="Deploy commands.")' in source
    assert '@command_app.command("deploy:run")' in source
    # The stub must be valid Python as written.
    compile(source, str(module), "exec")

    # Package markers so the bootstrap loader can import the module.
    assert (root / "app" / "commands" / "__init__.py").exists()
    assert (root / "app" / "__init__.py").exists()


def test_make_command_snake_cases_multi_word_names(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch, "HealthCheck")
    assert (root / "app" / "commands" / "health_check_command.py").exists()
    source = (root / "app" / "commands" / "health_check_command.py").read_text()
    assert '@command_app.command("health_check:run")' in source


def test_make_command_refuses_to_clobber(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    module = tmp_path / "app" / "commands" / "deploy_command.py"
    module.parent.mkdir(parents=True)
    module.write_text("# hand-written\n")

    result = runner.invoke(cli_app, ["make:command", "Deploy"])

    assert result.exit_code == 0, result.output
    assert module.read_text() == "# hand-written\n"
    assert "exists" in result.output


def test_make_command_rejects_invalid_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["make:command", "../evil"])
    assert result.exit_code == 1
    assert not (tmp_path / "app" / "commands").exists()


# --- loader: real-bootstrap invocation ---------------------------------------


def test_generated_command_runs_through_the_real_bootstrap(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch)

    proc = _run_cli(root, "deploy:run")

    assert proc.returncode == 0, proc.stderr
    # Exit 0 means the command mounted and executed — an unmounted name
    # would be Typer's "No such command" exit 2.
    assert "No such command" not in proc.stderr


def test_list_raw_includes_the_mounted_command(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch)

    proc = _run_cli(root, "list", "--raw")

    assert proc.returncode == 0, proc.stderr
    assert "deploy:run" in proc.stdout.splitlines()


# --- loader: defensive behavior -----------------------------------------------


def test_loader_skips_a_module_that_raises_at_import(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch)
    (root / "app" / "commands" / "broken_command.py").write_text("raise RuntimeError('boom')\n")

    proc = _run_cli(root, "list", "--raw")

    # A malformed module must never crash the CLI: healthy commands still
    # mount, and the bad module is reported as a warning.
    assert proc.returncode == 0, proc.stderr
    assert "deploy:run" in proc.stdout
    assert "broken_command.py" in proc.stdout


def test_loader_skips_a_module_without_command_app(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch)
    (root / "app" / "commands" / "vague_command.py").write_text("X = 1\n")

    proc = _run_cli(root, "list", "--raw")

    assert proc.returncode == 0, proc.stderr
    assert "vague_command.py" in proc.stdout
    assert "deploy:run" in proc.stdout


# --- loader: return contract ---------------------------------------------------


def test_loader_returns_mounted_command_names(tmp_path, monkeypatch):
    root = _generate(tmp_path, monkeypatch)

    proc = _run_snippet(root, _LOADER_SNIPPET)

    assert proc.returncode == 0, proc.stderr
    assert "deploy:run" in proc.stdout


def test_loader_noops_when_app_commands_is_absent(tmp_path):
    """The framework repo has no app/commands/ — the loader must no-op cleanly."""
    proc = _run_snippet(tmp_path, _LOADER_SNIPPET)

    assert proc.returncode == 0, proc.stderr
    assert "[]" in proc.stdout
