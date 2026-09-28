"""`fastplace about` must report the database the app will actually use."""

from __future__ import annotations

import os

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()

# Captured at import time, before any test in this module can run: the
# canary below must detect NEW environ writes, not flag values the
# operator's shell already exported into the pytest process (and not
# miss a leak that happens to overwrite a value with itself).
_BASELINE = {name: os.environ.get(name) for name in ("DATABASE_URL", "DATABASE_DRIVER", "APP_NAME")}


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
        "DATABASE_DRIVER=sqlite\nDATABASE_URL=postgresql://user:secret@localhost/ffw2_pg\n"
    )

    result = runner.invoke(cli_app, ["about"])

    assert result.exit_code == 0, result.output
    assert "postgresql" in result.output


def test_about_env_file_values_do_not_leak_into_later_tests(project):
    """`about` loads .env into os.environ — the process must not keep it.

    ``monkeypatch.delenv`` on an absent key tracks nothing, so a value
    dotenv writes mid-test survives every fixture teardown in pytest.
    Once leaked, a later test's manager build reads a foreign DATABASE_URL
    (the full-suite doctor/queue failures this pair once reproduced).
    """
    (project / ".env").write_text("DATABASE_URL=mysql://user:secret@localhost/ffw2_my\n")
    assert runner.invoke(cli_app, ["about"]).exit_code == 0


def test_no_database_url_leaks_between_cli_tests():
    """Runs after the leaker above: the process environ must be unchanged.

    Compares against the import-time baseline rather than None — a shell
    that exported DATABASE_URL into pytest would otherwise make this
    canary fail for the wrong reason (or pass while masking nothing).
    """
    for name, baseline in _BASELINE.items():
        assert os.environ.get(name) == baseline, f"{name} leaked between CLI tests"
