"""Gap-fix wave assertions on the emitted auth scaffold (2026-09 audit).

Every test either scaffolds with ``make:auth`` into ``tmp_path`` and asserts
on the GENERATED content, or asserts on the shipped starter corpus directly
(the files ``fastplace new`` copies verbatim).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastplace.cli import app as cli_app
from fastplace.cli.generators import scaffold_templates_dir

CORPUS = Path(scaffold_templates_dir())


def _invoke(tmp_path: Path, monkeypatch, *args: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, ["make:auth", *args])


@pytest.fixture(autouse=True)
def _gate_isolation():
    from fastplace.authz.gate import Gate

    Gate.reset_shared()
    yield
    Gate.reset_shared()


@pytest.fixture()
def scaffolded(tmp_path, monkeypatch):
    """A scaffolded app plus the base project files `fastplace new` writes."""
    _minimal_project_files(tmp_path)
    result = _invoke(tmp_path, monkeypatch, "--no-migration")
    assert result.exit_code == 0, result.output
    return tmp_path


def _minimal_project_files(tmp_path: Path) -> None:
    """Stand-ins for the non-auth files only `fastplace new` writes."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = [\n    "fastplace",\n]\n'
    )
    (tmp_path / ".env").write_text("APP_KEY=k\n")
    (tmp_path / ".env.example").write_text("APP_KEY=\n")
    (tmp_path / "index.html").write_text(
        "<!doctype html>\n<html>\n<head>\n<title>demo</title>\n</head>\n"
        '<body>\n<div id="fastplace"></div>\n</body>\n</html>\n'
    )
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Parked form targets\n\nThe starter ships the full "
        "settings UI. These form targets are intentionally\nunrouted until "
        "you wire their backends.\n"
    )


class TestSettingsWriteEndpoints:
    def test_write_routes_are_emitted(self, scaffolded):
        routes = (scaffolded / "routes/auth.py").read_text()
        assert 'router.patch(\n    "/settings/profile"' in routes
        assert 'router.put(\n    "/settings/password"' in routes
        assert 'router.delete(\n    "/settings/profile"' in routes
        assert 'router.get(\n    "/settings",' in routes

    def test_profile_route_gates_on_verified(self, scaffolded):
        routes = (scaffolded / "routes/auth.py").read_text()
        assert 'middleware=["auth", "verified", "throttle:6,60"]' in routes

    def test_password_route_is_throttled_six_per_minute(self, scaffolded):
        routes = (scaffolded / "routes/auth.py").read_text()
        assert '"auth", "throttle:6,60"' in routes

    def test_controller_is_emitted(self, scaffolded):
        controller = (scaffolded / "app/http/controllers/settings_api_controller.py").read_text()
        assert "Profile updated." in controller
        assert "Password updated." in controller
        assert "validate_credentials" in controller
        assert "The password is incorrect." in controller
        assert "Hash.make" in controller
        # Email change re-enters the verification pipeline.
        assert "email_verified_at=None" in controller
        assert "send_link" in controller
        # Deletion is a hard delete and takes every session with it.
        assert "destroy_for_user" in controller
        assert "await user.force_delete()" in controller

    def test_account_deletion_frees_the_unique_email(self, scaffolded):
        """Model.delete() only stamps deleted_at — the tombstone keeps the
        unique email locked, so re-registering the address 500s. The destroy
        route must force_delete the row instead."""
        controller = (scaffolded / "app/http/controllers/settings_api_controller.py").read_text()
        assert "await user.force_delete()" in controller
        assert "await user.delete()" not in controller
        assert "The email has already been taken." in controller

    def test_request_templates_are_emitted(self, scaffolded):
        requests = scaffolded / "app/http/requests"
        profile = (requests / "profile_request.py").read_text()
        assert "EmailStr" in profile
        password = (requests / "password_update_request.py").read_text()
        assert "current_password" in password
        assert (requests / "delete_profile_request.py").is_file()

    def test_readme_parked_targets_section_is_replaced(self, scaffolded):
        readme = (scaffolded / "README.md").read_text()
        assert "Parked form targets" not in readme
        assert "PATCH `/settings/profile`" in readme

    def test_readme_without_the_section_is_left_alone(self, tmp_path, monkeypatch):
        _minimal_project_files(tmp_path)
        (tmp_path / "README.md").write_text("# Demo\n\nCustom readme body.\n")
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        assert "Custom readme body." in (tmp_path / "README.md").read_text()


