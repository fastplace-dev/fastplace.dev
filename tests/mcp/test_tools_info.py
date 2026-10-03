"""application-info and get-absolute-url tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.tools.info import build_application_info, build_get_absolute_url


@pytest.fixture()
def project(tmp_path: Path, monkeypatch) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.py").write_text(
        'APP_NAME = "Demo"\nAPP_URL = "https://demo.test"\nAPP_DEBUG = True\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastplace>=0.4"]\n'
    )
    # Config.get resolves the process environment first; some CLI tests call
    # load_env() at the repo root and .env values outlive them in-process.
    for var in ("APP_NAME", "APP_URL", "APP_DEBUG"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture()
def ctx(project: Path) -> McpContext:
    return McpContext(root=project, config=McpConfig())


async def test_application_info_reports_versions_and_config(ctx, project):
    handle = build_application_info(ctx)
    info = json.loads(await handle())

    assert info["python_version"].startswith(f"{sys.version_info.major}.{sys.version_info.minor}")
    assert info["fastplace_version"].count(".") == 2
    assert info["app"]["name"] == "Demo"
    assert info["app"]["url"] == "https://demo.test"
    assert info["app"]["debug"] is True
    assert info["packages"]["python"]["fastplace"].count(".") == 2


async def test_application_info_without_config_dir(tmp_path):
    ctx = McpContext(root=tmp_path, config=McpConfig())
    handle = build_application_info(ctx)
    info = json.loads(await handle())

    # A missing config dir must not crash the tool — defaults stand in.
    assert "fastplace_version" in info
    assert info["app"]["name"] is None or isinstance(info["app"]["name"], str)


async def test_get_absolute_url_joins_paths(ctx):
    handle = build_get_absolute_url(ctx)

    assert await handle(path="/dashboard") == "https://demo.test/dashboard"
    assert await handle(path="/") == "https://demo.test/"
    assert await handle(path="items/5") == "https://demo.test/items/5"


async def test_get_absolute_url_rejects_empty(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.py").write_text('APP_URL = "https://demo.test"\n')
    ctx = McpContext(root=tmp_path, config=McpConfig())

    handle = build_get_absolute_url(ctx)
    with pytest.raises(ValueError, match="path"):
        await handle(path="")
