"""Task 27 CLI — `fastplace down` / `fastplace up` (spec #61).

`down` writes storage/framework/maintenance.json with ONLY the options the
operator passed; `up` removes it and is idempotent. `down` is a destructive
command, so it carries the production confirmation guard (the queue:clear
convention: confirm unless --force when APP_ENV=production, unset defaults
to production).
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.http.maintenance import MAINTENANCE_FILE

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401

# Rich colorizes output when the environment forces color; strip codes so
# assertions match on plain text.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A tmp Fastplace project — asgi.py marks the root for _project_root()."""
    (tmp_path / "asgi.py").write_text("app = None\n")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _state(root) -> dict:
    return json.loads((root / MAINTENANCE_FILE).read_text())


# ---------------------------------------------------------------------------
# down — writes only the fields the operator gave
# ---------------------------------------------------------------------------


def test_down_writes_only_the_given_fields(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["down", "--secret", "s", "--retry", "60"])
    assert result.exit_code == 0, result.output
    assert _state(project) == {"retry": 60, "secret": "s"}


def test_down_bare_writes_an_empty_state(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["down"])
    assert result.exit_code == 0, result.output
    assert _state(project) == {}


def test_down_writes_all_three_fields(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(
        cli_app, ["down", "--retry", "300", "--secret", "ops", "--refresh", "30"]
    )
    assert result.exit_code == 0, result.output
    assert _state(project) == {"retry": 300, "secret": "ops", "refresh": 30}


def test_down_reports_the_maintenance_mode(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["down"])
    assert result.exit_code == 0, result.output
    assert "maintenance" in ANSI_RE.sub("", result.output).lower()


# ---------------------------------------------------------------------------
# up — removes the file, idempotent when already up
# ---------------------------------------------------------------------------


def test_up_removes_the_state_file(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    runner.invoke(cli_app, ["down"])

    result = runner.invoke(cli_app, ["up"])
    assert result.exit_code == 0, result.output
    assert not (project / MAINTENANCE_FILE).exists()


def test_up_when_already_up_exits_zero(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")

    result = runner.invoke(cli_app, ["up"])
    assert result.exit_code == 0, result.output
    assert "already up" in ANSI_RE.sub("", result.output).lower()


# ---------------------------------------------------------------------------
# outside a project — friendly refusal
# ---------------------------------------------------------------------------


def test_down_outside_a_project_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)  # no asgi.py here

    result = runner.invoke(cli_app, ["down"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)


def test_up_outside_a_project_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["up"])
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# the production guard — both confirm paths, plus --force
# ---------------------------------------------------------------------------


def test_down_production_confirm_decline_refuses(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["down"], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in ANSI_RE.sub("", result.output)
    assert not (project / MAINTENANCE_FILE).exists()  # untouched


def test_down_production_confirm_accept_proceeds(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["down", "--retry", "60"], input="y\n")
    assert result.exit_code == 0, result.output
    assert _state(project) == {"retry": 60}


def test_down_production_force_skips_the_prompt(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    result = runner.invoke(cli_app, ["down", "--force"])
    assert result.exit_code == 0, result.output
    assert "Bring the production application down" not in result.output  # no prompt
    assert _state(project) == {}
