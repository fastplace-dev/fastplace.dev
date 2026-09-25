# tests/cli/test_key_rotate.py
"""`key:rotate` one-step APP_KEY rotation (roadmap spec #8)."""

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    key:rotate bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich table width so detail cells never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "nothing was rotated" split
    across lines. COLUMNS is read live per render, so pinning it here keeps
    every message on one line (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _active_key(text: str) -> str:
    import re

    m = re.search(r"^\s*APP_KEY\s*=\s*(\S+)\s*$", text, re.MULTILINE)
    return m.group(1) if m else ""


def _make_project(tmp_path, monkeypatch, app_key="old" * 16, env="local"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text(f"APP_ENV={env}\nAPP_KEY={app_key}\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def keys_encrypt_module() -> str:
    """The framework module keys.py imports encrypt/decrypt from.

    The test must exercise the exact encrypt/decrypt the command uses, so
    discover the module path from keys.py's own import block instead of
    hardcoding it.
    """
    import pathlib

    keys_file = pathlib.Path(__import__("fastplace.cli.keys", fromlist=["x"]).__file__)
    for line in keys_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") and " import " in stripped and "decrypt" in stripped:
            return stripped[len("from "):].split(" import ")[0].strip()
    raise AssertionError("decrypt/encrypt import not found in keys.py")


def test_key_rotate_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "key:rotate" in result.stdout


def test_rotate_refuses_without_existing_key(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, app_key="")
    result = runner.invoke(cli_app, ["key:rotate"])
    assert result.exit_code == 1
    assert "APP_KEY" in _out(result)


def test_rotate_without_encrypted_file_rotates_key(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch)
    old_text = (root / ".env").read_text()
    result = runner.invoke(cli_app, ["key:rotate", "--force"])
    assert result.exit_code == 0, result.stdout
    new_text = (root / ".env").read_text()
    assert _active_key(new_text) != _active_key(old_text)
    assert (root / ".env.bak").read_text() == old_text
    out = _out(result)
    assert "INVALIDATED" in out and "PRESERVED" in out and "two_factor" in out


def test_rotate_reencrypts_env_encrypted_under_new_key(tmp_path, monkeypatch):
    import importlib

    root = _make_project(tmp_path, monkeypatch)
    enc = importlib.import_module(keys_encrypt_module())
    plaintext = "SECRET_VALUE=hidden\n"
    (root / ".env.encrypted").write_text(enc.encrypt(plaintext, key="old" * 16) + "\n")

    result = runner.invoke(cli_app, ["key:rotate", "--force"])
    assert result.exit_code == 0, result.stdout
    new_key = _active_key((root / ".env").read_text())
    token = (root / ".env.encrypted").read_text().strip()
    assert enc.decrypt(token, key=new_key) == plaintext  # round-trips under NEW key
    assert (root / ".env.encrypted.bak").exists()


def test_rotate_hard_stops_when_ciphertext_under_different_key(tmp_path, monkeypatch):
    import importlib

    root = _make_project(tmp_path, monkeypatch)
    enc = importlib.import_module(keys_encrypt_module())
    # .env.encrypted under a key that is NOT the .env APP_KEY
    (root / ".env.encrypted").write_text(enc.encrypt("X=1\n", key="other" * 8) + "\n")
    before = (root / ".env").read_text()

    result = runner.invoke(cli_app, ["key:rotate", "--force"])
    assert result.exit_code == 1
    out = _out(result)
    assert "nothing was rotated" in out
    assert (root / ".env").read_text() == before  # APP_KEY untouched


def test_production_guard_blocks_without_force(tmp_path, monkeypatch):
    root = _make_project(tmp_path, monkeypatch, env="production")
    before = (root / ".env").read_text()
    result = runner.invoke(cli_app, ["key:rotate"], input="n\n")
    assert result.exit_code == 1
    assert "aborted" in _out(result)
    assert (root / ".env").read_text() == before  # nothing mutated
