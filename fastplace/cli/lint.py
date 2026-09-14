"""Project linting commands — module import boundaries."""

from __future__ import annotations

from pathlib import Path, PurePath

import typer

from fastplace.console import console

lint_app = typer.Typer(help="Static checks that keep the architecture honest.")


def _relevant_change(path: str) -> bool:
    """A save that can change module boundaries: a ``*.py`` file under
    ``app/`` or ``routes/``.

    Frontend assets, tests, and docs never move an import across a module
    seam — re-running the lint on those saves would be pure noise.
    """
    parts = PurePath(path).parts
    return path.endswith(".py") and ("app" in parts or "routes" in parts)


def _watch_dirs(root: Path) -> list[Path]:
    """Directories whose Python saves can move a module boundary.

    Watching the repo root would subscribe watchfiles to every directory
    under it (.venv, node_modules, .git — thousands of inotify handles on a
    fresh clone, until Linux starts killing watchers); ``lint_imports``
    reads only ``app/`` and ``routes/``, so subscribe to exactly those.
    Missing directories are skipped — ``watch()`` raises FileNotFoundError
    for paths that do not exist.
    """
    return [d for d in (root / "app", root / "routes") if d.is_dir()]


def lint_and_report(root: Path) -> int:
    """Run the boundary lint and print the report; returns the violation count."""
    from fastplace.modules import discover_modules, lint_imports

    violations = lint_imports(root)
    if not violations:
        modules = discover_modules(root)
        names = ", ".join(sorted(modules)) or "none found"
        console.print(
            f"[green]✓[/] module boundaries clean — {len(modules)} module(s) checked: {names}"
        )
        return 0

    for v in violations:
        console.print(f"[cyan]{v.file}:{v.line}[/] [yellow]{v.rule}[/] {v.import_target}")
    console.print(
        f"\n[red]✗ {len(violations)} violation(s)[/] — cross-module access goes "
        "through services; models and repositories stay module-private."
    )
    return len(violations)


@lint_app.command("lint:modules")
def lint_modules() -> None:
    """Enforce module import boundaries (CSR: models/repositories are private)."""
    violations = lint_and_report(Path.cwd())
    if violations:
        raise typer.Exit(code=1)


@lint_app.command("lint:watch")
def lint_watch(
    once: bool = typer.Option(
        False, "--once", help="Run a single lint pass and exit (no watching)."
    ),
) -> None:
    """Re-run the module boundary lint on every app/ or routes/ save.

    Prints the report on startup and again after each relevant save; keeps
    watching (and never exits on violations) so `fastplace run dev` keeps
    its feedback loop alive. Ctrl-C stops it.
    """
    root = Path.cwd()

    if lint_and_report(root) and once:
        raise typer.Exit(code=1)
    if once:
        return

    watch_dirs = _watch_dirs(root)
    if not watch_dirs:
        console.print("[yellow]! nothing to watch — no app/ or routes/ found[/]")
        return

    from watchfiles import watch

    console.print("[dim]lint: watching app/ and routes/ for saves (Ctrl-C to stop)…[/]")
    try:
        for _changes in watch(
            *watch_dirs, watch_filter=lambda _change, path: _relevant_change(str(path))
        ):
            lint_and_report(root)
    except KeyboardInterrupt:
        pass