class TestFlashBridge:
    def test_shared_props_module_is_emitted_and_registered(self, scaffolded):
        shared = (scaffolded / "app/http/shared_props.py").read_text()
        assert "FLASH_SESSION_KEY" in shared
        assert '"toast"' in shared
        assert '"success"' in shared
        routes = (scaffolded / "routes/auth.py").read_text()
        assert "register_flash_props()" in routes

    def test_share_callback_registers_through_the_render_api(self, scaffolded):
        shared = (scaffolded / "app/http/shared_props.py").read_text()
        assert "share(" in shared


class TestRouteGating:
    def test_verify_link_redemption_is_throttled(self, scaffolded):
        routes = (scaffolded / "routes/auth.py").read_text()
        fulfill = routes.split('"/email/verify/{id}/{hash}"')[1].split("router.post")[0]
        assert '"throttle:6,60"' in fulfill

    def test_profile_update_route_is_throttled(self, scaffolded):
        """A profile PATCH re-mails the verification link on every email
        change — the route must share the section's 6-per-minute throttle,
        or a scripted loop turns the mail transport into a bombing relay."""
        routes = (scaffolded / "routes/auth.py").read_text()
        block = routes.split('router.patch(\n    "/settings/profile"')[1].split("router.put")[0]
        assert '"verified"' in block
        assert '"throttle:6,60"' in block

    def test_settings_index_redirects_into_the_section(self, scaffolded):
        controller = (scaffolded / "app/http/controllers/settings_api_controller.py").read_text()
        assert 'Redirect("/settings/profile", status_code=303)' in controller

    @pytest.mark.parametrize(
        ("route_block", "controller"),
        [
            ('router.get(\n    "/settings/appearance"', "SettingsAppearanceController"),
            ('router.get(\n    "/settings/profile"', "SettingsPagesController"),
            ('router.get(\n    "/settings/security"', "SettingsPagesController"),
        ],
    )
    def test_settings_pages_gate_on_verified(self, route_block, controller):
        """Every settings GET page sits behind `verified` — an unverified user
        must land on /email/verify, not read the settings shell. Asserted on
        the template constant directly: `fastplace new` writes it to
        routes/web.py verbatim (no interpolation)."""
        from fastplace.cli.generators import _WEB_ROUTES_AUTH_TEMPLATE

        block = _WEB_ROUTES_AUTH_TEMPLATE.split(route_block)[1].split("router.get")[0]
        assert controller in block
        assert 'middleware=["auth", "verified"]' in block


class TestEmailFormatValidation:
    @pytest.mark.parametrize(
        "request_file",
        [
            "register_request.py",
            "forgot_password_request.py",
            "reset_password_request.py",
            "profile_request.py",
        ],
    )
    def test_email_fields_are_emailstr(self, scaffolded, request_file):
        content = (scaffolded / "app/http/requests" / request_file).read_text()
        assert "EmailStr" in content, request_file

    def test_register_name_strips_before_length_validation(self, scaffolded):
        content = (scaffolded / "app/http/requests/register_request.py").read_text()
        assert 'field_validator("name", mode="before")' in content
        assert "value.strip()" in content
        assert "min_length=1" in content


class TestVerificationResend:
    def test_resend_short_circuits_already_verified_users(self, scaffolded):
        service = (scaffolded / "app/modules/accounts/services/verification_service.py").read_text()
        assert "email_verified_at is not None" in service
        assert "return None" in service

    def test_controller_answers_204_json_when_already_verified(self, scaffolded):
        controller = (scaffolded / "app/http/controllers/auth_api_controller.py").read_text()
        assert "status_code=204" in controller
        assert "Redirect(request.intended(), status_code=303)" in controller


