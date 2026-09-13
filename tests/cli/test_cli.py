"""CLI smoke tests — command wiring via Typer's CliRunner."""

from fastplace.cli import app as cli_app


def test_about_runs(monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("APP_NAME", "TestApp")
    runner = CliRunner()
    result = runner.invoke(cli_app, ["about"])
    assert result.exit_code == 0
    assert "Fastplace" in result.output


def test_run_dev_help_lists_options():
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["run", "dev", "--help"])
    assert result.exit_code == 0
    assert "--skip-vite" in result.output


def test_serve_help_lists_options():
    from typer.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--skip-build" in result.output


def test_make_page_scaffolds_react_component(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "resources" / "js" / "pages").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:page", "Projects/Index"])
    assert result.exit_code == 0, result.output

    page = tmp_path / "resources" / "js" / "pages" / "Projects" / "Index.jsx"
    assert page.exists()
    source = page.read_text()
    assert "usePage" in source
    assert "Projects/Index" in source


def test_make_page_refuses_to_clobber(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    page = tmp_path / "resources" / "js" / "pages" / "Projects" / "Index.jsx"
    page.parent.mkdir(parents=True)
    page.write_text("// hand-written\n")

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:page", "Projects/Index"])
    assert result.exit_code == 0
    assert page.read_text() == "// hand-written\n"
    assert "exists" in result.output


def test_make_page_generates_valid_identifier_for_hyphenated_names(tmp_path, monkeypatch):
    import re

    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "resources" / "js" / "pages").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:page", "My-Settings"])
    assert result.exit_code == 0, result.output

    page = tmp_path / "resources" / "js" / "pages" / "My-Settings.jsx"
    assert page.exists()
    source = page.read_text()
    # Component name keeps the hyphen (resolver matches file path)…
    assert 'component="My-Settings"' in source
    # …but the JS function identifier must be a valid identifier.
    match = re.search(r"export default function (\w+)\(", source)
    assert match is not None
    assert match.group(1) == "MySettings"


def test_make_page_rejects_dotted_names(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "resources" / "js" / "pages").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:page", "Blog.Posts"])
    assert result.exit_code == 1
    assert not (tmp_path / "resources" / "js" / "pages" / "Blog.jsx").exists()


def test_make_controller_stub_is_runnable_and_uses_convention_filename(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:controller", "HealthCheck"])
    assert result.exit_code == 0, result.output

    path = tmp_path / "app" / "http" / "controllers" / "health_check_controller.py"
    assert path.exists(), "expected snake_case <name>_controller.py convention"

    # The stub must return a valid response — Json(content) takes a payload,
    # not keyword arguments.
    source = path.read_text()
    assert "Json(" in source
    assert "items=" not in source
    namespace: dict = {}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - stub sanity
    assert callable(namespace["HealthCheck"])


def test_lint_modules_reports_clean_tree_with_exit_zero(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    services = tmp_path / "app" / "modules" / "billing" / "services"
    services.mkdir(parents=True)
    (tmp_path / "app" / "modules" / "billing" / "__init__.py").write_text("")
    (services / "s.py").write_text("from ..models import Invoice\n")

    runner = CliRunner()
    result = runner.invoke(cli_app, ["lint:modules"])
    assert result.exit_code == 0, result.output
    assert "billing" in result.output


def test_lint_modules_exits_one_with_rich_report_on_violations(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "app" / "http" / "controllers").mkdir(parents=True)
    (tmp_path / "app" / "http" / "controllers" / "bad_controller.py").write_text(
        "from app.modules.billing.repositories.invoice_repository import InvoiceRepository\n"
    )

    runner = CliRunner()
    result = runner.invoke(cli_app, ["lint:modules"])
    assert result.exit_code == 1
    assert "bad_controller.py" in result.output
    assert "invoice_repository" in result.output


def test_make_module_scaffolds_csr_layout(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:module", "Knowledge"])
    assert result.exit_code == 0, result.output

    base = tmp_path / "app" / "modules" / "knowledge"
    for rel in (
        "__init__.py",
        "models/__init__.py",
        "repositories/__init__.py",
        "services/__init__.py",
    ):
        assert (base / rel).exists(), rel


def test_make_module_rejects_invalid_names(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:module", "../evil"])
    assert result.exit_code == 1
    assert not (tmp_path / "app" / "modules" / "..").exists()
