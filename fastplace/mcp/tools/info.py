"""``application-info`` and ``get-absolute-url``."""

from __future__ import annotations

import json
import platform
from pathlib import Path

from fastplace.config import Config
from fastplace.mcp.context import McpContext
from fastplace.mcp.packages import scan_js_packages, scan_python_packages


def _app_settings(root: Path) -> dict:
    config = Config(root)
    return {
        "name": config.get("APP_NAME"),
        "url": config.get("APP_URL"),
        "debug": config.get("APP_DEBUG"),
        "environment": config.get("APP_ENV"),
    }


def _database_engine(ctx: McpContext) -> str:
    try:
        config = Config(ctx.root)
        url = str(config.get("DATABASE_URL") or "")
        if not url:
            return "unknown"
        from fastplace.mcp.safety.sql_readonly import detect_engine

        return detect_engine(url)
    except Exception:
        return "unknown"


def build_application_info(ctx: McpContext):
    """Version/config snapshot agents should read on each new chat."""

    async def application_info() -> str:
        import fastplace

        return json.dumps(
            {
                "python_version": platform.python_version(),
                "fastplace_version": fastplace.__version__,
                "database_engine": _database_engine(ctx),
                "app": _app_settings(ctx.root),
                "packages": {
                    "python": scan_python_packages(ctx.root),
                    "js": scan_js_packages(ctx.root),
                },
            },
            indent=2,
        )

    return application_info


def build_get_absolute_url(ctx: McpContext):
    """Join a relative path onto the configured app URL."""

    async def get_absolute_url(path: str = "") -> str:
        config = Config(ctx.root)
        base = str(config.get("APP_URL") or "").rstrip("/")
        if not base:
            raise ValueError("APP_URL is not configured; cannot build an absolute URL.")
        if not path:
            raise ValueError("Provide a relative path (e.g. '/dashboard').")
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}"

    return get_absolute_url
