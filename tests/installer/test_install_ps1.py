"""Task 5 — static contract for installer/install.ps1.

Behavior runs in the CI windows runner (Task 6); these tests pin the
contract on any OS so the file cannot silently drift from install.sh.
"""

from __future__ import annotations

import re

from tests.installer.conftest import INSTALLER_SH

INSTALLER_PS1 = INSTALLER_SH.parent / "install.ps1"


def test_script_exists():
    assert INSTALLER_PS1.exists(), "installer/install.ps1 missing"


def test_param_block():
    text = INSTALLER_PS1.read_text()
    assert "[switch]$Mcp" in text
    assert "[switch]$NoModifyPath" in text


def test_strict_error_preference_and_version():
    text = INSTALLER_PS1.read_text()
    assert "$ErrorActionPreference = 'Stop'" in text
    assert "INSTALLER_VERSION" in text
    assert '"1.0.0"' in text or "'1.0.0'" in text


def test_exact_urls():
    text = INSTALLER_PS1.read_text()
    assert "https://astral.sh/uv/install.ps1" in text
    assert "https://fastplace.dev" in text


def test_install_command_shape():
    text = INSTALLER_PS1.read_text()
    assert "uv tool install --upgrade --python 3.12" in text
    # spec building mirrors install.sh: extras + pin
    assert "fastplace[mcp]" in text
    assert "FASTPLACE_VERSION" in text
    assert re.search(r"\^?\[?0-9\]", text) or "[0-9]" in text  # pin regex present


def test_user_path_branch():
    text = INSTALLER_PS1.read_text()
    assert "[Environment]::SetEnvironmentVariable(" in text
    assert "'User'" in text
    assert ".local" in text  # uv tool bin dir under the user profile


def test_verification_by_full_path():
    text = INSTALLER_PS1.read_text()
    assert re.search(r"\$env:USERPROFILE.*\.local.*fastplace", text) or (
        ".local" in text and "--version" in text
    )


def test_no_forbidden_words():
    forbidden = re.compile(r"laravel|eloquent", re.IGNORECASE)
    assert not forbidden.search(INSTALLER_PS1.read_text())
