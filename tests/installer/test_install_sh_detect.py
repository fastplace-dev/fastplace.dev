"""Task 4 — non-uv shadow warning, --help/--version, brand guardrail."""

from __future__ import annotations

import re

from tests.installer.conftest import INSTALLER_SH, _STUB_UV, run_installer


def _with_uv(sandbox):
    sandbox.write_stub("uv", _STUB_UV.replace("$SANDBOX_HOME", str(sandbox.home)))


def test_warns_on_non_uv_fastplace(sandbox):
    _with_uv(sandbox)
    # A fastplace that lives outside ~/.local/bin — pip global, pipx, brew.
    other = sandbox.stub_bin / "fastplace"
    other.write_text("#!/usr/bin/env bash\necho fastplace 0.1.0\n")
    other.chmod(0o755)
    r = run_installer(sandbox.home)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "fastplace is already installed" in out
    assert str(other) in out
    assert other.exists()  # untouched


def test_help_and_version(sandbox):
    help_r = run_installer(sandbox.home, args=("--help",))
    assert help_r.returncode == 0
    for flag in ("--mcp", "--no-modify-path", "--help", "--version"):
        assert flag in help_r.stdout
    version_r = run_installer(sandbox.home, args=("--version",))
    assert version_r.returncode == 0
    assert "1.0.0" in version_r.stdout
    # help/version short-circuit: no install machinery runs
    assert not (sandbox.home / "uv.log").exists()


def test_no_forbidden_words():
    script = INSTALLER_SH.read_text()
    help_out = run_installer_help_text()
    forbidden = re.compile(r"laravel|eloquent", re.IGNORECASE)
    assert not forbidden.search(script), "installer script mentions banned brand"
    assert not forbidden.search(help_out), "installer help output mentions banned brand"


def run_installer_help_text() -> str:
    import subprocess

    return subprocess.run(
        ["bash", str(INSTALLER_SH), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
