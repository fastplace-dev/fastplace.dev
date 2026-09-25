"""T4.5 — module system: discovery + AST import-boundary linting.

Rules enforced (CSR, blueprint §"modular monolith"):
  * one bounded module's code may import another module's **services** (the
    public seam) but never its ``models`` or ``repositories``;
  * code outside ``app/modules`` (controllers, jobs, agents, routes) goes
    through services too — never models/repositories directly.
"""

from __future__ import annotations

from pathlib import Path

from fastplace.modules import discover_modules, lint_imports


def make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "proj"
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


CLEAN_PROJECT = {
    "app/modules/billing/__init__.py": "",
    "app/modules/billing/models/invoice.py": (
        "from fastplace.orm import Model\n\n\nclass Invoice(Model):\n    pass\n"
    ),
    "app/modules/billing/repositories/invoice_repository.py": (
        "from app.modules.billing.models.invoice import Invoice\n"
    ),
    "app/modules/billing/services/invoice_service.py": (
        # same-module model/repository access through relative imports is fine
        "from ..models.invoice import Invoice\n"
        "from ..repositories.invoice_repository import InvoiceRepository\n"
    ),
    "app/modules/accounts/__init__.py": "",
    "app/modules/accounts/services/ledger_service.py": (
        # cross-module access happens through the other module's services
        "from app.modules.billing.services.invoice_service import InvoiceService\n"
    ),
    "app/http/controllers/invoices_controller.py": (
        "from app.modules.billing.services.invoice_service import InvoiceService\n"
    ),
    "app/jobs/email_job.py": (
        "from app.modules.accounts.services.ledger_service import LedgerService\n"
        "from app.models import BaseModel\n"
    ),
    "app/ai/agents/assistant.py": (
        "from app.modules.billing.services.invoice_service import InvoiceService\n"
    ),
}


def test_discovers_modules_under_app_modules(tmp_path):
    root = make_project(tmp_path, CLEAN_PROJECT)
    found = discover_modules(root)
    assert set(found) == {"billing", "accounts"}
    assert found["billing"].path == root / "app" / "modules" / "billing"


