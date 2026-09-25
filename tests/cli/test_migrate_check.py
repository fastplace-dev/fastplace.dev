"""migrate:check — model-vs-live-schema drift without writing a revision (roadmap A2)."""

from __future__ import annotations

import os
import re

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from _isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


def _out(result) -> str:
    return ANSI_RE.sub("", result.output)


# The field import is the project fixture's exact pattern (test_db_wipe.py):
# `Field` from fastplace.orm — there is no StringField in this framework.
POST_MODEL = """\
from fastplace.orm import Field, Model


class Post(Model):
    title: str = Field()
"""


def _scaffold(tmp_path, monkeypatch, models: dict[str, str]) -> None:
    (tmp_path / "app" / "modules" / "blog" / "models").mkdir(parents=True)
    (tmp_path / "app" / "modules" / "blog" / "__init__.py").write_text("")
    (tmp_path / "app" / "modules" / "__init__.py").write_text("")
    (tmp_path / "app" / "__init__.py").write_text("")
    for name, source in models.items():
        (tmp_path / "app" / "modules" / "blog" / "models" / f"{name}.py").write_text(source)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/x.db")


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    _scaffold(tmp_path, monkeypatch, {"post": POST_MODEL})
    # make:migration takes a required name argument (see fastplace/cli/database.py).
    for args in (["db:configure"], ["make:migration", "create_posts_table"], ["migrate"]):
        result = runner.invoke(cli_app, args)
        assert result.exit_code == 0, result.output
    return tmp_path


def test_migrated_project_is_clean(migrated):
    result = runner.invoke(cli_app, ["migrate:check"])
    assert result.exit_code == 0, result.output
    assert "clean" in _out(result)


def test_drift_after_new_model_exits_one(migrated, tmp_path):
    (tmp_path / "app" / "modules" / "blog" / "models" / "comment.py").write_text(
        POST_MODEL.replace("Post", "Comment").replace("title", "body")
    )
    result = runner.invoke(cli_app, ["migrate:check"])
    assert result.exit_code == 1
    out = _out(result)
    assert "Comment" in out or "comments" in out


def test_unconfigured_migrations_notice(tmp_path, monkeypatch):
    _scaffold(tmp_path, monkeypatch, {"post": POST_MODEL})
    result = runner.invoke(cli_app, ["migrate:check"])
    assert result.exit_code == 1
    assert "db:configure" in _out(result)


def test_framework_tables_are_not_on_model_metadata(migrated):
    from fastplace.cli.inspect import collect_models
    from fastplace.orm import Model

    collect_models(migrated)
    for framework_table in (
        "cache",
        "_fastplace_failed_jobs",
        "alembic_version",
        "fastplace_migrations",
    ):
        assert framework_table not in Model.metadata.tables
