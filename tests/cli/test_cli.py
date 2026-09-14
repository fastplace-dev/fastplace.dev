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


def test_make_agent_scaffolds_agent_and_tool_suite(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:agent", "ResearchAssistant"])
    assert result.exit_code == 0, result.output

    agent_path = tmp_path / "app" / "ai" / "agents" / "research_assistant_agent.py"
    tool_path = tmp_path / "app" / "ai" / "tools" / "research_assistant_tools.py"
    assert agent_path.exists(), "expected app/ai/agents/<snake>_agent.py"
    assert tool_path.exists(), "expected app/ai/tools/<snake>_tools.py"
    assert (tmp_path / "app" / "ai" / "vectors" / "__init__.py").exists()

    # The agent stub assembles a real Agent from fastplace.ai.
    agent_src = agent_path.read_text()
    assert "from fastplace.ai import Agent" in agent_src
    assert "def research_assistant_agent()" in agent_src

    # The tool stub is @Tool-decorated and registers through a real
    # package-path import — the same route app boot takes — not a detached
    # exec that would miss relative-import or package-context breakage.
    tool_src = tool_path.read_text()
    assert "@Tool(" in tool_src
    import importlib
    import sys

    from fastplace.ai import reset_tool_registry, tool_registry

    # complete the regular-package chain so the repo's own dogfood `app`
    # cannot shadow this namespace portion during the import
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "ai" / "__init__.py").write_text("")

    reset_tool_registry()
    saved = {n: m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")}
    for name in saved:
        sys.modules.pop(name)
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module("app.ai.tools.research_assistant_tools")
        assert "research_assistant_helper" in tool_registry
    finally:
        sys.path.remove(str(tmp_path))
        for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
            sys.modules.pop(name)
        sys.modules.update(saved)
        reset_tool_registry()


def test_make_agent_rejects_invalid_names(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["make:agent", "../evil"])
    assert result.exit_code == 1
    assert not (tmp_path / "app" / "ai" / "agents" / "..").exists()


def test_make_service_scaffolds_csr_service_stub(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:service", "Invoice", "--module", "billing"])
    assert result.exit_code == 0, result.output

    path = tmp_path / "app" / "modules" / "billing" / "services" / "invoice_service.py"
    assert path.exists(), "expected app/modules/<module>/services/<snake>_service.py"

    source = path.read_text()
    assert "class InvoiceService:" in source
    # The stub must be valid Python as written.
    compile(source, str(path), "exec")


def test_make_service_refuses_to_clobber(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "app" / "modules" / "billing" / "services" / "invoice_service.py"
    path.parent.mkdir(parents=True)
    path.write_text("# hand-written\n")

    result = runner.invoke(cli_app, ["make:service", "Invoice", "--module", "billing"])
    assert result.exit_code == 0, result.output
    assert path.read_text() == "# hand-written\n"


def test_make_repository_scaffolds_data_access_stub(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    runner = CliRunner()

    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:repository", "Invoice", "--module", "billing"])
    assert result.exit_code == 0, result.output

    path = tmp_path / "app" / "modules" / "billing" / "repositories" / "invoice_repository.py"
    assert path.exists(), "expected app/modules/<module>/repositories/<snake>_repository.py"

    source = path.read_text()
    assert "class InvoiceRepository:" in source
    # Repositories are the data-access layer: the stub talks to the ORM.
    assert "from fastplace.db import db" in source or "from fastplace.orm import" in source
    compile(source, str(path), "exec")
