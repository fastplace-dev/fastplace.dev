"""`env:encrypt` / `env:decrypt` CLI commands (spec #55)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

#: A realistic .env: plain keys, a secret, and the APP_KEY line itself —
#: encrypting must seal the whole file as one blob, APP_KEY included.
ENV_BODY = (
    "APP_NAME=Demo\n"
    "APP_KEY=from-dotenv-key\n"
    "DATABASE_URL=sqlite+aiosqlite:///./app.sqlite3\n"
    "STRIPE_SECRET=sk_test_abc123\n"
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal project root (asgi.py marker + .env); env and config restored.

    ``env:encrypt`` never imports project code — like ``log:tail``, the
    asgi.py marker is all a project needs to look like. APP_ENV defaults to
    testing so the production prompt only fires in tests that opt into it.
    """
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)

    root = tmp_path / "srv"
    root.mkdir()
    (root / "asgi.py").write_text("app = None\n")
    (root / ".env").write_text(ENV_BODY)
    monkeypatch.chdir(root)
    monkeypatch.setenv("APP_ENV", "testing")
    try:
        yield root
    finally:
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


def _run(*args, **kwargs):
    result = runner.invoke(cli_app, list(args), **kwargs)
    return result.exit_code, ANSI_RE.sub("", result.output)


def _no_app_key_anywhere(monkeypatch, root: Path) -> None:
    """Make APP_KEY unreachable: empty in the process env, absent from .env."""
    monkeypatch.setenv("APP_KEY", "")  # present-but-empty refuses, not falls through
    (root / ".env").write_text("APP_NAME=Demo\nSTRIPE_SECRET=sk_test_abc123\n")


# --- roundtrip -----------------------------------------------------------------


def test_env_encrypt_writes_armored_file_and_keeps_original(project, monkeypatch):
    monkeypatch.delenv("APP_KEY", raising=False)  # the .env-resident key must load
    code, out = _run("env:encrypt")
    assert code == 0, out

    encrypted = project / ".env.encrypted"
    assert encrypted.exists()
    # The original .env survives byte-for-byte — encrypt never edits its input.
    assert (project / ".env").read_text() == ENV_BODY
    # One blob, base64-armored: a single fpaes1 token line, no plaintext inside.
    body = encrypted.read_text().strip()
    assert re.fullmatch(r"fpaes1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", body)
    assert "sk_test_abc123" not in body and "sqlite" not in body
    # And nothing secret reaches the terminal either.
    assert ".env.encrypted" in out
    assert "sk_test_abc123" not in out and "from-dotenv-key" not in out


def test_roundtrip_via_app_key_restores_a_drifted_env(project, monkeypatch):
    monkeypatch.delenv("APP_KEY", raising=False)
    assert _run("env:encrypt")[0] == 0

    # In sync: an immediate decrypt is a no-op — no .env.bak churn.
    code, out = _run("env:decrypt")
    assert code == 0, out
    assert (project / ".env").read_text() == ENV_BODY
    assert not (project / ".env.bak").exists()

    # Drifted: the current .env is kept as .env.bak, then restored.
    (project / ".env").write_text("APP_NAME=Lost\n")
    code, out = _run("env:decrypt")
    assert code == 0, out
    assert (project / ".env").read_text() == ENV_BODY
    assert (project / ".env.bak").read_text() == "APP_NAME=Lost\n"


# --- key sources ---------------------------------------------------------------


def test_missing_app_key_refuses_encrypt_and_decrypt(project, monkeypatch):
    _no_app_key_anywhere(monkeypatch, project)

    code, out = _run("env:encrypt")
    assert code == 1
    assert "APP_KEY" in out
    assert not (project / ".env.encrypted").exists()

    # A token minted under another key still cannot be opened without APP_KEY.
    assert _run("env:encrypt", "--key", "k1")[0] == 0
    code, out = _run("env:decrypt")
    assert code == 1
    assert "APP_KEY" in out


