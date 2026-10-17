"""Pytest harness for the one-line installers.

Runs the real scripts in a sandbox $HOME with PATH-stubbed fake ``uv``,
``curl``, and ``fastplace`` binaries, so every installer decision is
exercised without network access or touching the developer's machine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_SH = REPO_ROOT / "installer" / "install.sh"

UV_INSTALL_URL = "https://astral.sh/uv/install.sh"

_STUB_CURL = """\
#!/usr/bin/env bash
# Fake curl: only the official uv installer URL "works". Any other URL
# fails like real curl (exit 22), and --fail-mode aborts everything.
set -u
log="$SANDBOX_HOME/curl.log"
printf '%s\\n' "$*" >> "$log"
if [[ "${FAKE_CURL_FAIL_MODE:-0}" == "1" ]]; then
  echo "curl: (22) The requested URL returned error: 404" >&2
  exit 22
fi
for arg in "$@"; do
  if [[ "$arg" == "{uv_url}" ]]; then
    # The uv installer drops a uv binary into ~/.local/bin.
    mkdir -p "$SANDBOX_HOME/.local/bin"
    cp "{staged}" "$SANDBOX_HOME/.local/bin/uv"
    chmod +x "$SANDBOX_HOME/.local/bin/uv"
    printf '%s\\n' "$(date -u +%FT%TZ) uv bootstrap (stub)" >> "$SANDBOX_HOME/uv-install.log"
    exit 0
  fi
done
echo "curl: (22) The requested URL returned error: 404" >&2
exit 22
"""

_STUB_UV = """\
#!/usr/bin/env bash
# Fake uv: records argv, then materializes a fake fastplace CLI exactly
# the way `uv tool install` would (into ~/.local/bin).
set -u
printf '%s\\n' "$*" >> "$SANDBOX_HOME/uv.log"
for arg in "$@"; do
  if [[ "$arg" == fastplace* ]]; then
    mkdir -p "$SANDBOX_HOME/.local/bin"
    bin="$SANDBOX_HOME/.local/bin/fastplace"
    printf '%s\\n' '#!/usr/bin/env bash' 'echo "fastplace 0.4.3"' > "$bin"
    chmod +x "$bin"
  fi
done
exit 0
"""


class Sandbox:
    """A fake $HOME plus the stub binaries the installer may call."""

    def __init__(self, home: Path, stub_bin: Path) -> None:
        self.home = home
        self.stub_bin = stub_bin

    def write_stub(self, name: str, body: str) -> Path:
        path = self.stub_bin / name
        path.write_text(body)
        path.chmod(0o755)
        return path


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("# existing zsh config\n")
    stub_bin = tmp_path / "stub-bin"
    (stub_bin / "staged").mkdir(parents=True)
    sb = Sandbox(home, stub_bin)
    # uv-staged: what the fake curl "downloads" into ~/.local/bin. A
    # fresh machine has no uv on PATH — tests that want a pre-existing
    # uv write sandbox.write_stub("uv", ...) themselves.
    sb.write_stub("uv-staged", _STUB_UV.replace("$SANDBOX_HOME", str(home)))
    sb.write_stub(
        "curl",
        _STUB_CURL.replace("$SANDBOX_HOME", str(home))
        .replace("{uv_url}", UV_INSTALL_URL)
        .replace("{staged}", str(stub_bin / "uv-staged")),
    )
    return sb


@pytest.fixture()
def failing_curl(sandbox: Sandbox) -> Sandbox:
    """Every curl invocation fails (network-down bootstrap)."""
    body = (
        "#!/usr/bin/env bash\n"
        "echo 'curl: (22) The requested URL returned error: 404' >&2\n"
        "exit 22\n"
    )
    sandbox.write_stub("curl", body)
    return sandbox


def _sandbox_env(home: Path) -> dict[str, str]:
    stub_bin = home.parent / "stub-bin"
    return {
        "HOME": str(home),
        "SANDBOX_HOME": str(home),
        "PATH": f"{stub_bin}:{home}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "SHELL": "/bin/zsh",
    }


def run_installer(
    home: Path,
    args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    shell: str = "bash",
) -> subprocess.CompletedProcess[str]:
    """Run install.sh as a file argument in the sandbox HOME."""
    run_env = _sandbox_env(home)
    if env:
        run_env.update(env)
    return subprocess.run(
        [shell, str(INSTALLER_SH), *args],
        capture_output=True,
        text=True,
        env=run_env,
        timeout=120,
    )


def run_installer_stdin(
    home: Path,
    env: dict[str, str] | None = None,
    shell: str = "sh",
) -> subprocess.CompletedProcess[str]:
    """Run install.sh the way `curl … | sh` does: script on stdin."""
    run_env = _sandbox_env(home)
    if env:
        run_env.update(env)
    return subprocess.run(
        [shell],
        input=INSTALLER_SH.read_text(),
        capture_output=True,
        text=True,
        env=run_env,
        timeout=120,
    )
