"""Task 3 — PATH persistence: rc creation, idempotency, --no-modify-path,
read-only rc, $HOME with spaces."""

from __future__ import annotations

import stat

import pytest

from tests.installer.conftest import _STUB_UV, run_installer, run_installer_stdin


def _with_uv(sandbox):
    sandbox.write_stub("uv", _STUB_UV.replace("$SANDBOX_HOME", str(sandbox.home)))


def test_no_rc_file_creates_one(sandbox, tmp_path):
    (sandbox.home / ".zshrc").unlink()  # sandbox pre-seeds one; fresh box has none
    _with_uv(sandbox)
    r = run_installer(sandbox.home)
    assert r.returncode == 0, r.stdout + r.stderr
    # zsh user without a .zshrc: the installer creates it (zsh reads it).
    rc = sandbox.home / ".zshrc"
    assert "# fastplace-installer" in rc.read_text()


@pytest.mark.parametrize("shell", ["sh", "zsh"])
def test_piped_stdin_to_shell_installs(sandbox, shell):
    """The real `curl … | sh` shape: script arrives on stdin, $0 is the
    shell's own name. zsh (non-bash) reproduces the dash failure mode."""
    _with_uv(sandbox)
    r = run_installer_stdin(sandbox.home, shell=shell)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fastplace new" in r.stdout
    assert (sandbox.home / ".local/bin/fastplace").exists()


def test_no_modify_path_flag_writes_nothing(sandbox):
    _with_uv(sandbox)
    before = (sandbox.home / ".zshrc").read_text()
    r = run_installer(sandbox.home, args=("--no-modify-path",))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (sandbox.home / ".zshrc").read_text() == before
    assert ".local/bin" in r.stdout  # instruction printed instead


def test_marker_idempotent(sandbox):
    _with_uv(sandbox)
    first = run_installer(sandbox.home)
    second = run_installer(sandbox.home)
    assert first.returncode == 0 and second.returncode == 0
    markers = (sandbox.home / ".zshrc").read_text().count("# fastplace-installer")
    assert markers == 1


def test_readonly_rc_warns_but_succeeds(sandbox):
    _with_uv(sandbox)
    rc = sandbox.home / ".zshrc"
    rc.chmod(stat.S_IRUSR)
    r = run_installer(sandbox.home)
    rc.chmod(0o644)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PATH" in r.stdout  # warning tells the user what to add by hand


def test_home_with_spaces(sandbox, tmp_path):
    # A sibling HOME under the same tmp_path parent keeps run_installer's
    # stub-bin derivation valid; only the HOME path itself carries spaces.
    spaced = tmp_path / "fp home x"
    spaced.mkdir()
    (spaced / ".zshrc").write_text("# existing\n")
    sandbox.home = spaced
    sandbox.write_stub("uv", _STUB_UV.replace("$SANDBOX_HOME", str(spaced)))
    r = run_installer(spaced)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (spaced / ".local/bin/fastplace").exists()
    assert "# fastplace-installer" in (spaced / ".zshrc").read_text()