def test_key_option_overrides_app_key_and_writes_nothing_to_env(project, monkeypatch):
    monkeypatch.setenv("APP_KEY", "config-material")
    code, out = _run("env:encrypt", "--key", "explicit-material")
    assert code == 0, out
    # --key is explicit material only: .env (and its APP_KEY line) stay untouched.
    assert (project / ".env").read_text() == ENV_BODY

    # The explicit key — not APP_KEY — sealed the file, so decrypt needs it too.
    code, out = _run("env:decrypt", "--key", "explicit-material")
    assert code == 0, out
    assert (project / ".env").read_text() == ENV_BODY


def test_explicit_key_rescues_a_missing_app_key(project, monkeypatch):
    _no_app_key_anywhere(monkeypatch, project)

    code, out = _run("env:encrypt", "--key", "hand-carried")
    assert code == 0, out
    expected = "APP_NAME=Demo\nSTRIPE_SECRET=sk_test_abc123\n"
    assert (project / ".env").read_text() == expected
    code, out = _run("env:decrypt", "--key", "hand-carried")
    assert code == 0, out
    assert (project / ".env").read_text() == expected


def test_wrong_key_decrypt_exits_1(project, monkeypatch):
    monkeypatch.setenv("APP_KEY", "config-material")
    assert _run("env:encrypt", "--key", "right-material")[0] == 0

    code, out = _run("env:decrypt", "--key", "wrong-material")
    assert code == 1
    assert "decrypt" in out


def test_tampered_encrypted_file_refused(project, monkeypatch):
    monkeypatch.setenv("APP_KEY", "config-material")
    assert _run("env:encrypt")[0] == 0
    token = (project / ".env.encrypted").read_text().strip()
    head, nonce, body = token.split(".")
    mangled = body[:-2] + ("AA" if not body.endswith("AA") else "BB")
    (project / ".env.encrypted").write_text(f"{head}.{nonce}.{mangled}\n")

    code, out = _run("env:decrypt")
    assert code == 1
    assert "decrypt" in out


# --- production guard (destructive-set convention, mirrors db:wipe) ------------


def test_env_encrypt_refuses_in_production_without_force(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    code, out = _run("env:encrypt", input="n\n")
    assert code == 1
    assert "Encrypt" in out  # the confirmation prompt was asked
    assert not (project / ".env.encrypted").exists()


def test_env_encrypt_production_confirm_proceeds(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    code, out = _run("env:encrypt", input="y\n")
    assert code == 0, out
    assert (project / ".env.encrypted").exists()


def test_env_encrypt_production_force_skips_the_prompt(project, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    code, out = _run("env:encrypt", "--force")
    assert code == 0, out
    assert "Encrypt the production" not in out  # no confirmation prompt
    assert (project / ".env.encrypted").exists()


def test_env_encrypt_unset_app_env_defaults_to_production(project, monkeypatch):
    # A real CLI process binds config to the project root; this tmp project has
    # no config/ module, so the framework's default="production" is what a
    # fresh process would see (the suite's own config/app.py must not leak in).
    from fastplace.config import reset_config

    reset_config(project)
    monkeypatch.delenv("APP_ENV", raising=False)
    code, out = _run("env:encrypt", input="n\n")
    assert code == 1
    assert not (project / ".env.encrypted").exists()


def test_env_encrypt_testing_env_needs_no_prompt(project):
    code, out = _run("env:encrypt")  # no input: a prompt would starve and abort
    assert code == 0, out
    assert (project / ".env.encrypted").exists()


def test_env_decrypt_needs_no_force_even_in_production(project, monkeypatch):
    """Only env:encrypt is in the spec's destructive set — decrypt is a restore."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_KEY", "config-material")
    assert _run("env:encrypt", "--force")[0] == 0
    (project / ".env").write_text("APP_NAME=Lost\n")

    code, out = _run("env:decrypt")  # no input, no --force
    assert code == 0, out
    assert (project / ".env").read_text() == ENV_BODY


# --- friendly guards -----------------------------------------------------------


def test_env_encrypt_without_env_file_exits_friendly(project):
    (project / ".env").unlink()
    code, out = _run("env:encrypt")
    assert code == 1
    assert ".env" in out


def test_env_decrypt_without_encrypted_file_exits_friendly(project):
    code, out = _run("env:decrypt")
    assert code == 1
    assert "env:encrypt" in out  # the message points at the producing command


@pytest.mark.parametrize("command", [["env:encrypt"], ["env:decrypt"]])
def test_env_crypt_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
