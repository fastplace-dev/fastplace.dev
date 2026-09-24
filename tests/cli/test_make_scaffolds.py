"""``make:*`` backend scaffolders — seeder, job, request, middleware, policy,
test, scope, config, mail (spec #10–#18)."""

from __future__ import annotations

import pytest

from fastplace.cli import app as cli_app

# (argv, expected project-relative path, must-appear protocol markers)
_CASES = [
    pytest.param(
        ["make:seeder", "User"],
        "database/seeders/user_seeder.py",
        ["async def run() -> None:"],
        id="seeder",
    ),
    pytest.param(
        ["make:job", "SendInvoice"],
        "app/jobs/send_invoice_job.py",
        ["from fastplace.queue import Job", "@Job()", "async def send_invoice("],
        id="job",
    ),
    pytest.param(
        ["make:request", "StoreInvoice"],
        "app/http/requests/store_invoice_request.py",
        ["class StoreInvoiceRequest(BaseModel):", "Field("],
        id="request",
    ),
    pytest.param(
        ["make:middleware", "RequestId"],
        "app/http/middleware/request_id_middleware.py",
        ["class RequestIdMiddleware(Middleware):", "async def handle("],
        id="middleware",
    ),
    pytest.param(
        ["make:policy", "Invoice"],
        "app/authz/invoice_policy.py",
        ["class InvoicePolicy:", "async def view_any(", "async def view("],
        id="policy",
    ),
    pytest.param(
        ["make:test", "Invoice"],
        "tests/unit/test_invoice.py",
        ["def test_invoice("],
        id="test-unit",
    ),
    pytest.param(
        ["make:scope", "Invoice", "--module", "billing"],
        "app/modules/billing/models/scopes/invoice_scope.py",
        ["class InvoiceScope:", "def apply("],
        id="scope",
    ),
    pytest.param(
        ["make:config", "Billing"],
        "config/billing.py",
        ["BILLING"],
        id="config",
    ),
    pytest.param(
        ["make:mail", "InvoicePaid"],
        "app/mail/invoice_paid.py",
        ["from fastplace.mail import MailMessage", "-> MailMessage:", "text="],
        id="mail",
    ),
]


def _invoke(monkeypatch, tmp_path, *args: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(cli_app, list(args))


class TestBackendScaffolds:
    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_creates_stub_with_protocol_markers(self, tmp_path, monkeypatch, argv, rel, markers):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        path = tmp_path / rel
        assert path.is_file(), f"missing {rel}"
        source = path.read_text()
        for marker in markers:
            assert marker in source, f"{rel} lacks {marker!r}"
        # The stub must be valid Python as written, and the command reports
        # the created path.
        compile(source, str(path), "exec")
        assert rel in result.output

    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_refuses_to_overwrite_without_force(self, tmp_path, monkeypatch, argv, rel, markers):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        path = tmp_path / rel
        path.write_text("# SENTINEL — hand edits must survive\n")
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output
        assert "exists" in result.output
        assert "SENTINEL" in path.read_text()

    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_force_overwrites_the_stub(self, tmp_path, monkeypatch, argv, rel, markers):
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_text("# SENTINEL — replaced under --force\n")

        result = _invoke(monkeypatch, tmp_path, *argv, "--force")
        assert result.exit_code == 0, result.output
        source = path.read_text()
        assert "SENTINEL" not in source
        for marker in markers:
            assert marker in source, f"{rel} lacks {marker!r}"

    def test_job_policy_and_mail_write_package_markers(self, tmp_path, monkeypatch):
        for argv in (
            ["make:job", "SendInvoice"],
            ["make:policy", "Invoice"],
            ["make:mail", "InvoicePaid"],
        ):
            result = _invoke(monkeypatch, tmp_path, *argv)
            assert result.exit_code == 0, result.output
        for marker in ("app/jobs/__init__.py", "app/authz/__init__.py", "app/mail/__init__.py"):
            assert (tmp_path / marker).is_file(), f"missing {marker}"

    def test_make_test_feature_flag_targets_tests_http(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:test", "Invoice", "--feature")
        assert result.exit_code == 0, result.output
        feature = tmp_path / "tests" / "http" / "test_invoice.py"
        assert feature.is_file()
        assert "def test_invoice(" in feature.read_text()
        assert not (tmp_path / "tests" / "unit" / "test_invoice.py").exists()

    def test_make_scope_requires_the_module_option(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:scope", "Invoice")
        assert result.exit_code != 0
        assert not (tmp_path / "app" / "modules").exists()

    def test_rejects_path_shaped_names(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:seeder", "../evil")
        assert result.exit_code == 1
        assert not (tmp_path / "database").exists()


# ---------------------------------------------------------------------------
# --module path traversal (final review I-2) — a --module (or a make:model
# name) that escapes app/modules must be rejected, not written.
# ---------------------------------------------------------------------------

#: Commands taking --module directly, plus make:model whose module segment is
#: derived verbatim from the NAME argument (traversal shapes reach it that
#: way: name "../evil" → module "../evil").
_MODULE_COMMANDS = [
    ("make:scope", lambda module: ["make:scope", "Invoice", "--module", module]),
    ("make:service", lambda module: ["make:service", "Invoice", "--module", module]),
    ("make:repository", lambda module: ["make:repository", "Invoice", "--module", module]),
    ("make:model", lambda module: ["make:model", module]),
]

_TRAVERSAL_MODULES = ["../evil", "/etc", "a/../../b"]


class TestModuleTraversalRejected:
    @pytest.mark.parametrize("command,argv_for", _MODULE_COMMANDS)
    @pytest.mark.parametrize("module", _TRAVERSAL_MODULES)
    def test_rejects_traversal_modules(self, tmp_path, monkeypatch, command, argv_for, module):
        result = _invoke(monkeypatch, tmp_path, *argv_for(module))
        assert result.exit_code == 1, result.output
        assert "invalid module" in result.output
        # Nothing may be created outside (or inside) app/modules.
        assert not (tmp_path / "app").exists()
        assert not (tmp_path / "evil").exists()

    @pytest.mark.parametrize(
        ("module", "expected_rel"),
        [
            ("blog", "app/modules/blog/services/invoice_service.py"),
            ("shop/billing", "app/modules/shop/billing/services/invoice_service.py"),
            ("shop.billing", "app/modules/shop.billing/services/invoice_service.py"),
        ],
        ids=["single", "multi-segment", "dotted"],
    )
    def test_legitimate_module_names_still_generate(
        self, tmp_path, monkeypatch, module, expected_rel
    ):
        result = _invoke(monkeypatch, tmp_path, "make:service", "Invoice", "--module", module)
        assert result.exit_code == 0, result.output
        assert (tmp_path / expected_rel).is_file(), f"missing {expected_rel}"
