"""Module system — bounded-module discovery + import-boundary linting.

Fastplace is a modular monolith: feature code lives in bounded modules under
``app/modules/<name>`` with a fixed CSR layout (``models/``,
``repositories/``, ``services/``). The seam between modules is the *service*
layer — anything deeper (models, repositories) is private. :func:`lint_imports`
enforces that boundary statically over the AST, and `fastplace lint:modules`
reports it (exit 1 on violation) so CI keeps modules honest.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

#: Layers that are private to their module — the service seam is public.
_PRIVATE_LAYERS = frozenset({"models", "repositories"})


@dataclass(frozen=True)
class ModuleInfo:
    """One bounded module under ``app/modules``."""

    name: str
    path: Path

    def dotted(self, layer: str | None = None) -> str:
        base = f"app.modules.{self.name}"
        return f"{base}.{layer}" if layer else base


@dataclass(frozen=True)
class ImportViolation:
    """One import that crosses a module boundary."""

    file: Path
    line: int
    import_target: str
    rule: str
    message: str


def discover_modules(root: str | Path) -> dict[str, ModuleInfo]:
    """Map module names to their packages under ``app/modules/``."""
    modules_dir = Path(root) / "app" / "modules"
    if not modules_dir.is_dir():
        return {}
    found: dict[str, ModuleInfo] = {}
    for entry in sorted(modules_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith(("_", ".")):
            found[entry.name] = ModuleInfo(entry.name, entry)
    return found


def lint_imports(root: str | Path) -> list[ImportViolation]:
    """AST-walk the project (``app/`` + ``routes/``) and report cross-boundary imports."""
    root = Path(root)
    sources = [d for d in (root / "app", root / "routes") if d.is_dir()]
    if not sources:
        return []

    violations: list[ImportViolation] = []
    for source in sources:
        for path in sorted(source.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            package = _package_of(root, path)
            is_package = path.name == "__init__.py"
            try:
                # Parse from bytes so PEP 263 coding cookies are honored.
                tree = ast.parse(path.read_bytes(), filename=str(path))
            except (SyntaxError, ValueError, OSError):
                continue  # a broken file is the interpreter's/IDE's complaint, not ours
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for target in _import_targets(node, package, is_package=is_package):
                        violation = _check(root, path, node.lineno, target, package)
                        if violation is not None:
                            violations.append(violation)
                elif isinstance(node, ast.Call):
                    dynamic = _dynamic_import_target(node)
                    if dynamic is not None:
                        violation = _check(root, path, node.lineno, dynamic, package)
                        if violation is not None:
                            violations.append(violation)
    return violations


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _package_of(root: Path, path: Path) -> str:
    """Dotted package of a file (``app/modules/billing/services/x.py`` →
    ``app.modules.billing.services.x``; ``__init__.py`` is the package itself)."""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _import_targets(node: ast.AST, package: str, *, is_package: bool = False) -> list[str]:
    """Absolute dotted targets imported by an Import/ImportFrom node."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []

    base = _resolve_relative(package, node.level, is_package=is_package)
    if base is None:
        return []  # relative import escaping the package tree — skip silently
    if base and node.module:
        module = f"{base}.{node.module}"
    else:
        module = base or (node.module or "")

    targets = [module]
    # `from app.modules.billing import models` — the alias names a private
    # layer package; treat it as importing that package.
    prefix = module + "." if module else ""
    star = any(alias.name == "*" for alias in node.names)
    for alias in node.names:
        candidate = f"{prefix}{alias.name}"
        parts = candidate.split(".")
        if len(parts) == 4 and parts[:2] == ["app", "modules"] and parts[3] in _PRIVATE_LAYERS:
            targets.append(candidate)
    if star:
        # `from app.modules.billing import *` re-exports the private layers —
        # flag them as if imported directly.
        parts = module.split(".")
        if len(parts) == 3 and parts[:2] == ["app", "modules"]:
            targets.extend(f"{module}.{layer}" for layer in sorted(_PRIVATE_LAYERS))
    return targets


def _resolve_relative(package: str, level: int, *, is_package: bool = False) -> str | None:
    """Base package for a relative import of the given level (0 = absolute).

    For a plain module, level N drops N components (the filename is the
    first hop). For a package's ``__init__`` the "module" is the package
    itself, so level 1 already lands on the package and each further level
    drops one more — mirroring Python's own relative-import semantics.
    """
    if level == 0:
        return ""
    drop = level - 1 if is_package else level
    parts = package.split(".")
    if drop >= len(parts):
        return None  # climbs past the project root — an invalid import anyway
    return ".".join(parts[: len(parts) - drop])


def _dynamic_import_target(node: ast.Call) -> str | None:
    """Literal target of ``importlib.import_module("…")`` / ``__import__("…")``."""
    func = node.func
    name: str | None = None
    if isinstance(func, ast.Attribute):  # importlib.import_module(...)
        if func.attr == "import_module":
            value = func.value
            if isinstance(value, ast.Name) and value.id == "importlib":
                name = "import_module"
    elif isinstance(func, ast.Name):  # import_module(...) or __import__(...)
        if func.id in ("import_module", "__import__"):
            name = func.id
    if name is None or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None  # computed targets are out of scope for static linting


def _check(root: Path, file: Path, line: int, target: str, package: str) -> ImportViolation | None:
    parts = target.split(".")
    if parts[:2] != ["app", "modules"] or len(parts) < 4:
        return None  # framework imports, stdlib, app.models shared base — fine
    target_module, layer = parts[2], parts[3]
    if layer not in _PRIVATE_LAYERS:
        return None  # the service seam (or the module root) is public

    pkg_parts = package.split(".")
    own = pkg_parts[2] if pkg_parts[:2] == ["app", "modules"] else None
    if own == target_module:
        return None  # same module — models/repositories stay importable inside

    rel_file = file.relative_to(root)
    if own is None:
        return ImportViolation(
            file=rel_file,
            line=line,
            import_target=target,
            rule="csr:outside-module",
            message=(
                f"{rel_file} is outside module '{target_module}' — models and "
                "repositories are module-private; go through its services."
            ),
        )
    return ImportViolation(
        file=rel_file,
        line=line,
        import_target=target,
        rule="module:private-layer",
        message=(
            f"module '{own}' imports module '{target_module}' {layer} — cross-module "
            "access must go through services."
        ),
    )
