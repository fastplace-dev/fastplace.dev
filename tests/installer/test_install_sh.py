"""Behavior tests for installer/install.sh — run against the real script
in a sandbox HOME with stubbed uv/curl (see conftest)."""

from __future__ import annotations

from tests.installer.conftest import run_installer


def test_fresh_install_happy_path(sandbox):
    r = run_installer(sandbox.home)
    assert r.returncode == 0, r.stdout + r.stderr
    uv_args = (sandbox.home / "uv.log").read_text().strip().split()
    assert uv_args[:6] == [
        "tool",
        "install",
        "--upgrade",
        "--python",
        "3.12",
        "fastplace",
    ]
    assert "fastplace new" in r.stdout  # next-steps box
    assert "fastplace 0.4.3" in r.stdout  # verified version echoed
    rc = (sandbox.home / ".zshrc").read_text()
    assert "# fastplace-installer" in rc and ".local/bin" in rc


def test_piped_to_sh_still_runs_under_bash(sandbox):
    r = run_installer(sandbox.home, shell="sh")
    assert r.returncode == 0 and "fastplace new" in r.stdout


def test_uv_bootstrap_failure_aborts_clean(sandbox, failing_curl):
    r = run_installer(sandbox.home)
    assert r.returncode != 0
    # The script's own abort message — not a shell "file not found".
    assert "Failed to install uv" in r.stdout + r.stderr
    assert not (sandbox.home / ".local/bin/fastplace").exists()
