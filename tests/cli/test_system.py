# tests/cli/test_system.py
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli.system import iter_command_names

runner = CliRunner()


def test_list_raw_includes_known_commands():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    names = result.stdout.splitlines()
    assert "migrate" in names
    assert "make:model" in names
    assert "queue:work" in names


def test_list_grouped_shows_namespace_rows():
    result = runner.invoke(cli_app, ["list"])
    assert result.exit_code == 0
    assert "make" in result.stdout  # namespace column exists


def test_env_prints_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    result = runner.invoke(cli_app, ["env"])
    assert result.exit_code == 0
    assert "testing" in result.stdout


def test_iter_command_names_flat_and_nested():
    names = [n for n, _ in iter_command_names(cli_app)]
    assert "about" in names and "make:model" in names
