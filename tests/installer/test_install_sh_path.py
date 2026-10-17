"""Task 3 — PATH persistence: rc creation, idempotency, --no-modify-path,
read-only rc, $HOME with spaces."""

from __future__ import annotations

import stat

import pytest

from tests.installer.conftest import _STUB_UV, run_installer, run_installer_stdin


def _with_uv(sandbox):
    sandbox.write_stub("uv", _STUB_UV.replace("$SANDBOX_HOME", str(sandbox.home)))


def test_piped_stdin_to_bash_installs(sandbox):
    """The documented `curl … | bash` shape: script on stdin, $0="bash"."""
    _with_uv(sandbox)
    r = run_installer_stdin(sandbox.home, shell="bash")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fastplace new" in r.stdout
    assert (sandbox.home / ".local/bin/fastplace").exists()


def _sh_is_bash() -> bool:
    """macOS /bin/sh IS bash (sh-mode); Linux /bin/sh is dash."""
    import shutil
    import subprocess

    probe = subprocess.run(
        ["/bin/sh", "-c", 'echo "${BASH_VERSION:-nobash}"'],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return probe.stdout.strip() != "nobash"


def test_piped_stdin_to_sh(sandbox):
    """`curl … | sh`: works where sh is bash, refuses with guidance on dash."""
    _with_uv(sandbox)
    r = run_installer_stdin(sandbox.home, shell="sh")
    if _sh_is_bash():
        assert r.returncode == 0, r.stdout + r.stderr
        assert "fastplace new" in r.stdout
    else:
        # dash cannot hand its stdin to bash (it has already buffered it);
        # the script must refuse with the pipe-to-bash guidance instead.
        assert r.returncode == 2
        assert "bash" in r.stdout + r.stderr


@pytest.mark.skipif(not __import__("shutil").which("zsh"), reason="zsh absent")
def test_piped_stdin_to_zsh_refuses_with_guidance(sandbox):
    """Non-bash stdin shells get the same refusal — no guessing."""
    _with_uv(sandbox)
    r = run_installer_stdin(sandbox.home, shell="zsh")
    assert r.returncode == 2
    assert "bash" in r.stdout + r.stderr


def test_no_rc_file_creates_one(sandbox, tmp_path):
    (sandbox.home / ".zshrc").unlink()  # sandbox pre-seeds one; fresh box has none
    _with_uv(sandbox)
    r = run_installer(sandbox.home)
    assert r.returncode == 0, r.stdout + r.stderr
    # zsh user without a .zshrc: the installer creates it (zsh reads it).
    rc = sandbox.home / ".zshrc"
    assert "# fastplace-installer" in rc.read_text()


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


@pytest.mark.skipif(not __import__("shutil").which("zsh"), reason="zsh absent")
def test_zsh_file_invocation_still_reexecs(sandbox):
    """zsh running the script as a file (not stdin) must keep working —
    the -f guard must refuse only name-like $0, never real paths."""
    _with_uv(sandbox)
    r = run_installer(sandbox.home, shell="zsh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fastplace new" in r.stdout
