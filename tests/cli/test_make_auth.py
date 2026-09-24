"""``fastplace make:auth`` — new-files-only scaffolder (spec §4.17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastplace.cli import app as cli_app

_GENERATED = (
    "app/modules/accounts/models/user.py",
    "app/modules/accounts/repositories/user_repository.py",
    "app/modules/accounts/services/auth_service.py",
    "app/modules/accounts/services/two_factor_service.py",
    "app/http/requests/login_request.py",
    "app/http/requests/register_request.py",
    "app/http/controllers/auth_api_controller.py",
    "app/http/controllers/two_factor_api_controller.py",
    "routes/auth.py",
    "database/seeders/user_seeder.py",
    "app/auth/gates.py",
)


def _invoke(tmp_path: Path, monkeypatch, *args: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, ["make:auth", *args])


@pytest.fixture(autouse=True)
def _gate_isolation():
    # The generated gates.py template registers abilities on the shared
    # gate when any test imports it — reset around every test here.
    from fastplace.authz.gate import Gate

    Gate.reset_shared()
    yield
    Gate.reset_shared()


class TestMakeAuth:
    def test_scaffolds_the_auth_surface(self, tmp_path, monkeypatch):
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        for rel in _GENERATED:
            assert (tmp_path / rel).is_file(), f"missing {rel}"

    def test_templates_carry_their_content_markers(self, tmp_path, monkeypatch):
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        model = (tmp_path / "app/modules/accounts/models/user.py").read_text()
        assert "class User(Model)" in model
        assert "__hidden__" in model  # credential columns stay unserializable
        gates = (tmp_path / "app/auth/gates.py").read_text()
        assert "gate.define" in gates
        routes = (tmp_path / "routes/auth.py").read_text()
        assert '"/login"' in routes

    def test_rerun_never_clobbers(self, tmp_path, monkeypatch):
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        gates_path = tmp_path / "app/auth/gates.py"
        gates_path.write_text("# SENTINEL — user edits must survive\n")
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0
        assert "exists" in result.output
        assert "SENTINEL" in gates_path.read_text()

    def test_migration_flag_uses_the_manager(self, tmp_path, monkeypatch):
        made: list[str] = []

        class FakeManager:
            configured = True

            def scaffold(self) -> None:
                made.append("scaffold")

            def make(self, name: str) -> Path | None:
                made.append(name)
                return tmp_path / "database/migrations/versions/rev.py"

        import fastplace.cli.database as cli_db

        monkeypatch.setattr(cli_db, "_manager", lambda: FakeManager())
        result = _invoke(tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert made == ["create_users_table"]

    def test_prints_the_manual_checklist(self, tmp_path, monkeypatch):
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0
        for step in ("migrate", "db:seed", "MAIL_"):
            assert step in result.output
