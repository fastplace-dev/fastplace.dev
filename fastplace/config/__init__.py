"""Configuration loading: .env files plus project ``config/*.py`` modules.

Project config modules (``config/app.py``, ``config/database.py``, ...) declare
plain defaults; environment variables always win, coerced to the default's
type. Access is declarative through the ``config`` helper::

    from fastplace.config import config
    name = config("APP_NAME")
    url = config("APP_URL", default="http://localhost:9000")
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_MISSING = object()

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def load_env(path: str | Path = ".env") -> None:
    """Load ``.env`` into the process environment (never overriding real env)."""
    load_dotenv(Path(path), override=False)


def _coerce(raw: str, base: Any) -> Any:
    """Coerce an environment string into the type of the config default."""
    if isinstance(base, bool):
        lowered = raw.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        return raw
    if isinstance(base, int) and not isinstance(base, bool):
        try:
            return int(raw)
        except ValueError:
            return raw
    if isinstance(base, float):
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


class Config:
    """Registry merging project config-module defaults with environment overrides."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.root = Path(project_root) if project_root else Path(os.getcwd())
        self._defaults: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        """Import every ``config/*.py`` module and collect its UPPER_CASE names."""
        config_dir = self.root / "config"
        if config_dir.is_dir():
            for file in sorted(config_dir.glob("*.py")):
                if file.name.startswith("_"):
                    continue
                module = self._import_module(file)
                namespace = file.stem
                for key, value in vars(module).items():
                    if not key.isupper():
                        continue
                    self._defaults[key] = value
                    self._defaults[f"{namespace}.{key}"] = value
        self._loaded = True

    @staticmethod
    def _import_module(file: Path):
        name = f"_fastplace_config_{file.stem}"
        spec = importlib.util.spec_from_file_location(name, file)
        if spec is None or spec.loader is None:  # pragma: no cover
            raise ImportError(f"Cannot import config module: {file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Resolve ``key`` — environment first, then config-module default."""
        if not self._loaded:
            self.load()
        if key in os.environ:
            return _coerce(os.environ[key], self._defaults.get(key))
        if key in self._defaults:
            return self._defaults[key]
        if default is not _MISSING:
            return default
        return None

    def refresh(self) -> None:
        """Drop cached defaults so the next ``get`` re-reads config modules."""
        self._defaults.clear()
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded


_default_config = Config()


def config(key: str, default: Any = _MISSING) -> Any:
    """Module-level accessor bound to the process working directory."""
    return _default_config.get(key, default)


def reset_config(project_root: str | Path | None = None) -> Config:
    """Rebind the default config — used by tests and the CLI."""
    global _default_config
    _default_config = Config(project_root)
    return _default_config