def test_discovery_ignores_non_module_dirs(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/not_a_module_file.py"] = "x = 1"
    root = make_project(tmp_path, files)
    assert "not_a_module_file.py" not in discover_modules(root)


def test_clean_project_has_no_violations(tmp_path):
    root = make_project(tmp_path, CLEAN_PROJECT)
    assert lint_imports(root) == []


def test_cross_module_model_import_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/ledger_service.py"] = (
        "from app.modules.billing.models.invoice import Invoice\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    v = violations[0]
    assert v.import_target == "app.modules.billing.models.invoice"
    assert str(v.file).endswith("ledger_service.py")
    assert v.line == 1


def test_cross_module_repository_import_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/ledger_service.py"] = (
        "from app.modules.billing.repositories.invoice_repository import InvoiceRepository\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    assert "repositories" in violations[0].import_target


def test_controller_importing_repository_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/http/controllers/invoices_controller.py"] = (
        "from app.modules.billing.repositories.invoice_repository import InvoiceRepository\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    assert violations[0].file.name == "invoices_controller.py"


def test_controller_importing_model_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/http/controllers/invoices_controller.py"] = (
        "import app.modules.billing.models.invoice\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1


def test_relative_import_escaping_the_module_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    # three dots from app/modules/accounts/services → app.modules → billing
    files["app/modules/accounts/services/ledger_service.py"] = (
        "from ...billing.models.invoice import Invoice\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    assert violations[0].import_target == "app.modules.billing.models.invoice"


def test_importing_the_models_package_itself_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/ledger_service.py"] = (
        "from app.modules.billing import models\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    assert violations[0].import_target == "app.modules.billing.models"


def test_multiple_violations_are_all_reported(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/ledger_service.py"] = (
        "from app.modules.billing.models.invoice import Invoice\n"
        "from app.modules.billing.repositories.invoice_repository import InvoiceRepository\n"
    )
    files["app/jobs/nightly_job.py"] = "from app.modules.accounts.models.user import User\n"
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 3


def test_empty_app_tree_lints_clean(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert lint_imports(root) == []


# ---------------------------------------------------------------------------
# review-hardening: encoding, routes/, __init__ packages, dynamic + star imports
# ---------------------------------------------------------------------------


def test_non_utf8_source_file_does_not_crash_the_lint(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    legacy = root / "app" / "legacy.py"
    legacy.parent.mkdir(parents=True)
    # latin-1 bytes with a PEP 263 coding cookie — ast.parse from bytes honors it
    legacy.write_bytes("# -*- coding: latin-1 -*-\nx = 'caf\xe9'\n".encode("latin-1"))
    assert lint_imports(root) == []


def test_routes_importing_module_models_are_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["routes/api.py"] = "from app.modules.billing.models.invoice import Invoice\n"
    files["routes/web.py"] = (
        "from app.modules.billing.services.invoice_service import InvoiceService\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    v = violations[0]
    assert v.file.name == "api.py"
    assert v.rule == "csr:outside-module"
    assert v.import_target == "app.modules.billing.models.invoice"


def test_relative_import_inside_package_init_resolves_correctly(tmp_path):
    files = dict(CLEAN_PROJECT)
    # From billing/__init__.py (a package), one dot = the package itself, so
    # .models.invoice is billing's own models — same module, must NOT flag.
    files["app/modules/billing/__init__.py"] = "from .models.invoice import Invoice\n"
    # But accounts/__init__.py reaching into billing via ..billing IS cross-module.
    files["app/modules/accounts/__init__.py"] = "from ..billing.models.invoice import Invoice\n"
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    assert violations[0].import_target == "app.modules.billing.models.invoice"
    assert str(violations[0].file).endswith("app/modules/accounts/__init__.py")


def test_importlib_dynamic_import_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/ledger_service.py"] = (
        "import importlib\n"
        "Invoice = importlib.import_module('app.modules.billing.models.invoice').Invoice\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1
    assert violations[0].import_target == "app.modules.billing.models.invoice"


def test_dunder_import_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/http/controllers/invoices_controller.py"] = (
        "__import__('app.modules.billing.models.invoice')\n"
    )
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) == 1


def test_dynamic_import_with_non_constant_argument_is_ignored(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/http/controllers/invoices_controller.py"] = (
        "import importlib\nname = 'app.modules.billing.' + 'models.invoice'\n"
        "importlib.import_module(name)\n"
    )
    assert lint_imports(make_project(tmp_path, files)) == []


def test_star_import_of_a_module_root_is_flagged(tmp_path):
    files = dict(CLEAN_PROJECT)
    files["app/modules/accounts/services/ledger_service.py"] = "from app.modules.billing import *\n"
    violations = lint_imports(make_project(tmp_path, files))
    assert len(violations) >= 1
    assert any(v.import_target.endswith("models") for v in violations)


# ---------------------------------------------------------------------------
# module http layer is private — controllers/requests are a module's own edge
# ---------------------------------------------------------------------------


def test_cross_module_http_import_flagged(tmp_path):
    """Another module importing billing's http layer is a boundary violation."""
    project = make_project(
        tmp_path,
        {
            "app/modules/billing/__init__.py": "",
            "app/modules/billing/http/__init__.py": "",
            "app/modules/billing/http/controllers/__init__.py": "",
            "app/modules/billing/services/__init__.py": "",
            "app/modules/accounts/__init__.py": "",
            "app/modules/accounts/services/__init__.py": "",
            "app/modules/accounts/services/account_service.py": (
                "from app.modules.billing.http.controllers.billing_controller import BillingController\n"
            ),
        },
    )

    violations = lint_imports(project)

    assert any(v.rule == "module:private-layer" for v in violations)


def test_centralized_controller_importing_module_http_flagged(tmp_path):
    """app/http may not reach into a module's private http edge either."""
    project = make_project(
        tmp_path,
        {
            "app/http/controllers/__init__.py": "",
            "app/http/controllers/invoice_controller.py": (
                "from app.modules.billing.http.requests.store_billing_request import StoreBillingRequest\n"
            ),
            "app/modules/billing/__init__.py": "",
            "app/modules/billing/http/__init__.py": "",
            "app/modules/billing/http/requests/__init__.py": "",
        },
    )

    violations = lint_imports(project)

    assert any(v.rule == "csr:outside-module" for v in violations)


def test_same_module_http_imports_clean(tmp_path):
    """A module's own routes/controller may import its own http layer freely."""
    project = make_project(
        tmp_path,
        {
            "app/modules/billing/__init__.py": "",
            "app/modules/billing/http/__init__.py": "",
            "app/modules/billing/http/controllers/__init__.py": "",
            "app/modules/billing/http/controllers/billing_controller.py": "",
            "app/modules/billing/http/requests/__init__.py": "",
            "app/modules/billing/http/requests/store_billing_request.py": "",
            "app/modules/billing/services/__init__.py": "",
            "app/modules/billing/routes.py": (
                "from app.modules.billing.http.controllers.billing_controller import BillingController\n"
            ),
        },
    )

    assert lint_imports(project) == []


def test_from_module_import_http_alias_flagged(tmp_path):
    """`from app.modules.billing import http` must not slip past the rule."""
    project = make_project(
        tmp_path,
        {
            "app/modules/billing/__init__.py": "",
            "app/modules/billing/http/__init__.py": "",
            "app/modules/accounts/__init__.py": "",
            "app/modules/accounts/services/__init__.py": "",
            "app/modules/accounts/services/account_service.py": (
                "from app.modules.billing import http\n"
            ),
        },
    )

    violations = lint_imports(project)

    assert any(v.rule == "module:private-layer" for v in violations)
