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


class TestTwoFactorStatusRoute:
    """a11y2-G5: after confirming their password, the 2FA enable journey 404s
    — the frozen frontend GETs /user/two-factor-authentication to recover and
    the scaffolded surface had no such route. The status GET must exist and
    walk browser visitors back to the security page."""

    def test_status_route_is_emitted_with_the_section_middleware(self, scaffolded):
        routes = (scaffolded / "routes/auth.py").read_text()
        block = routes.split('router.get(\n    "/user/two-factor-authentication"')[1]
        block = block.split("router.")[0]
        assert "TwoFactorApiController" in block
        assert '"status"' in block
        assert 'name="auth.two_factor.status"' in block
        assert "middleware=_TWO_FACTOR_MIDDLEWARE" in block

    def test_status_controller_redirects_browsers_and_answers_api_clients(self, scaffolded):
        controller = (scaffolded / "app/http/controllers/two_factor_api_controller.py").read_text()
        assert "async def status" in controller
        # Browser/fetch visitors (bridge header or no JSON Accept) walk back
        # to the security page; the fetch layer follows the 303 and swaps in
        # the page payload, so a re-click of Enable runs against a confirmed
        # session. JSON clients get the boolean.
        assert 'Redirect("/settings/security", status_code=303)' in controller
        assert 'Json({"enabled": enabled})' in controller

    def test_service_exposes_is_enabled(self, scaffolded):
        service = (scaffolded / "app/modules/accounts/services/two_factor_service.py").read_text()
        assert "async def is_enabled" in service
        # R10 semantics: the flag check runs before the verdict, so a
        # disabled surface 404s here exactly like every other method.
        body = service.split("async def is_enabled")[1].split("async def")[0]
        assert "_require_enabled()" in body
        assert "two_factor_confirmed_at" in body


class TestSettingsServiceBoundary:
    """supp-2-G6: the settings controller reached straight into the accounts
    module's repository layer — a lint:modules violation shipped green in
    every scaffolded app because nothing re-ran the lint on the tree."""

    def test_user_service_is_emitted_at_the_service_seam(self, scaffolded):
        service = scaffolded / "app/modules/accounts/services/user_service.py"
        assert service.is_file()
        content = service.read_text()
        assert "class UserService" in content
        assert "async def email_in_use" in content

    def test_settings_controller_consumes_the_service_not_the_repository(self, scaffolded):
        controller = (scaffolded / "app/http/controllers/settings_api_controller.py").read_text()
        assert "from app.modules.accounts.services.user_service import UserService" in controller
        assert "UserRepository" not in controller
        assert "email_in_use" in controller

    def test_scaffolded_tree_passes_module_lint(self, scaffolded):
        from fastplace.modules import lint_imports

        violations = lint_imports(scaffolded)
        assert violations == [], [v.message for v in violations]


class TestPyprojectExtras:
    def test_dependency_carries_queue_and_webauthn_extras(self, scaffolded):
        """q2-G6: the scaffold ships app/jobs/mail.py (the queue-backed
        verification listener), so the emitted dependency needs the queue
        extra beside webauthn — not webauthn alone."""
        import tomllib

        data = tomllib.loads((scaffolded / "pyproject.toml").read_text())
        deps = data["project"]["dependencies"]
        assert any(dep.startswith("fastplace[queue,webauthn]") for dep in deps), deps

    def test_mysql_extra_ships_documented(self, scaffolded):
        """mysql-G4: parity with the framework's own extras — a scaffolded
        app can `pip install -e ".[mysql]"` without inventing the pins."""
        import tomllib

        data = tomllib.loads((scaffolded / "pyproject.toml").read_text())
        extras = data["project"]["optional-dependencies"]
        assert "mysql" in extras
        assert "asyncmy>=0.2.9" in extras["mysql"]
        assert "cryptography>=42" in extras["mysql"]


