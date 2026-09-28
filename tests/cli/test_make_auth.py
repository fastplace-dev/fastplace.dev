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

    def test_generated_two_factor_enforces_single_use_totp(self, tmp_path, monkeypatch):
        """tfa-G2: the scaffold must ship the single-use TOTP contract, not the
        stateless verifier — a replayed code must not complete two challenges."""
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        service = (tmp_path / "app/modules/accounts/services/two_factor_service.py").read_text()
        assert "verify_code_step(" in service  # the step-returning primitive
        assert "verify_code(" not in service  # the stateless one must be gone
        assert "user.two_factor_accepted_step = step" in service
        model = (tmp_path / "app/modules/accounts/models/user.py").read_text()
        assert "two_factor_accepted_step: int | None = None" in model
        assert '"two_factor_accepted_step"' in model  # hidden from serialization
        # Rotation resets the mark; teardown wipes it with the rest.
        assert "user.two_factor_accepted_step = None" in service

    def test_generated_password_reset_does_not_resurrect_the_reset_session(
        self, tmp_path, monkeypatch
    ):
        """tfa-G4: the reset device's dirty session must not be re-persisted by
        the response-time writer after destroy_for_user — the template ships
        the clear + CSRF rotation + id-regeneration logout block."""
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        service = (tmp_path / "app/modules/accounts/services/password_reset_service.py").read_text()
        assert "await store.destroy_for_user(user.id)" in service
        assert "session.clear()" in service
        assert "CSRF_SESSION_KEY" in service
        assert "regenerate()" in service
        assert "queue_remember_cookie(request, None)" in service
        assert "await pat_store().revoke_all_for_user(user.id)" in service

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
        for step in ("migrate", "/register", "admin", "MAIL_"):
            assert step in result.output

    def test_make_auth_ships_no_seeder(self, tmp_path, monkeypatch):
        # Binding ruling: admin is never seeded — the first real /register
        # signup becomes the admin, so no seeder may exist on any path.
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "database" / "seeders" / "user_seeder.py").exists()

    def test_gates_hook_keys_on_is_admin(self, tmp_path, monkeypatch):
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        gates = (tmp_path / "app/auth/gates.py").read_text()
        assert "is_admin" in gates
        assert "admin@example.com" not in gates


def test_no_admin_literal_in_generated_tree(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_app, ["new", "blog", "--auth"])
    assert result.exit_code == 0, result.output
    hits = [
        str(p.relative_to(tmp_path / "blog"))
        for p in (tmp_path / "blog").rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".md", ".ts", ".tsx", ".js", ".jsx"}
        and "admin@example.com" in p.read_text(errors="ignore")
    ]
    assert hits == []


def test_manifest_lists_every_auth_file():
    """The shared manifest is the single source of truth for the auth surface —
    both ``fastplace new --auth`` and ``make:auth`` consume it."""
    from fastplace.cli.auth_scaffold import AUTH_FILES

    rels = [rel for rel, _ in AUTH_FILES]
    assert "routes/auth.py" in rels
    assert "app/modules/accounts/models/user.py" in rels
    assert "app/auth/gates.py" in rels
    assert len(rels) == len(set(rels))  # no duplicates
    # The templated file count must not silently shrink. (19 since the
    # seeder left the manifest — admin is never seeded, binding ruling —
    # and the mail job + the models re-export joined it.)
    assert len(rels) >= 19


def test_make_auth_writes_the_mail_verification_job(tmp_path, monkeypatch):
    """Without app/jobs/mail.py the kernel's import_jobs() finds nothing: the
    Registered event dispatches with no listener, the verification mail never
    sends, and `verified` locks the fresh account out of /dashboard forever."""
    result = _invoke(tmp_path, monkeypatch, "--no-migration")
    assert result.exit_code == 0, result.output

    mail_job = tmp_path / "app" / "jobs" / "mail.py"
    assert mail_job.is_file(), "app/jobs/mail.py missing from the auth scaffold"
    source = mail_job.read_text()
    assert '@Job(name="mail_send")' in source
    assert "async def send_registration_verification" in source
    assert 'listen("Registered", send_registration_verification)' in source


def test_make_auth_models_init_reexports_user(tmp_path, monkeypatch):
    """config/auth.py points the ORM provider at the dotted path
    app.modules.accounts.models.User — an empty models __init__ makes that
    getattr blow up with AttributeError on every authenticated request."""
    result = _invoke(tmp_path, monkeypatch, "--no-migration")
    assert result.exit_code == 0, result.output

    init = (tmp_path / "app" / "modules" / "accounts" / "models" / "__init__.py").read_text()
    assert "from app.modules.accounts.models.user import User" in init
    assert "__all__" in init
