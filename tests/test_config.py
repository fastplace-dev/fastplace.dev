"""Tests for fastplace.config — .env loading and the config registry."""

from pathlib import Path

import pytest

from fastplace.config import Config, _coerce, load_env


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(
        'APP_NAME = "Fastplace"\nAPP_DEBUG = False\nAPP_PORT = 8000\nAPP_RATE = 0.5\n'
    )
    (tmp_path / "config" / "database.py").write_text('DATABASE_DRIVER = "sqlite"\n')
    for var in ("APP_NAME", "APP_DEBUG", "APP_PORT", "APP_RATE", "DATABASE_DRIVER"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_load_env_reads_dotenv_without_overriding_real_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_REAL", "real")
    (tmp_path / ".env").write_text("FORCE_REAL=fake\nLOAD_ME=loaded\n")
    load_env(tmp_path / ".env")
    import os

    assert os.environ["FORCE_REAL"] == "real"
    assert os.environ["LOAD_ME"] == "loaded"


def test_config_reads_defaults_from_project_modules(project):
    cfg = Config(project)
    assert cfg.get("APP_NAME") == "Fastplace"
    assert cfg.get("DATABASE_DRIVER") == "sqlite"


def test_config_namespaced_lookup(project):
    cfg = Config(project)
    assert cfg.get("app.APP_NAME") == "Fastplace"
    assert cfg.get("database.DATABASE_DRIVER") == "sqlite"


def test_environment_overrides_module_default(project, monkeypatch):
    monkeypatch.setenv("APP_NAME", "Override")
    cfg = Config(project)
    assert cfg.get("APP_NAME") == "Override"


def test_env_values_coerced_to_default_type(project, monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("APP_RATE", "0.75")
    cfg = Config(project)
    assert cfg.get("APP_DEBUG") is True
    assert cfg.get("APP_PORT") == 9000
    assert cfg.get("APP_RATE") == 0.75


def test_missing_key_returns_default_or_none(project):
    cfg = Config(project)
    assert cfg.get("NOPE", default="fallback") == "fallback"
    assert cfg.get("NOPE") is None


def test_coerce_bool_forms():
    assert _coerce("1", True) is True
    assert _coerce("YES", True) is True
    assert _coerce("0", True) is False
    assert _coerce("off", False) is False
    assert _coerce("junk", True) == "junk"
    assert _coerce("42", 10) == 42
    assert _coerce("nope", 10) == "nope"
    assert _coerce("1.5", 1.0) == 1.5