class TestPolicyModuleOption:
    """sweep-G12: policies belong under app/modules/<name>/policies/ where
    Gate auto-discovers them; make:policy could only write app/authz/."""

    def test_module_option_writes_the_discovered_path(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        (tmp_path / "app" / "modules" / "billing" / "models").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli_app, ["make:policy", "Invoice", "--module", "billing"])
        assert result.exit_code == 0, result.output
        # Symmetric with the app/authz default layout: <stem>_policy.py file
        # inside the module's policies/ package.
        assert (
            tmp_path / "app" / "modules" / "billing" / "policies" / "invoice_policy.py"
        ).is_file()
        assert (tmp_path / "app" / "modules" / "billing" / "policies" / "__init__.py").is_file()

    def test_module_layout_resolves_through_gate_discovery(self, tmp_path, monkeypatch):
        """sweep-G12's point: Gate._resolve_policy must actually FIND the
        emitted policy. Discovery reads the <ModelName>Policy attribute off
        the app/modules/<m>/policies package, so the package __init__ must
        re-export the class — an empty marker leaves the binding dead and
        every authorized action on the model raises ConfigurationError."""
        import importlib
        import sys

        from typer.testing import CliRunner

        # Regular-package markers so the project's app/ (first on sys.path
        # below) beats the framework checkout's own app/ — a regular package
        # anywhere on sys.path wins over a namespace portion earlier on it.
        (tmp_path / "app" / "modules" / "billing" / "models").mkdir(parents=True)
        for marker in (
            "app/__init__.py",
            "app/modules/__init__.py",
            "app/modules/billing/__init__.py",
        ):
            (tmp_path / marker).write_text("")
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli_app, ["make:policy", "Invoice", "--module", "billing"])
        assert result.exit_code == 0, result.output

        saved = {k: m for k, m in sys.modules.items() if k == "app" or k.startswith("app.")}
        for key in saved:
            sys.modules.pop(key, None)
        monkeypatch.syspath_prepend(str(tmp_path))
        try:
            policies = importlib.import_module("app.modules.billing.policies")
            assert hasattr(policies, "InvoicePolicy"), sorted(policies.__dict__)

            class Invoice:
                pass  # discovery only reads __module__ / __name__

            Invoice.__module__ = "app.modules.billing.models.invoice"
            from fastplace.authz.gate import Gate

            resolved = Gate()._resolve_policy(Invoice)
            assert resolved is not None
            assert resolved.__name__ == "InvoicePolicy"
        finally:
            for key in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
                sys.modules.pop(key)
            sys.modules.update(saved)

    def test_second_policy_in_a_module_appends_its_re_export(self, tmp_path, monkeypatch):
        """Two policies in one module: the second run must EXTEND the package
        __init__, not skip it (the non-clobbering _write would leave the new
        class undiscovered)."""
        from typer.testing import CliRunner

        (tmp_path / "app" / "modules" / "billing" / "models").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        assert (
            runner.invoke(cli_app, ["make:policy", "Invoice", "--module", "billing"]).exit_code == 0
        )
        assert (
            runner.invoke(cli_app, ["make:policy", "CreditNote", "--module", "billing"]).exit_code
            == 0
        )

        init = (tmp_path / "app" / "modules" / "billing" / "policies" / "__init__.py").read_text()
        assert "InvoicePolicy" in init
        assert "CreditNotePolicy" in init

    def test_module_option_rejects_path_escapes(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli_app, ["make:policy", "Invoice", "--module", "../evil"])
        assert result.exit_code != 0
        assert not (tmp_path / "app" / "modules" / "evil").exists()

    def test_default_stays_app_authz(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(cli_app, ["make:policy", "Invoice"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "app" / "authz" / "invoice_policy.py").is_file()

    def test_default_template_documents_the_module_option(self):
        from fastplace.cli.generators import _POLICY_TEMPLATE

        content = _POLICY_TEMPLATE.format(name="Invoice")
        assert "--module" in content
        # The documented mechanism must match Gate._resolve_policy: the
        # <Model>Policy ATTRIBUTE on the policies package — not a filename
        # scan (the comment once described a path discovery never reads).
        assert "Gate reads the InvoicePolicy attribute" in content


class TestAlertDestructiveTokens:
    def test_template_alert_carries_option_b_destructive_tokens(self):
        """The destructive variant must survive both themes by contrast, not
        by a foreground token that collapses onto the alert's own background.
        W5 tuned the description slot to full text-destructive — the /80
        alpha blend lands under 4.5:1 on the light surface (a11y2-G1)."""
        alert = (CORPUS / "resources/js/components/ui/alert.tsx").read_text()
        assert "text-destructive border-destructive/50" in alert
        assert "*:data-[slot=alert-description]:text-destructive" in alert
        assert "*:data-[slot=alert-description]:text-destructive/80" not in alert


class TestEmittedTwoFactorJourney:
    def test_feature_suite_carries_the_full_enable_journey(self, scaffolded):
        """The emitted suite must exercise the shipped 2FA UI's loop over
        HTTP — password-confirmation wall, status recovery, QR/secret, a
        pyotp-confirmed enable, and the recovery codes — with pyotp (a core
        framework dependency, no extra install)."""
        suite = (scaffolded / "tests/feature/test_auth_flow.py").read_text()
        assert "import pyotp" in suite
        assert '"/user/two-factor-authentication"' in suite
        assert '"/user/confirm-password"' in suite
        assert '"/user/two-factor-qr-code"' in suite
        assert '"/user/two-factor-secret-key"' in suite
        assert '"/user/confirmed-two-factor-authentication"' in suite
        assert '"/user/two-factor-recovery-codes"' in suite
        assert "pyotp.TOTP" in suite
