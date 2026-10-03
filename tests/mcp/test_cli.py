"""``fastplace mcp`` CLI: start (stdio) with gates, and --check."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.mcp.cli import mcp_app


@pytest.fixture()
def project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_start_refuses_when_disabled(project, monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_ENABLED", "0")
    result = CliRunner().invoke(mcp_app, ["start"])

    assert result.exit_code == 1
    assert "disabled" in result.output.lower()


def test_start_check_reports_tools(project, monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_TINKER", "1")
    result = CliRunner().invoke(mcp_app, ["start", "--check"])

    assert result.exit_code == 0
    assert "fastplace-aibrain" in result.output
    assert "application-info" in result.output
    assert "tinker" in result.output


def test_start_check_respects_exclude(project, monkeypatch):
    monkeypatch.setenv("FASTPLACE_MCP_TOOLS_EXCLUDE", "search-docs")
    result = CliRunner().invoke(mcp_app, ["start", "--check"])

    assert result.exit_code == 0
    assert "search-docs" not in result.output


def test_install_detected_agent(tmp_path, monkeypatch):
    (tmp_path / ".cursor").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTPLACE_MCP_ENABLED", "1")

    result = CliRunner().invoke(mcp_app, ["install"])

    assert result.exit_code == 0, result.output
    assert "cursor" in result.output.lower()
    assert (tmp_path / ".cursor/mcp.json").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / ".cursor/skills/testing-best-practices/SKILL.md").exists()
    assert (tmp_path / ".fastplace/mcp.json").exists()


def test_install_unknown_agent_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTPLACE_MCP_ENABLED", "1")

    result = CliRunner().invoke(mcp_app, ["install", "nope"])

    assert result.exit_code != 0


def test_update_without_install_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FASTPLACE_MCP_ENABLED", "1")

    result = CliRunner().invoke(mcp_app, ["update"])

    assert result.exit_code != 0
    assert "install" in result.output.lower()