class TestEmittedTestTree:
    def test_conftest_and_feature_suite_are_emitted(self, scaffolded):
        conftest = scaffolded / "tests/conftest.py"
        assert conftest.is_file()
        content = conftest.read_text()
        assert "ASGITransport" in content
        assert "db.create_all" in content
        assert (scaffolded / "tests/feature/test_auth_flow.py").is_file()

    def test_pyproject_gains_test_tooling_and_dev_extra(self, scaffolded):
        pyproject = (scaffolded / "pyproject.toml").read_text()
        assert "[tool.pytest.ini_options]" in pyproject
        assert 'asyncio_mode = "auto"' in pyproject
        assert 'testpaths = ["tests"]' in pyproject
        assert "[project.optional-dependencies]" in pyproject
        assert '"pytest' in pyproject
        assert "httpx" in pyproject
        assert "[tool.ruff]" in pyproject
        assert "[tool.mypy]" in pyproject
        assert "email-validator" in pyproject

    def test_pyproject_without_dependency_block_is_not_corrupted(self, tmp_path, monkeypatch):
        _minimal_project_files(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        content = (tmp_path / "pyproject.toml").read_text()
        assert content.startswith("[project]\n")


class TestToolingConfig:
    def test_editorconfig_ships_in_the_corpus(self):
        assert (CORPUS / ".editorconfig").is_file()

    def test_ci_workflow_ships_with_the_auth_surface(self, scaffolded):
        workflow = scaffolded / ".github/workflows/tests.yml"
        assert workflow.is_file()
        content = workflow.read_text()
        assert "npm run test" in content
        assert "pytest" in content

    def test_dependabot_ships_with_the_auth_surface(self, scaffolded):
        assert (scaffolded / ".github/dependabot.yml").is_file()

    def test_mail_keys_land_in_env_and_env_example(self, scaffolded):
        assert "MAIL_DRIVER" in (scaffolded / ".env").read_text()
        assert "MAIL_DRIVER" in (scaffolded / ".env.example").read_text()
        assert (scaffolded / "config/mail.py").is_file()

    def test_mail_config_defaults_are_emitted(self, scaffolded):
        content = (scaffolded / "config/mail.py").read_text()
        assert "MAIL_DRIVER" in content
        assert "MAIL_FROM_ADDRESS" in content

    def test_robots_and_touch_icon_ship_in_public(self):
        assert (CORPUS / "public/robots.txt").is_file()
        assert (CORPUS / "public/apple-touch-icon.png").is_file()

    def test_index_html_gains_icon_link_tags(self, scaffolded):
        index = (scaffolded / "index.html").read_text()
        assert '<link rel="icon"' in index
        assert 'rel="apple-touch-icon"' in index

    def test_index_html_icons_are_idempotent(self, scaffolded, tmp_path, monkeypatch):
        index = tmp_path / "index.html"
        first = index.read_text()
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        assert index.read_text() == first or index.read_text().count('rel="icon"') == 1


class TestHtmlMail:
    def test_verification_and_reset_mail_carry_html(self, scaffolded):
        assert (scaffolded / "app/modules/accounts/services/mail_views.py").is_file()
        service = (scaffolded / "app/modules/accounts/services/verification_service.py").read_text()
        assert "message.html = " in service
        reset = (scaffolded / "app/modules/accounts/services/password_reset_service.py").read_text()
        assert "message.html = " in reset

    def test_html_views_render_a_button_and_fallback_url(self, scaffolded):
        views = (scaffolded / "app/modules/accounts/services/mail_views.py").read_text()
        assert "<a href=" in views
        assert "word-break:break-all" in views


class TestSeeder:
    def test_working_database_seeder_is_emitted(self, scaffolded):
        seeder = scaffolded / "database/seeders/database_seeder.py"
        assert seeder.is_file()
        content = seeder.read_text()
        assert "async def run(" in content
        assert "create_user" in content

    def test_seeder_guard_checks_the_email_it_creates(self, scaffolded):
        """db:seed claims idempotency: the guard's lookup email must equal
        the created account's email, or every rerun attempts a duplicate
        insert against the unique-email constraint."""
        import re

        content = (scaffolded / "database/seeders/database_seeder.py").read_text()
        guarded = re.search(r'find_by_email\(\s*"([^"]+)"', content)
        created = re.search(r'email="([^"]+)"', content)
        assert guarded is not None, content
        assert created is not None, content
        assert guarded.group(1) == created.group(1)


class TestI18nStub:
    def test_lang_example_config_key_and_helper_ship(self):
        lang = CORPUS / "lang/en/messages.py"
        assert lang.is_file()
        assert "LINES" in lang.read_text()
        locale_config = CORPUS / "config/locale.py"
        assert locale_config.is_file()
        assert "APP_LOCALE" in locale_config.read_text()
        helper = CORPUS / "app/support/lang.py"
        assert helper.is_file()
        assert "def __(" in helper.read_text()

    def test_locale_keys_are_documented_in_env_example(self, scaffolded):
        env = (scaffolded / ".env.example").read_text()
        assert "APP_LOCALE" in env
        assert "APP_FALLBACK_LOCALE" in env


class TestWelcomeCopy:
    def test_welcome_page_carries_fastplace_copy(self):
        home = (CORPUS / "resources/js/pages/Home/Index.tsx").read_text()
        assert "incredibly rich ecosystem" not in home
        assert "Fastplace" in home
        # No stripped-link placeholder spans: the doc/tutorial list is gone.
        assert "Read the Documentation" not in home


class TestPasswordChangeRevokesSessions:
    def test_password_update_sweeps_other_sessions_and_rotates_this_one(self, scaffolded):
        """A rotated password must kill what a thief already holds: every
        OTHER session dies (guard.logout_other_devices also rotates the
        remember token) and the current session regenerates its ID."""
        controller = (scaffolded / "app/http/controllers/settings_api_controller.py").read_text()
        assert "logout_other_devices" in controller
        assert "request.session.regenerate()" in controller
        # Revocation runs after the rehash — and never before the current
        # password has been verified.
        assert controller.index("logout_other_devices") > controller.index("Hash.make")


class TestEmittedConftestIsolation:
    def test_conftest_resets_the_shared_props_registry(self, scaffolded):
        """share() registrations live in the framework package (purge_app_modules
        cannot see them), so the emitted conftest must reset them itself."""
        conftest = (scaffolded / "tests/conftest.py").read_text()
        assert "reset_shared_props" in conftest


class TestReadmeDriftDegradation:
    def test_readme_mentioning_the_phrase_without_the_heading_is_left_alone(
        self, tmp_path, monkeypatch
    ):
        """The rewrite splices on the exact '## ' heading; a README that only
        mentions the phrase must skip the rewrite — never raise."""
        _minimal_project_files(tmp_path)
        (tmp_path / "README.md").write_text(
            "# Demo\n\nThe Parked form targets section lives elsewhere.\n"
        )
        result = _invoke(tmp_path, monkeypatch, "--no-migration")
        assert result.exit_code == 0, result.output
        readme = (tmp_path / "README.md").read_text()
        assert "lives elsewhere" in readme
        assert "Settings flows" not in readme


class TestEmailChangeRace:
    def test_integrity_error_translates_to_the_friendly_validation_error(self, scaffolded):
        """The find-first unique check can lose a race to a concurrent
        registration; the emitted controller must answer the UNIQUE
        constraint with the same 422, not a 500."""
        controller = (scaffolded / "app/http/controllers/settings_api_controller.py").read_text()
        assert "from sqlalchemy.exc import IntegrityError" in controller
        assert "except IntegrityError:" in controller
        assert "The email has already been taken." in controller
