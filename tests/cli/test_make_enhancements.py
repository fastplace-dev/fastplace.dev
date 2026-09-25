"""Generator enhancements — make:controller --resource/--api and the
make:model companion flags -c/-s/-r/-a (spec E2, E3)."""

from __future__ import annotations

import pytest

# Autouse fixture: clean db/model/module state per test (see _isolation.py) —
# the make:model -a -m path runs migration autogenerate against app.* models.
from _isolation import isolate_project_state  # noqa: F401
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

runner = CliRunner()

_FULL_ACTIONS = ("index", "create", "store", "show", "edit", "update", "destroy")
_API_ACTIONS = ("index", "store", "show", "update", "destroy")


class TestMakeControllerVariants:
    def test_resource_writes_all_seven_crud_actions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli_app, ["make:controller", "ProductController", "--resource"])

        assert result.exit_code == 0, result.output
        path = tmp_path / "app" / "http" / "controllers" / "product_controller.py"
        assert path.is_file()
        source = path.read_text()
        for action in _FULL_ACTIONS:
            assert f"async def {action}(" in source, f"missing {action} action"
        # The stub must be valid Python as written.
        compile(source, str(path), "exec")

    def test_api_variant_omits_create_and_edit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli_app, ["make:controller", "ProductController", "--api"])

        assert result.exit_code == 0, result.output
        path = tmp_path / "app" / "http" / "controllers" / "product_controller.py"
        source = path.read_text()
        for action in _API_ACTIONS:
            assert f"async def {action}(" in source, f"missing {action} action"
        assert "async def create(" not in source
        assert "async def edit(" not in source

    def test_plain_controller_stays_minimal(self, tmp_path, monkeypatch):
        """No variant flag → the original single-index stub, unchanged."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli_app, ["make:controller", "ProductController"])

        assert result.exit_code == 0, result.output
        path = tmp_path / "app" / "http" / "controllers" / "product_controller.py"
        source = path.read_text()
        assert "async def index(" in source
        for action in ("create", "store", "show", "edit", "update", "destroy"):
            assert f"async def {action}(" not in source


class TestMakeModelCompanions:
    def test_all_with_migration_creates_five_files(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}")

        result = runner.invoke(cli_app, ["make:model", "Product", "-a", "-m"])

        assert result.exit_code == 0, result.output

        model = tmp_path / "app" / "modules" / "product" / "models" / "product.py"
        assert model.is_file()
        assert "class Product(Model):" in model.read_text()

        controller = tmp_path / "app" / "http" / "controllers" / "product_controller.py"
        assert controller.is_file()
        assert "class ProductController(Controller):" in controller.read_text()

        service = tmp_path / "app" / "modules" / "product" / "services" / "product_service.py"
        assert service.is_file()
        assert "class ProductService:" in service.read_text()

        repository = (
            tmp_path / "app" / "modules" / "product" / "repositories" / "product_repository.py"
        )
        assert repository.is_file()
        assert "class ProductRepository:" in repository.read_text()

        versions = list((tmp_path / "database" / "migrations" / "versions").glob("*.py"))
        assert len(versions) == 1, "--migration must still be respected alongside -a"
        assert "products" in versions[0].read_text()

    def test_all_without_migration_skips_versions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli_app, ["make:model", "Product", "-a"])

        assert result.exit_code == 0, result.output
        for rel in (
            "app/modules/product/models/product.py",
            "app/http/controllers/product_controller.py",
            "app/modules/product/services/product_service.py",
            "app/modules/product/repositories/product_repository.py",
        ):
            assert (tmp_path / rel).is_file(), f"missing {rel}"
        assert not (tmp_path / "database" / "migrations").exists()

    @pytest.mark.parametrize(
        ("flag", "companion_rel"),
        [
            pytest.param("-c", "app/http/controllers/product_controller.py", id="controller"),
            pytest.param("-s", "app/modules/product/services/product_service.py", id="service"),
            pytest.param(
                "-r", "app/modules/product/repositories/product_repository.py", id="repository"
            ),
        ],
    )
    def test_companion_flag_alone_creates_only_that_companion(
        self, tmp_path, monkeypatch, flag, companion_rel
    ):
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli_app, ["make:model", "Product", flag])

        assert result.exit_code == 0, result.output
        # The model itself is always created…
        assert (tmp_path / "app" / "modules" / "product" / "models" / "product.py").is_file()
        # …plus exactly the requested companion.
        assert (tmp_path / companion_rel).is_file(), f"missing {companion_rel}"
        companion_roots = {
            "-c": tmp_path / "app" / "http" / "controllers",
            "-s": tmp_path / "app" / "modules" / "product" / "services",
            "-r": tmp_path / "app" / "modules" / "product" / "repositories",
        }
        for other_flag, root in companion_roots.items():
            if other_flag != flag:
                assert not root.exists(), f"unexpected {other_flag} companion at {root}"
        assert not (tmp_path / "database").exists()

    def test_summary_line_echoes_derived_names(self, tmp_path, monkeypatch):
        """The derived module/table are echoed — a plural input (Projects)
        double-pluralizing into `projectses` is visible immediately."""
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli_app, ["make:model", "Project"])

        assert result.exit_code == 0, result.output
        assert "model Project" in result.output
        assert "module product" not in result.output
        assert "module project" in result.output
        assert "table projects" in result.output
