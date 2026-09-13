"""CLI smoke tests — command wiring via Typer's CliRunner."""

from fastplace.cli import app as cli_app


def test_about_runs(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("APP_NAME", "TestApp")
    runner = CliRunner()
    result = runner.invoke(cli_app, ["about"])
    assert result.exit_code == 0
    assert "Fastplace" in result.output


def test_run_dev_help_lists_options():
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["run", "dev", "--help"])
    assert result.exit_code == 0
    assert "--skip-vite" in result.output


def test_serve_help_lists_options():
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--skip-build" in result.output
