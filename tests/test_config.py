"""Tests for fastplace.config — .env loading and the config registry."""

from pathlib import Path

import pytest

from fastplace.config import Config, _coerce, load_env


@pytest.fixture(autouse=True)
def _clean_config_state():
    """Reset the default registry and the .env provenance snapshot per test."""
    from fastplace.config import reset_config

    reset_config()
    yield
    reset_config()


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


# --- provenance: where the effective value came from -------------------------


def test_source_reports_env_file_when_env_matches_the_snapshot(project, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_NAME", "Fastplace")
    (tmp_path / ".env").write_text("APP_NAME=Fastplace\n")
    load_env(tmp_path / ".env")
    cfg = Config(project)
    assert cfg.get("APP_NAME") == "Fastplace"
    assert cfg.source("APP_NAME") == "env-file"
    assert cfg.shadowed_env_value("APP_NAME") is None


def test_source_reports_shell_when_env_differs_from_env_file(project, monkeypatch, tmp_path):
    """A live value that differs from the .env snapshot cannot be file-provided:
    it is a shell export shadowing the file."""
    monkeypatch.setenv("APP_NAME", "FromShell")
    (tmp_path / ".env").write_text("APP_NAME=FromFile\n")
    load_env(tmp_path / ".env")
    cfg = Config(project)
    assert cfg.get("APP_NAME") == "FromShell"
    assert cfg.source("APP_NAME") == "shell"
    assert cfg.shadowed_env_value("APP_NAME") == "FromFile"


def test_source_reports_shell_without_any_env_file_entry(project, monkeypatch):
    monkeypatch.setenv("APP_NAME", "FromShell")
    cfg = Config(project)
    assert cfg.source("APP_NAME") == "shell"
    assert cfg.shadowed_env_value("APP_NAME") is None  # nothing in .env to shadow


def test_source_reports_default_when_unset(project):
    cfg = Config(project)
    assert cfg.source("APP_NAME") == "default"
    assert cfg.shadowed_env_value("APP_NAME") is None


def test_source_returns_none_for_a_fully_unset_key(project):
    cfg = Config(project)
    assert cfg.source("NEVER_CONFIGURED") is None


def test_module_level_helpers_mirrored_on_the_default_config(project, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_NAME", "FromShell")
    (tmp_path / ".env").write_text("APP_NAME=FromFile\n")
    from fastplace.config import reset_config, shadowed_env_value, source

    # Rebind first, load second — reset drops the snapshot it did not create.
    reset_config(project)
    load_env(tmp_path / ".env")
    assert source("APP_NAME") == "shell"
    assert shadowed_env_value("APP_NAME") == "FromFile"


def test_reset_config_clears_the_env_snapshot(project, monkeypatch, tmp_path):
    """Stale provenance must not leak across configs: a fresh registry reads
    a .env-free world even if a previous load_env recorded one."""
    monkeypatch.setenv("APP_NAME", "FromShell")
    (tmp_path / ".env").write_text("APP_NAME=FromFile\n")
    load_env(tmp_path / ".env")
    cfg = Config(project)
    assert cfg.source("APP_NAME") == "shell"
    assert cfg.shadowed_env_value("APP_NAME") == "FromFile"

    from fastplace.config import reset_config

    monkeypatch.delenv("APP_NAME")
    fresh = reset_config(project)
    # The snapshot died with the reset: no shadowing .env value is reported
    # for a world whose .env was never loaded into THIS registry's lifetime.
    assert fresh.source("APP_NAME") == "default"
    assert fresh.shadowed_env_value("APP_NAME") is None
