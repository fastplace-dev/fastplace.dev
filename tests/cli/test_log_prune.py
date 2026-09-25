# tests/cli/test_log_prune.py
"""`log:prune` old-log inventory + removal (roadmap spec #12)."""

import os
import re
import time

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

    log:prune bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so phrasing never wraps mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and listing lines split across wrapped
    edges. COLUMNS is read live per render, so pinning it here keeps every
    line intact (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _make_project(tmp_path, monkeypatch, env_text="APP_ENV=local\n"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_log(root, name, content="x" * 100, age_days=10):
    logs = root / "storage" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / name
    path.write_text(content)
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))
    return path


def test_log_prune_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "log:prune" in result.stdout


def test_default_lists_without_deleting(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    _write_log(root, "app.log")
    result = runner.invoke(cli_app, ["log:prune"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "app.log" in out
    assert (root / "storage" / "logs" / "app.log").exists()  # list-only


def test_contents_never_printed(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    pii = "alice@example.test bought widget #4412"
    _write_log(root, "mail.log", content=f"TO: {pii}\n")
    result = runner.invoke(cli_app, ["log:prune"])
    assert result.exit_code == 0, result.stdout
    assert "alice@example.test" not in _out(result)  # names/sizes only, never content


def test_days_removes_only_older_files(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    old = _write_log(root, "old.log", age_days=30)
    fresh = _write_log(root, "fresh.log", age_days=1)
    result = runner.invoke(cli_app, ["log:prune", "--days", "7", "--force"])
    assert result.exit_code == 0, result.stdout
    assert not old.exists()
    assert fresh.exists()


def test_days_zero_removes_all_logs(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    _write_log(root, "a.log", age_days=0.01)
    result = runner.invoke(cli_app, ["log:prune", "--days", "0", "--force"])
    assert result.exit_code == 0, result.stdout
    assert not (root / "storage" / "logs" / "a.log").exists()


def test_non_log_files_untouched(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    logs = root / "storage" / "logs"
    logs.mkdir(parents=True)
    (logs / "keep.txt").write_text("not a log")
    result = runner.invoke(cli_app, ["log:prune", "--days", "0", "--force"])
    assert result.exit_code == 0, result.stdout
    assert (logs / "keep.txt").exists()  # glob is *.log only


def test_bytes_and_count_reported(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    _write_log(root, "b1.log", content="y" * 2048, age_days=30)
    _write_log(root, "b2.log", content="y" * 1024, age_days=30)
    result = runner.invoke(cli_app, ["log:prune", "--days", "7", "--force"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "3072" in out          # bytes freed (2048 + 1024)
    assert re.search(r"2 (log )?files?", out)  # files removed


def test_production_guard_prompts_without_force(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env_text="APP_ENV=production\n")
    _write_log(root, "prod.log", age_days=30)
    result = runner.invoke(cli_app, ["log:prune", "--days", "7"], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in _out(result)
    assert (root / "storage" / "logs" / "prod.log").exists()  # nothing deleted


def test_absent_logs_dir_friendly_empty(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)  # no storage/logs created
    result = runner.invoke(cli_app, ["log:prune"])
    assert result.exit_code == 0, result.stdout
    assert "no log files" in _out(result).lower() or "0 log" in _out(result).lower()


def test_help_documents_file_granularity():
    result = runner.invoke(cli_app, ["log:prune", "--help"])
    assert result.exit_code == 0
    out = _out(result)
    assert "--days" in out
    assert "file" in out.lower()  # whole-file granularity stated in help
