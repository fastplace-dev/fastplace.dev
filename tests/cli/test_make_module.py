"""`make:module` — self-contained module scaffold.

Default creates the module directories plus a Model, Repository, Service
and the module's own http edge (controller, store request, route table)
wired by naming convention (Controllers → Services → Repositories →
Models); `--api`/`--resource` widen the module-local controller to CRUD,
`--bare` keeps the old packages-only behavior, `-m` a migration.
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


def test_full_module_default(tmp_path, monkeypatch):
    """Default scaffolds the complete self-contained module, incl. routes."""
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
    assert "class OrderRepository:" in (base / "repositories" / "order_repository.py").read_text()
    assert "class OrderService:" in (base / "services" / "order_service.py").read_text()
    # Module-local http edge.
    controller = base / "http" / "controllers" / "order_controller.py"
    assert controller.is_file()
    csource = controller.read_text()
    assert "class OrderController(Controller):" in csource
    assert "service = OrderService()" in csource
    compile(csource, str(controller), "exec")
    request = base / "http" / "requests" / "store_order_request.py"
    assert request.is_file()
    rsource = request.read_text()
    assert "class StoreOrderRequest(BaseModel):" in rsource
    compile(rsource, str(request), "exec")
    # Route table wired to the controller.
    routes = base / "routes.py"
    assert routes.is_file()
    vsource = routes.read_text()
    assert "web_routes: Router | None = None" in vsource
    assert "api_routes = Router()" in vsource
    assert 'api_routes.get("/orders", OrderController, "index"' in vsource
    compile(vsource, str(routes), "exec")
    # No central leak.
    assert not (tmp_path / "app" / "http" / "controllers" / "order_controller.py").exists()
    for marker in ("http/__init__.py", "http/controllers/__init__.py", "http/requests/__init__.py"):
        assert (base / marker).is_file(), marker


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
    assert not (base / "routes.py").exists()
    assert not (base / "http" / "controllers" / "order_controller.py").exists()


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


def test_api_flag_scaffolds_module_crud(tmp_path, monkeypatch):
    """--api: CRUD controller + routes, module-local, no form actions."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "--api"])

    assert result.exit_code == 0, result.output
    base = tmp_path / "app" / "modules" / "order"
    source = (base / "http" / "controllers" / "order_controller.py").read_text()
    for action in ("index", "store", "show", "update", "destroy"):
        assert f"async def {action}(" in source
    assert "async def create(" not in source
    assert "async def edit(" not in source
    assert "data = await request.validate(StoreOrderRequest)" in source
    routes = (base / "routes.py").read_text()
    assert 'api_routes.post("/orders", OrderController, "store"' in routes
    assert 'api_routes.get("/orders/{id}", OrderController, "show"' in routes
    assert 'api_routes.put("/orders/{id}", OrderController, "update"' in routes
    assert 'api_routes.delete("/orders/{id}", OrderController, "destroy"' in routes
    compile(routes, str(base / "routes.py"), "exec")
    assert not (tmp_path / "app" / "http" / "controllers" / "order_controller.py").exists()


def test_resource_flag_scaffolds_full_module_crud(tmp_path, monkeypatch):
    """--resource: the seven-action controller inside the module."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "--resource"])

    assert result.exit_code == 0, result.output
    source = (
        tmp_path / "app" / "modules" / "order" / "http" / "controllers" / "order_controller.py"
    ).read_text()
    for action in ("index", "create", "store", "show", "edit", "update", "destroy"):
        assert f"async def {action}(" in source
    compile(source, "order_controller.py", "exec")


def test_migration_flag_creates_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'mod.sqlite3'}")

    result = runner.invoke(cli_app, ["make:module", "OrderModule", "-m"])

    assert result.exit_code == 0, result.output
    versions = list((tmp_path / "database" / "migrations" / "versions").glob("*.py"))
    assert len(versions) == 1
    assert "orders" in versions[0].read_text()
