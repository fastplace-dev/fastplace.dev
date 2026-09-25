# tests/cli/test_key_verify.py
"""`key:verify` ciphertext-under-current-key check (roadmap spec #11)."""

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

    key:verify bootstraps config via load_env(), and python-dotenv writes
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
    """Pin the Rich console width so long phrases never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "wrong key or a rotated
    APP_KEY" split across lines. COLUMNS is read live per render, so
    pinning it here keeps every message on one line (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


def _keys_encrypt_module() -> str:
    """The framework module keys.py imports encrypt/decrypt from."""
    import pathlib

    keys_file = pathlib.Path(__import__("fastplace.cli.keys", fromlist=["x"]).__file__)
    for line in keys_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") and " import " in stripped and "decrypt" in stripped:
            return stripped[len("from "):].split(" import ")[0].strip()
    raise AssertionError("decrypt/encrypt import not found in keys.py")


def _make_project(tmp_path, monkeypatch, app_key="good" * 16, env="local"):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text(f"APP_ENV={env}\nAPP_KEY={app_key}\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_key_verify_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "key:verify" in result.stdout


def test_missing_encrypted_file_exits_one(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["key:verify"])
    assert result.exit_code == 1
    assert ".env.encrypted" in _out(result)


def test_in_sync_exits_zero(tmp_path, monkeypatch):
    import importlib

    root = _make_project(tmp_path, monkeypatch)
    enc = importlib.import_module(_keys_encrypt_module())
    (root / ".env.encrypted").write_text(enc.encrypt("SECRET=1\n", key="good" * 16) + "\n")
    result = runner.invoke(cli_app, ["key:verify"])
    assert result.exit_code == 0, result.stdout
    out = _out(result)
    assert "in sync" in out.lower() or "decrypts" in out.lower()


def test_wrong_key_exits_one_with_rotated_hint(tmp_path, monkeypatch):
    import importlib

    root = _make_project(tmp_path, monkeypatch)  # .env APP_KEY = good*16
    enc = importlib.import_module(_keys_encrypt_module())
    # ciphertext under a DIFFERENT key than the .env one
    (root / ".env.encrypted").write_text(enc.encrypt("SECRET=1\n", key="other" * 8) + "\n")
    result = runner.invoke(cli_app, ["key:verify"])
    assert result.exit_code == 1
    out = _out(result).lower()
    assert "wrong key" in out or "rotated" in out


def test_empty_app_key_exits_one_with_missing_message(tmp_path, monkeypatch):
    import importlib

    root = _make_project(tmp_path, monkeypatch, app_key="")
    enc = importlib.import_module(_keys_encrypt_module())
    # Structured token (not gibberish): it passes decrypt()'s fpaes1 prefix and
    # framing checks, reaches _key(None), and raises ConfigurationError there —
    # genuinely exercising the missing-APP_KEY handler.
    (root / ".env.encrypted").write_text(enc.encrypt("SECRET=1\n", key="good" * 16) + "\n")
    result = runner.invoke(cli_app, ["key:verify"])
    assert result.exit_code == 1
    assert "APP_KEY" in _out(result)


def test_verify_writes_nothing(tmp_path, monkeypatch):
    import importlib

    root = _make_project(tmp_path, monkeypatch)
    enc = importlib.import_module(_keys_encrypt_module())
    token = enc.encrypt("SECRET=1\n", key="good" * 16)
    encrypted = root / ".env.encrypted"
    encrypted.write_text(token + "\n")
    env_before = (root / ".env").read_text()
    mtime = encrypted.stat().st_mtime_ns
    env_mtime = (root / ".env").stat().st_mtime_ns
    result = runner.invoke(cli_app, ["key:verify"])
    assert result.exit_code == 0, result.stdout
    assert encrypted.read_text() == token + "\n"   # ciphertext untouched
    assert encrypted.stat().st_mtime_ns == mtime   # no rewrite
    assert (root / ".env").stat().st_mtime_ns == env_mtime   # .env untouched too
    assert (root / ".env").read_text() == env_before
