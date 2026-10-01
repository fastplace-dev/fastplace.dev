"""`ai:doctor` — AI stack diagnosis (roadmap spec, app plane)."""

from __future__ import annotations

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from tests.cli._isolation import (  # noqa: F401 — autouse + fixture by name
    isolate_project_state,
    park_project_modules,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so fix/detail hints never wrap mid-word."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _restored_config():
    """Rebind the process config singleton after each test (test_log_prune)."""
    import fastplace.config as config_module

    saved = config_module._default_config
    yield
    config_module._default_config = saved


@pytest.fixture(autouse=True)
def _reset_vector_registry():
    """Restore the shipped vector-registry defaults after project imports."""
    from fastplace.ai.vectors import reset_vector_registry

    reset_vector_registry()
    yield
    reset_vector_registry()


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


def _make_project(tmp_path, monkeypatch, env_text: str = "") -> object:
    """A cwd carrying the project marker + .env; chdir'd into."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "asgi.py").write_text("# marker\n")
    if env_text:
        (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _declare_vector_model(root, dimensions: int, name: str) -> None:
    """An app model with one VectorField column (test_ai_ops declaration form).

    Each caller passes a unique module stem (used as the table name too):
    the process-wide declarative base survives across tests and keeps every
    class ever mapped, and ``_declared_in_project`` tells them apart by the
    module file under the project root — a shared module path would let the
    stale class from an earlier test shadow the fresh declaration.
    """
    (root / "app").mkdir(exist_ok=True)
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "models").mkdir(parents=True)
    (root / "app" / "models" / "__init__.py").write_text("")
    (root / "app" / "models" / f"{name}.py").write_text(
        "from fastplace.orm import Model\n"
        "from fastplace.orm import VectorField\n"
        "\n"
        "\n"
        "class Doc(Model):\n"
        f'    __tablename__ = "{name}"\n'
        "\n"
        f'    body: "list[float] | None" = VectorField(dimensions={dimensions})\n'
    )
    (root / "app" / "models" / "__init__.py").write_text(
        f"from .{name} import Doc\n"  # noqa: F401
    )


def test_ai_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "ai:doctor" in result.stdout


def test_ai_doctor_help_exits_zero():
    result = runner.invoke(cli_app, ["ai:doctor", "--help"])
    assert result.exit_code == 0
    assert "usage" in _out(result).lower()


def test_no_keys_local_project_warns_not_fails(tmp_path, monkeypatch):
    """Missing provider key is a WARN (AI is optional), never a FAIL."""
    _make_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "FAIL" not in out


def test_openai_key_masked_in_output(tmp_path, monkeypatch):
    """A present key renders as its last 4 chars — never in full."""
    _make_project(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-supersecret99wxyz")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "wxyz" in out  # masked tail visible
    assert "supersecret99" not in out  # the body never renders


def test_claude_model_uses_anthropic_key(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="AI_MODEL=claude-3-5-sonnet\n")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "ANTHROPIC_API_KEY" in out


def test_unknown_vector_store_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="AI_VECTOR_STORE=weaviate\n")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "weaviate" in out.lower() or "vector store" in out.lower()


def test_vector_field_on_non_postgres_fails(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param
    root = _make_project(
        tmp_path, monkeypatch, env_text="DATABASE_URL=sqlite+aiosqlite:///./ai_probe.sqlite3\n"
    )
    _declare_vector_model(root, dimensions=8, name="ai_doctor_docs_sqlite")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "postgres" in out.lower()  # names the requirement


def test_dimension_mismatch_fails(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param
    """text-embedding-3-small emits 1536 dims — a VectorField(8) can never store it."""
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "DATABASE_URL=postgresql+asyncpg://u:p@localhost/db\n"
            "AI_EMBEDDING_MODEL=text-embedding-3-small\n"
        ),
    )
    _declare_vector_model(root, dimensions=8, name="ai_doctor_docs_mismatch")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "1536" in out
    assert "8" in out


def test_dimension_match_passes(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "DATABASE_URL=postgresql+asyncpg://u:p@localhost/db\n"
            "AI_EMBEDDING_MODEL=text-embedding-3-small\n"
        ),
    )
    _declare_vector_model(root, dimensions=1536, name="ai_doctor_docs_match")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "FAIL" not in out


def test_unknown_model_remedy_names_the_real_flag(tmp_path, monkeypatch, park_project_modules):  # noqa: F811 — fixture param
    """The dimension check's fix hint must name a shipped option.

    It used to say `fastplace ai:embed --verify` — a flag that does not
    exist (the real one is --check), so following the doctor's own repair
    instruction produced `No such option: --verify`.
    """
    root = _make_project(
        tmp_path,
        monkeypatch,
        env_text=(
            "DATABASE_URL=postgresql+asyncpg://u:p@localhost/db\n"
            "AI_EMBEDDING_MODEL=made-up-embedder\n"
        ),
    )
    _declare_vector_model(root, dimensions=8, name="ai_doctor_docs_unknown")

    result = runner.invoke(cli_app, ["ai:doctor"])

    assert result.exit_code == 0, _out(result)  # warn, not fail
    out = _out(result)
    assert "ai:embed --check" in out
    assert "--verify" not in out
