"""Project linting commands — module import boundaries."""

from __future__ import annotations

from pathlib import Path

import typer

from fastplace.console import console

lint_app = typer.Typer(help="Static checks that keep the architecture honest.")


@lint_app.command("lint:modules")
def lint_modules() -> None:
    """Enforce module import boundaries (CSR: models/repositories are private)."""
    from fastplace.modules import discover_modules, lint_imports

    root = Path.cwd()
    violations = lint_imports(root)
    modules = discover_modules(root)

    if not violations:
        names = ", ".join(sorted(modules)) or "none found"
        console.print(
            f"[green]✓[/] module boundaries clean — {len(modules)} module(s) checked: {names}"
        )
        return

    for v in violations:
        console.print(f"[cyan]{v.file}:{v.line}[/] [yellow]{v.rule}[/] {v.import_target}")
    console.print(
        f"\n[red]✗ {len(violations)} violation(s)[/] — cross-module access goes "
        "through services; models and repositories stay module-private."
    )
    raise typer.Exit(code=1)
