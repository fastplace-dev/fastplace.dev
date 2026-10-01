"""Integer CLI options must reject zero/negative values, not swallow them.

`serve --workers 0`, `migrate:rollback --steps 0` and `E2E_PORT=banana/0`
all used to fall into `or`-style defaults, downstream clamps, or a raw
ValueError traceback. The contract (one shared floor check, see
fastplace/cli/dev.py `_require_min`): an explicit value below the floor is
a clean one-line error naming the value; zero is only valid where the
option means it (none of these three does).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing as testing_mod

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401

runner = CliRunner()


def _write_env(box, text: str) -> None:
    (box.root / ".env").write_text(text)


# --- serve --workers (falsy-zero trap) ---------------------------------------


@pytest.mark.parametrize("value", ["0", "-3"])
def test_serve_rejects_workers_below_one(spawned, value):
    """An explicit --workers 0/-3 is an error naming the value — the old
    `workers or default` silently swapped 0 for cpu_count-1 workers."""
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", value])
    assert result.exit_code == 1
    assert "workers" in result.output
    assert value in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_rejects_app_workers_zero(spawned, monkeypatch):
    """APP_WORKERS=0 gets the same refusal as the CLI flag (env parse runs
    before the default, so 0 is a value — not "unset")."""
    monkeypatch.setenv("APP_WORKERS", "0")
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build"])
    assert result.exit_code == 1
    assert "workers" in result.output
    assert "0" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_rejects_malformed_app_workers(spawned, monkeypatch):
    """APP_WORKERS=banana is a clean one-line error (the env parse runs
    before the CPU-count default), not a raw ValueError traceback."""
    monkeypatch.setenv("APP_WORKERS", "banana")
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build"])
    assert result.exit_code == 1
    assert "APP_WORKERS" in result.output
    assert "banana" in result.output
    assert "Traceback" not in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_workers_one_still_serves(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert any("uvicorn" in cmd for cmd in spawned.commands)


# --- migrate:rollback --steps (count/report mismatch) ------------------------


class _RecordingManager:
    """Configured stub: proves a rejected --steps never reaches Alembic."""

    configured = True

    def __init__(self) -> None:
        self.downgrades: list[int | None] = []

    def _last_batch_size(self) -> int:  # noqa: SLF001 — parity with the real API
        return 1

    def downgrade(self, steps):  # noqa: ANN001
        self.downgrades.append(steps)
        return True


@pytest.mark.parametrize("value", ["0", "-2"])
def test_rollback_rejects_steps_below_one(tmp_path, monkeypatch, value):
    """--steps 0 used to revert ONE revision while printing "rolled back 0"
    (the manager clamps to -1). It must refuse before anything runs."""
    from fastplace.cli import database as database_mod

    monkeypatch.chdir(tmp_path)
    stub = _RecordingManager()
    monkeypatch.setattr(database_mod, "_manager", lambda: stub)

    result = runner.invoke(cli_app, ["migrate:rollback", "--steps", value])

    assert result.exit_code == 1
    assert "steps" in result.output
    assert value in result.output
    assert stub.downgrades == []  # nothing reverted


def test_rollback_steps_one_still_reverts(tmp_path, monkeypatch):
    from fastplace.cli import database as database_mod

    monkeypatch.chdir(tmp_path)
    stub = _RecordingManager()
    monkeypatch.setattr(database_mod, "_manager", lambda: stub)

    result = runner.invoke(cli_app, ["migrate:rollback", "--steps", "1"])

    assert result.exit_code == 0, result.output
    assert stub.downgrades == [1]


def test_rollback_steps_zero_touches_no_migrations(project_like, monkeypatch):  # noqa: ANN001
    """Integration shape: a migrated schema is intact after the refusal."""
    from fastplace.cli import app as cli  # noqa: F401 — project_like did the migrate

    result = runner.invoke(cli_app, ["migrate:rollback", "--steps", "0"])
    assert result.exit_code == 1
    import sqlite3

    conn = sqlite3.connect(project_like / "test.sqlite3")
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "posts" in tables  # the zero-step "dry probe" reverted nothing


@pytest.fixture()
def project_like(tmp_path, monkeypatch):
    """A real configured+migrated project (posts table live)."""
    from fastplace.config import reset_config

    original_cwd = Path.cwd()
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    for marker in (
        tmp_path / "app" / "__init__.py",
        tmp_path / "app" / "modules" / "__init__.py",
        tmp_path / "app" / "modules" / "blog" / "__init__.py",
        tmp_path / "app" / "modules" / "blog" / "models" / "__init__.py",
    ):
        marker.write_text("")
    (tmp_path / "app" / "modules" / "blog" / "models" / "post.py").write_text(
        "from fastplace.orm import Field, Model\n"
        "\n"
        "class Post(Model):\n"
        "    __tablename__ = 'posts'\n"
        "    id: int = Field(primary_key=True)\n"
        "    title: str\n"
    )
    (tmp_path / "config" / "app.py").write_text("APP_NAME = 'NumericOptions'\n")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}")
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(cli_app, ["db:configure"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:migration", "create_posts_table"]).exit_code == 0
    assert runner.invoke(cli_app, ["migrate"]).exit_code == 0
    yield tmp_path
    # Rebind config to the real checkout: the CLI commands bound it to this
    # tmp root, and a stale binding outlives the directory — later suites
    # (tests/orm) would read a vanished project's defaults.
    reset_config(original_cwd)


# --- test:e2e E2E_PORT (raw int() traceback) ---------------------------------


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture()
def e2e_project(tmp_path, monkeypatch):
    from fastplace.config import reset_config

    original_cwd = Path.cwd()
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text("APP_ENV=local\n")
    (tmp_path / "package.json").write_text("{}\n")
    (tmp_path / "node_modules").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        testing_mod,
        "_subprocess_run",
        lambda argv, *a, **k: _Proc(stdout="chromium build not needed\n"),
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/local/bin/{name}" if name in ("npx", "node") else None,
        raising=False,
    )
    yield tmp_path
    # test:e2e bound the config registry to this tmp root; hand it back to
    # the real checkout so later suites do not read a deleted project.
    reset_config(original_cwd)


def test_e2e_malformed_port_is_a_clean_error(e2e_project, monkeypatch):
    """E2E_PORT=banana used to die as a raw ValueError traceback."""
    monkeypatch.setenv("E2E_PORT", "banana")
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 1
    assert "E2E_PORT" in result.output
    assert "banana" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("value", ["0", "70000"])
def test_e2e_out_of_range_port_is_a_clean_error(e2e_project, monkeypatch, value):
    monkeypatch.setenv("E2E_PORT", value)
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 1
    assert "E2E_PORT" in result.output
    assert value in result.output


def test_e2e_valid_port_proceeds(e2e_project, monkeypatch):
    monkeypatch.setenv("E2E_PORT", "8971")
    seen: list[list[str]] = []

    def fake_run(argv, *a, **k):  # noqa: ANN002, ANN003
        seen.append(list(argv))
        return _Proc(stdout="chromium build not needed\n")

    monkeypatch.setattr(testing_mod, "_subprocess_run", fake_run)
    result = runner.invoke(cli_app, ["test:e2e"])
    assert result.exit_code == 0, result.output
    assert seen and seen[-1][:3] == ["npx", "playwright", "test"]
