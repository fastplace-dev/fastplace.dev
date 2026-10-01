"""Shared CLI test fixtures — the tmp-project + captured-spawn harness."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeProc:
    """Just enough Popen for the runtimes' child management."""

    def __init__(self, command, env=None):
        self.command = [str(part) for part in command]
        self.env = env
        self.pid = None  # fake: no real process group to signal
        self.terminated = False
        self.returncode = 0

    def wait(self, timeout=None):  # noqa: ARG002 — signature parity
        self.returncode = 0
        return 0

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True


@pytest.fixture()
def spawned(monkeypatch, tmp_path):
    """Bare tmp project + captured Popen spawns + config bound to it.

    Yields a namespace with ``commands`` (spawned command lists) and
    ``envs`` (the environment dict passed to each spawn).
    """
    commands: list[list[str]] = []
    envs: list[dict] = []

    def fake_popen(command, *args, **kwargs):  # noqa: ANN002, ANN003
        proc = _FakeProc(command, env=kwargs.get("env"))
        commands.append(proc.command)
        envs.append(kwargs.get("env") or {})
        return proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # Hermetic port probe: runtime tests assert command wiring, not socket
    # state — a machine with 9000 (or any pinned port) occupied must not
    # redden them. Port-fallback tests re-patch with their own scenarios.
    from fastplace.cli import dev

    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: True)

    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "config" / "app.py").write_text("APP_NAME = 'W3'\n")
    (root / ".env").write_text("")
    original_cwd = Path.cwd()
    monkeypatch.chdir(root)

    # Fake interpreter + fastplace console script (spawned-fixture idiom
    # from test_lint_watch.py: no `python -m fastplace` exists).
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("fastplace", "python"):
        (fake_bin / name).write_text("#!/bin/sh\nexit 0\n")
        (fake_bin / name).chmod(0o755)
    original_executable = sys.executable
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))

    from fastplace.config import reset_config

    reset_config(root)
    saved_env = dict(os.environ)
    box = SimpleNamespace(commands=commands, envs=envs, root=root)
    yield box
    os.environ.clear()
    os.environ.update(saved_env)
    # Rebind config to the real checkout, not the (soon-vanishing) tmp
    # project — monkeypatch.chdir is undone only after this teardown.
    reset_config(original_cwd)
    monkeypatch.setattr(sys, "executable", original_executable)
