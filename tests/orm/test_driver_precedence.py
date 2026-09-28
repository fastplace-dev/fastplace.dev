"""Driver resolution: the URL the engine binds to is the source of truth.

A scaffold ships ``DATABASE_DRIVER='sqlite'`` in ``config/database.py``;
when the operator switches ``DATABASE_URL`` alone (env or ``.env``) to
PostgreSQL/MySQL, that stale module default must not flip the capability
registry — it would claim SQLite's truth (no vectors, no advisory locks)
against a live PostgreSQL, or MySQL's against PostgreSQL. A declared
driver counts only when it names the same family as the URL (aliases like
``postgres`` count); a contradiction warns and follows the URL.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from fastplace.orm.manager import DatabaseManager, _connections_from_config

#: What `fastplace new` ships — the exact trap: driver + URL declared as a
#: sqlite pair in the module, switched later through DATABASE_URL alone.
SCAFFOLD_DATABASE_PY = (
    "DATABASE_DRIVER = 'sqlite'\nDATABASE_URL = 'sqlite+aiosqlite:///./database.sqlite3'\n"
)


@pytest.fixture()
def bind_project(tmp_path, monkeypatch):
    """Write config/database.py under a tmp project, rebind the config
    singleton to it; restore the process singleton after."""
    import fastplace.config as config_module

    saved = config_module._default_config

    def _bind(module_text: str) -> Path:
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "database.py").write_text(module_text)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_DRIVER", raising=False)
        config_module.reset_config(tmp_path)
        return tmp_path

    yield _bind
    config_module._default_config = saved


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_scaffold_driver_default_never_beats_switched_url(bind_project, monkeypatch, caplog):
    bind_project(SCAFFOLD_DATABASE_PY)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/ffw2_pg")

    with caplog.at_level(logging.WARNING, logger="fastplace.db"):
        connections = _connections_from_config()

    # Capabilities follow the engine's URL, not the stale module string.
    assert connections["default"]["driver"] == "postgresql"
    caps = DatabaseManager(connections).capabilities()
    assert caps.driver == "postgresql"
    assert caps.supports_vector is True
    assert caps.supports_advisory_locks is True
    # The stale pair is diagnosed, not silently papered over.
    assert any("contradicts" in r.getMessage() for r in _warnings(caplog))


def test_mysql_url_derives_mysql_capabilities(bind_project, monkeypatch):
    bind_project(SCAFFOLD_DATABASE_PY)
    monkeypatch.setenv("DATABASE_URL", "mysql://user:secret@localhost/ffw2_my")

    caps = DatabaseManager(_connections_from_config()).capabilities()

    assert caps.driver == "mysql"
    assert caps.supports_json_path is True
    assert caps.supports_returning is False
    assert caps.supports_advisory_locks is True
    assert caps.supports_vector is False


def test_matching_env_driver_wins_without_warning(bind_project, monkeypatch, caplog):
    bind_project(SCAFFOLD_DATABASE_PY)
    monkeypatch.setenv("DATABASE_DRIVER", "mysql")
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://user@localhost/ffw2_my")

    with caplog.at_level(logging.WARNING, logger="fastplace.db"):
        connections = _connections_from_config()

    assert connections["default"]["driver"] == "mysql"
    assert not _warnings(caplog)


def test_alias_driver_name_counts_as_match(bind_project, monkeypatch, caplog):
    bind_project(SCAFFOLD_DATABASE_PY)
    monkeypatch.setenv("DATABASE_DRIVER", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/ffw2_pg")

    with caplog.at_level(logging.WARNING, logger="fastplace.db"):
        connections = _connections_from_config()

    assert connections["default"]["driver"] == "postgresql"
    assert not _warnings(caplog)


def test_contradicting_env_driver_warns_and_url_wins(bind_project, monkeypatch, caplog):
    bind_project(SCAFFOLD_DATABASE_PY)
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")
    monkeypatch.setenv("DATABASE_URL", "mysql://user@localhost/ffw2_my")

    with caplog.at_level(logging.WARNING, logger="fastplace.db"):
        connections = _connections_from_config()

    assert connections["default"]["driver"] == "mysql"
    assert any("contradicts" in r.getMessage() for r in _warnings(caplog))


def test_untouched_scaffold_pair_stays_silent(bind_project, monkeypatch, caplog):
    """The shipped sqlite pair agrees with itself — no warning noise."""
    bind_project(SCAFFOLD_DATABASE_PY)

    with caplog.at_level(logging.WARNING, logger="fastplace.db"):
        connections = _connections_from_config()

    assert connections["default"]["driver"] == "sqlite"
    assert not _warnings(caplog)


def test_named_connection_driver_checked_against_its_url(bind_project, monkeypatch, caplog):
    bind_project(
        SCAFFOLD_DATABASE_PY + "DATABASE_CONNECTIONS = {\n"
        "    'analytics': {'driver': 'postgresql', 'url': 'mysql://user@localhost/ffw2_an'},\n"
        "}\n"
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    with caplog.at_level(logging.WARNING, logger="fastplace.db"):
        connections = _connections_from_config()

    # The named connection's declaration contradicts its own URL — the URL
    # wins there without condemning the whole project to silence about it.
    assert connections["analytics"]["driver"] == "mysql"
    assert any("analytics" in r.getMessage() for r in _warnings(caplog))
    # The agreeing default connection is untouched.
    assert connections["default"]["driver"] == "sqlite"
