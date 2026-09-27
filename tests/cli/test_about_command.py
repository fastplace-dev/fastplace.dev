"""`fastplace about` must report the database the app will actually use."""

from __future__ import annotations

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_DRIVER", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_about_shows_driver_derived_from_env_file_url(project):
    (project / ".env").write_text("DATABASE_URL=mysql://user:secret@localhost/ffw2_my\n")

    result = runner.invoke(cli_app, ["about"])

    assert result.exit_code == 0, result.output
    assert "mysql" in result.output


def test_about_ignores_stale_driver_line_when_url_switched(project):
    """db:configure wrote the sqlite pair; the operator switched only the URL."""
    (project / ".env").write_text(
        "DATABASE_DRIVER=sqlite\n"
        "DATABASE_URL=postgresql://user:secret@localhost/ffw2_pg\n"
    )

    result = runner.invoke(cli_app, ["about"])

    assert result.exit_code == 0, result.output
    assert "postgresql" in result.output
