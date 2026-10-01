# tests/cli/test_test_e2e.py
"""`test:e2e` Playwright preflight + npx forwarding (roadmap spec #17)."""

import os
import re
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing as testing_mod

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ(tmp_path, monkeypatch):
    """Confine os.environ changes to the test that caused them.

    test:e2e bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41). The config registry
    gets the same treatment: test:e2e binds it to the tmp project root,
    and a binding that outlives its directory makes later suites read a
    vanished project's defaults.
    """
    from fastplace.config import reset_config

    original_cwd = os.getcwd()
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)
    reset_config(Path(original_cwd))


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich table width so detail cells never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "defaults only" split
    across cell lines. COLUMNS is read live per render, so pinning it here
    makes every table one-line-per-cell (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture
def calls(monkeypatch):
    """Dispatch fake: dry-run gets crafted stdout; the real run records argv."""
    seen: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        seen.append(list(argv))
        if argv[1:3] == ["playwright", "install"]:
            return _Proc(stdout="chromium 1155 build not needed\n")
        return _Proc(returncode=0)

    monkeypatch.setattr(testing_mod, "_subprocess_run", fake_run)
    return seen


def _make_project(tmp_path, monkeypatch, with_node=True):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text("APP_ENV=local\n")
    if with_node:
        (tmp_path / "package.json").write_text("{}\n")
        (tmp_path / "node_modules").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_test_e2e_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "test:e2e" in result.stdout


def test_forwards_to_npx_playwright_test(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/local/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 0, result.stdout
    forwarded = calls[-1]
    assert forwarded[:3] == ["npx", "playwright", "test"]
    assert "npm" not in forwarded  # NEVER npm run test:e2e (it rebuilds)


def test_forwarded_args_pass_through(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    result = runner.invoke(cli_app, ["test:e2e", "--grep", "auth", "--workers=1"])
    assert result.exit_code == 0, result.stdout
    assert calls[-1][3:] == ["--grep", "auth", "--workers=1"]


def test_child_exit_code_inherited(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )

    def fail_run(argv, *args, **kwargs):
        if argv[1:3] == ["playwright", "install"]:
            return _Proc(stdout="chromium ok\n")
        return _Proc(returncode=7)

    monkeypatch.setattr(testing_mod, "_subprocess_run", fail_run)
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 7


def test_missing_npx_is_actionable(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: None, raising=False)
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 1
    out = _out(result)
    assert "npx" in out and "PATH" in out


def test_busy_port_suggests_e2e_port_override_never_kill(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    # Hold an OS-assigned ephemeral port and point E2E_PORT at it — the
    # preflight sees exactly this port taken. (The test used to occupy the
    # fixed default 8907, which a real dev server on this machine also
    # wants, and skipped whenever it lost that race.)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    busy_port = blocker.getsockname()[1]
    blocker.listen(1)
    monkeypatch.setenv("E2E_PORT", str(busy_port))
    try:
        result = runner.invoke(cli_app, ["test:e2e"])
    finally:
        blocker.close()
    assert result.exit_code == 1
    out = _out(result)
    assert "E2E_PORT" in out
    assert str(busy_port) in out
    assert "kill" not in out.lower()  # we suggest the override, never killing processes


def test_free_port_proceeds(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    monkeypatch.setenv("E2E_PORT", "8941")  # nothing binds this
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 0, result.stdout
    assert calls[-1][:3] == ["npx", "playwright", "test"]


def test_missing_chromium_warns_and_suggests_install(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )

    def dry_says_missing(argv, *args, **kwargs):
        if argv[1:3] == ["playwright", "install"]:
            return _Proc(stdout="chromium 1155 is not downloaded (from https://…)\n")
        return _Proc(returncode=0)

    monkeypatch.setattr(testing_mod, "_subprocess_run", dry_says_missing)
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 0, result.stdout  # warn-level: Playwright itself will fail loudly
    out = _out(result)
    assert "chromium" in out
    assert "npx playwright install chromium" in out


def test_missing_package_json_is_actionable(tmp_path, monkeypatch, calls):
    _make_project(tmp_path, monkeypatch, with_node=False)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 1
    assert "package.json" in _out(result)


def test_storage_created_when_absent(tmp_path, monkeypatch, calls):
    root = _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 0, result.stdout
    assert (root / "storage").is_dir()  # present-or-creatable: sqlite needs the parent dir


def test_reports_which_fastplace_binary_webserver_resolves(tmp_path, monkeypatch, calls):
    root = _make_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    result = runner.invoke(cli_app, ["test:e2e"])
    out = _out(result)
    venv_bin = root / ".venv" / "bin" / "fastplace"
    expected = str(venv_bin) if venv_bin.exists() else "fastplace (PATH)"
    assert expected in out  # playwright.config.mjs:13-14 resolution surfaced
