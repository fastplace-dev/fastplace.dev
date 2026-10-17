"""Post-review polish fixes: paste-safe uninstall, --help before the
platform guard, bare-name $0 never re-exec'd, readable unset-HOME error."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from tests.installer.conftest import INSTALLER_SH, _sandbox_env, run_installer


def _with_uv(sandbox):
    sandbox.write_stub(
        "uv",
        (sandbox.stub_bin / "uv-staged").read_text().replace("$SANDBOX_HOME", str(sandbox.home)),
    )


def test_uninstall_hint_is_one_paste_safe_line(sandbox):
    _with_uv(sandbox)
    r = run_installer(sandbox.home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "uninstall: uv tool uninstall fastplace" in r.stdout
    lines = [line.strip() for line in r.stdout.splitlines()]
    assert not any(line.startswith("&&") for line in lines)


def test_help_works_on_unsupported_platform(sandbox):
    """--help must answer even where the installer refuses to install."""
    fake_uname = sandbox.stub_bin / "uname"
    fake_uname.write_text(
        '#!/usr/bin/env bash\nif [ "$1" = "-s" ]; then echo FreeBSD; else echo amd64; fi\n'
    )
    fake_uname.chmod(0o755)
    r = run_installer(sandbox.home, args=("--help",))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Usage:" in r.stdout
    assert "--mcp" in r.stdout
    assert "Unsupported platform" not in r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("dash"), reason="dash absent (brew install dash)")
def test_bare_dollar_zero_never_execs_a_cwd_file(sandbox, tmp_path):
    """$0="ash" (any unlisted stdin-shell name) with a planted ./ash in
    cwd must be refused — never `exec bash ash`. A dash symlink named
    'ash' reproduces the exotic-shell $0 exactly."""
    _with_uv(sandbox)
    # A symlink named "ash" on PATH gives a real unlisted stdin-shell $0;
    # executing it by bare name is exactly the exotic-shell attack shape.
    dash = shutil.which("dash")
    os.symlink(dash, sandbox.stub_bin / "ash")
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    planted = workdir / "ash"
    planted.write_text("#!/usr/bin/env bash\necho PWNED\n")
    planted.chmod(0o755)
    env = _sandbox_env(sandbox.home)
    r = subprocess.run(
        ["ash"],
        input=INSTALLER_SH.read_text(),
        capture_output=True,
        text=True,
        env=env,
        cwd=workdir,
        timeout=60,
    )
    assert "PWNED" not in r.stdout + r.stderr
    assert r.returncode == 2
    assert "bash" in r.stdout + r.stderr  # pipe-to-bash guidance


def test_unset_home_fails_with_readable_message(sandbox, tmp_path):
    _with_uv(sandbox)
    env = _sandbox_env(sandbox.home)
    env.pop("HOME", None)
    r = subprocess.run(
        ["bash", str(INSTALLER_SH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode == 2
    out = r.stdout + r.stderr
    assert "HOME" in out
    assert "unbound variable" not in out
