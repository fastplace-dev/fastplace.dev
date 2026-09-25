# tests/cli/test_maintenance_status.py
"""`maintenance:status` read-only 503-gate report (roadmap spec #5)."""

import json
import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    maintenance:status bootstraps config via load_env(), and python-dotenv
    writes the cwd .env's keys straight into the REAL os.environ — a
    mutation no monkeypatch sees or undoes. Snapshot before, restore after
    (verbatim pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich table width so detail cells never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "defaults only" split
    across cell lines. COLUMNS is read live per render, so pinning it here
    makes every table one-line-per-cell (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text("APP_ENV=local\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _state_path(root):
    from fastplace.http.maintenance import MAINTENANCE_FILE

    path = root / MAINTENANCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_maintenance_status_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "maintenance:status" in result.stdout


def test_up_when_no_state_file(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["maintenance:status"])
    assert result.exit_code == 0
    out = _out(result)
    assert "UP" in out


def test_down_reports_settings_verbatim_and_secret_presence_only(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    _state_path(root).write_text(
        json.dumps({"retry": 60, "refresh": 30, "secret": "hunter2-bypass-secret"}) + "\n"
    )
    result = runner.invoke(cli_app, ["maintenance:status"])
    assert result.exit_code == 0  # read-only report never breaks scripts
    out = _out(result)
    assert "DOWN" in out
    assert "retry=60" in out and "refresh=30" in out
    assert "hunter2-bypass-secret" not in out  # secret value NEVER printed
    assert re.search(r"secret[=:]\s*(yes|set|present)", out, re.IGNORECASE)


def test_down_reports_down_since_from_state_file_mtime(tmp_path, monkeypatch):
    import time

    root = _make_project(tmp_path, monkeypatch)
    state = _state_path(root)
    state.write_text(json.dumps({"retry": 60}) + "\n")
    result = runner.invoke(cli_app, ["maintenance:status"])
    assert result.exit_code == 0
    out = _out(result)
    # down-since surfaced as the state file's st_mtime ("state-file-last-written")
    assert "down-since" in out.lower()
    mtime = state.stat().st_mtime
    assert str(int(mtime)) in out or time.strftime("%Y-%m-%d", time.localtime(mtime)) in out


def test_corrupt_state_reports_fail_closed(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    _state_path(root).write_text("{not json\n")
    result = runner.invoke(cli_app, ["maintenance:status"])
    assert result.exit_code == 0  # without --exit-code always 0
    out = _out(result)
    assert "fail-closed" in out


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
def test_unreadable_state_file_reports_silent_reopen(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    state = _state_path(root)
    state.write_text("{}\n")
    state.chmod(0o000)
    result = runner.invoke(cli_app, ["maintenance:status"])
    assert result.exit_code == 0
    out = _out(result)
    # Present-but-unreadable = operational fault: the gate silently re-opens.
    assert "unreadable" in out or "re-open" in out or "reopens" in out


def test_exit_code_flag_maps_up_down_corrupt(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    # UP -> 0
    assert runner.invoke(cli_app, ["maintenance:status", "--exit-code"]).exit_code == 0
    # DOWN -> 1
    _state_path(root).write_text(json.dumps({"retry": 60}) + "\n")
    assert runner.invoke(cli_app, ["maintenance:status", "--exit-code"]).exit_code == 1
    # corrupt (fail-closed DOWN) -> 2
    _state_path(root).write_text("{not json\n")
    assert runner.invoke(cli_app, ["maintenance:status", "--exit-code"]).exit_code == 2


def test_help_documents_exit_codes():
    result = runner.invoke(cli_app, ["maintenance:status", "--help"])
    assert result.exit_code == 0
    out = _out(result)
    assert "--exit-code" in out
    assert "0" in out and "1" in out and "2" in out  # code meanings in help
