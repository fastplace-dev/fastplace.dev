"""`make:module` — full vertical slice scaffold.

Default creates the module directories plus a Model, Repository and Service
stub wired by naming convention (Controllers → Services → Repositories →
Models); `--bare` keeps the old packages-only behavior, `--resource`/`--api`
add a controller, `-m` a migration.
"""

from __future__ import annotations

# Autouse: clean db/model/module state per test (see _isolation.py) — the
# -m path runs migration autogenerate against app.* models.
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()

_MARKERS = (
    "__init__.py",
    "models/__init__.py",
    "repositories/__init__.py",
    "services/__init__.py",
)


def test_full_slice_default(tmp_path, monkeypatch):
    """`make:module OrderModule` scaffolds dirs + Model + Repository + Service."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "OrderModule"])

    assert result.exit_code == 0, result.output
    base = tmp_path / "app" / "modules" / "order"
    for rel in _MARKERS:
        assert (base / rel).is_file(), rel
    model = base / "models" / "order.py"
    assert model.is_file()
    source = model.read_text()
    assert "class Order(Model):" in source
    assert '__tablename__ = "orders"' in source
    compile(source, str(model), "exec")
    repository = base / "repositories" / "order_repository.py"
    assert repository.is_file()
    assert "class OrderRepository:" in repository.read_text()
    service = base / "services" / "order_service.py"
    assert service.is_file()
    assert "class OrderService:" in service.read_text()


def test_bare_flag_creates_packages_only(tmp_path, monkeypatch):
    """`--bare` restores the old behavior: markers, no slice stubs."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "--bare"])

    assert result.exit_code == 0, result.output
    base = tmp_path / "app" / "modules" / "order"
    for rel in _MARKERS:
        assert (base / rel).is_file(), rel
    assert not (base / "models" / "order.py").exists()
    assert not (base / "repositories" / "order_repository.py").exists()
    assert not (base / "services" / "order_service.py").exists()


def test_module_suffix_stripped_before_snake(tmp_path, monkeypatch):
    """OrderModule → `order`, not `order_module` (shipped modules are plain nouns)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "BillingCenterModule"])

    assert result.exit_code == 0, result.output
    base = tmp_path / "app" / "modules" / "billing_center"
    model = base / "models" / "billing_center.py"
    assert model.is_file()
    assert "class BillingCenter(Model):" in model.read_text()
    assert (base / "services" / "billing_center_service.py").is_file()
    assert (base / "repositories" / "billing_center_repository.py").is_file()
    assert not (tmp_path / "app" / "modules" / "billing_center_module").exists()


def test_plain_snake_name_unchanged(tmp_path, monkeypatch):
    """A name without the Module suffix passes through as before."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "inventory"])

    assert result.exit_code == 0, result.output
    base = tmp_path / "app" / "modules" / "inventory"
    assert "class Inventory(Model):" in (base / "models" / "inventory.py").read_text()
    assert (base / "services" / "inventory_service.py").is_file()
    assert (base / "repositories" / "inventory_repository.py").is_file()


def test_module_only_name_rejected(tmp_path, monkeypatch):
    """A bare `Module` name strips to nothing — refuse instead of scaffolding it."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "Module"])

    assert result.exit_code == 1
    assert not (tmp_path / "app" / "modules" / "module").exists()


def test_api_flag_also_scaffolds_controller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "--api"])

    assert result.exit_code == 0, result.output
    controller = tmp_path / "app" / "http" / "controllers" / "order_controller.py"
    assert controller.is_file()
    source = controller.read_text()
    assert "class OrderController(Controller):" in source
    assert "async def store(" in source
    assert "async def create(" not in source  # api drops the form actions


def test_resource_flag_scaffolds_full_controller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "--resource"])

    assert result.exit_code == 0, result.output
    source = (tmp_path / "app" / "http" / "controllers" / "order_controller.py").read_text()
    for action in ("index", "create", "store", "show", "edit", "update", "destroy"):
        assert f"async def {action}(" in source


def test_migration_flag_creates_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'mod.sqlite3'}")

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "-m"])

    assert result.exit_code == 0, result.output
    versions = list((tmp_path / "database" / "migrations" / "versions").glob("*.py"))
    assert len(versions) == 1
    assert "orders" in versions[0].read_text()
