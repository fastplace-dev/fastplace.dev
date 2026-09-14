"""Dev-loop boundary lint — `lint:watch` and its `run dev` wiring.

The blueprint (§4 "Enforcing Boundaries") wants the module-boundary check
in `fastplace run dev` for instant feedback on save, next to the existing
hard `lint:modules` gate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "proj"
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


# Minimal boundary-clean module layout (the full rules live in
# tests/modules/test_lint_modules.py — here we only need one clean and one
# violating project to drive the watcher).
CLEAN_PROJECT = {
    "app/modules/billing/__init__.py": "",
    "app/modules/billing/models/invoice.py": (
        "from fastplace.orm import Model\n\n\nclass Invoice(Model):\n    pass\n"
    ),
    "app/modules/billing/services/invoice_service.py": ("from ..models.invoice import Invoice\n"),
    "app/modules/accounts/__init__.py": "",
    "app/modules/accounts/services/ledger_service.py": (
        "from app.modules.billing.services.invoice_service import InvoiceService\n"
    ),
    "app/http/controllers/invoices_controller.py": (
        "from app.modules.billing.services.invoice_service import InvoiceService\n"
    ),
}


# --- what counts as a boundary-relevant save -------------------------------


def test_relevant_change_only_triggers_on_app_python_edits():
    from fastplace.cli.lint import _relevant_change

    assert _relevant_change("app/modules/billing/services/invoice_service.py") is True
    assert _relevant_change(str(Path.cwd() / "app" / "http" / "controllers" / "x.py")) is True
    # routes/ files are boundary-relevant too — lint_imports scans them, so a
    # route file importing a repository directly is exactly what the watcher
    # must catch.
    assert _relevant_change("routes/web.py") is True
    assert _relevant_change(str(Path.cwd() / "routes" / "api.py")) is True

    # Not Python under app/ or routes/ — a relint would be noise.
    assert _relevant_change("app/resources/js/pages/Index.jsx") is False
    assert _relevant_change("README.md") is False
    assert _relevant_change("tests/orm/test_query.py") is False
    assert _relevant_change("resources/css/app.css") is False


def test_watch_dirs_subscribe_to_app_and_routes_only(tmp_path):
    """The watcher must subscribe to exactly app/ + routes/, never the repo
    root — root-level watching drags .venv/node_modules/.git into inotify
    (thousands of handles) for directories the lint never reads."""
    from fastplace.cli.lint import _watch_dirs

    # CLEAN_PROJECT has app/ but no routes/ — a missing directory is skipped,
    # not handed to watchfiles (which would raise FileNotFoundError).
    root = make_project(tmp_path, CLEAN_PROJECT)
    assert _watch_dirs(root) == [root / "app"]

    (root / "routes").mkdir()
    assert _watch_dirs(root) == [root / "app", root / "routes"]

    # A bare project with neither directory degrades to "nothing to watch".
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _watch_dirs(empty) == []


# --- one-shot lint pass (the `--once` smoke door) ---------------------------


def test_lint_watch_once_is_clean_on_a_clean_project(tmp_path, monkeypatch):
    root = make_project(tmp_path, CLEAN_PROJECT)
    monkeypatch.chdir(root)

    from fastplace.cli import app as cli_app

    result = runner.invoke(cli_app, ["lint:watch", "--once"])
    assert result.exit_code == 0, result.output
    assert "clean" in result.output


def test_lint_watch_once_reports_violations(tmp_path, monkeypatch):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/leaky_service.py"] = (
        "from app.modules.billing.models.invoice import Invoice\n"
    )
    root = make_project(tmp_path, files)
    monkeypatch.chdir(root)

    from fastplace.cli import app as cli_app

    result = runner.invoke(cli_app, ["lint:watch", "--once"])
    assert result.exit_code == 1
    assert "leaky_service.py" in result.output


# --- `run dev` wiring --------------------------------------------------------


class _FakeProc:
    """Just enough Popen for run_dev's child management."""

    def __init__(self, command):
        self.command = command
        self.terminated = False

    def wait(self, timeout=None):  # noqa: ARG002 — signature parity
        return 0

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True


@pytest.fixture()
def spawned(monkeypatch, tmp_path):
    """Capture run_dev's child spawns; cwd is a bare project (no package.json,
    so no Vite child) with a fake `fastplace` binary next to sys.executable."""
    commands: list[list[str]] = []

    def fake_popen(command, *args, **kwargs):  # noqa: ANN002, ANN003
        proc = _FakeProc(command)
        commands.append([str(part) for part in command])
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    bare = tmp_path / "bare"
    (bare / "config").mkdir(parents=True)
    (bare / "config" / "app.py").write_text("APP_NAME = 'Bare'\n")
    monkeypatch.chdir(bare)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "fastplace").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "fastplace").chmod(0o755)
    original_executable = sys.executable
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))
    (fake_bin / "python").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "python").chmod(0o755)
    yield commands
    monkeypatch.setattr(sys, "executable", original_executable)


def _invoked(commands, *extra):
    from fastplace.cli import app as cli_app

    result = runner.invoke(cli_app, ["run", "dev", *extra])
    assert result.exit_code == 0, result.output
    return commands


def test_run_dev_spawns_the_lint_watcher_by_default(spawned):
    commands = _invoked(spawned)
    watchers = [cmd for cmd in commands if "lint:watch" in cmd]
    assert len(watchers) == 1
    # The watcher runs through the fastplace console script, not the module
    # path (there is no `python -m fastplace`).
    assert watchers[0][-1] == "lint:watch"
    assert watchers[0][0].endswith("fastplace")


def test_run_dev_skip_lint_omits_the_watcher(spawned):
    commands = _invoked(spawned, "--skip-lint")
    assert all("lint:watch" not in cmd for cmd in commands)
    # uvicorn still runs — only the watcher is opted out.
    assert any("uvicorn" in cmd for cmd in commands)
