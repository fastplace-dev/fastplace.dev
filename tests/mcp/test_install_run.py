"""run_install / run_update orchestration + the .fastplace/mcp.json state file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastplace.mcp.config import McpConfig
from fastplace.mcp.context import McpContext
from fastplace.mcp.install import read_state, run_install, run_update


@pytest.fixture()
def project(tmp_path: Path, monkeypatch) -> McpContext:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    monkeypatch.chdir(tmp_path)
    return McpContext(root=tmp_path, config=McpConfig())


def test_install_detected_agent_writes_all_surfaces(project: McpContext):
    (project.root / ".claude").mkdir()

    report = run_install(project)

    keys = {(row.agent, row.surface) for row in report.rows}
    assert ("claude_code", "mcp") in keys
    assert ("claude_code", "guidelines") in keys
    assert ("claude_code", "skills") in keys

    mcp = json.loads((project.root / ".mcp.json").read_text())
    assert mcp["mcpServers"]["fastplace-aibrain"]["args"] == ["mcp", "start"]

    agents_md = (project.root / "AGENTS.md").read_text()
    assert "<fastplace-guidelines>" in agents_md

    skill = project.root / ".claude/skills/infer-conventions/SKILL.md"
    assert skill.exists()

    state = read_state(project.root)
    assert state["agents"]["claude_code"] == {
        "mcp": True,
        "guidelines": True,
        "skills": True,
    }


def test_install_writes_state_file(project: McpContext):
    (project.root / ".claude").mkdir()
    run_install(project)

    state = json.loads((project.root / ".fastplace/mcp.json").read_text())
    assert state["version"] == 1
    assert state["fastplace_version"]
    assert state["installed_at"]


def test_install_skips_mcp_for_pi(project: McpContext):
    run_install(project, agents=["pi"])

    assert not (project.root / ".mcp.json").exists()
    assert (project.root / "AGENTS.md").exists()
    assert (project.root / ".pi/skills/testing-best-practices/SKILL.md").exists()
    state = read_state(project.root)
    assert state["agents"]["pi"] == {"mcp": False, "guidelines": True, "skills": True}


def test_install_unknown_agent_raises(project: McpContext):
    with pytest.raises(KeyError):
        run_install(project, agents=["nope"])


def test_install_idempotent_reports_unchanged(project: McpContext):
    (project.root / ".claude").mkdir()
    run_install(project)
    second = run_install(project)

    statuses = {row.status for row in second.rows}
    assert statuses == {"unchanged"}


def test_install_force_rewrites(project: McpContext):
    (project.root / ".claude").mkdir()
    run_install(project)
    forced = run_install(project, force=True)

    # force never reports unchanged — everything is rewritten
    assert {row.status for row in forced.rows} <= {"written", "updated"}


def test_opencode_install_shape(project: McpContext):
    run_install(project, agents=["opencode"])

    config = json.loads((project.root / "opencode.jsonc").read_text())
    server = config["mcp"]["fastplace-aibrain"]
    assert server["type"] == "local"
    assert server["command"][-2:] == ["mcp", "start"]
    assert config["$schema"] == "https://opencode.ai/config.json"


def test_codex_toml_install(project: McpContext):
    run_install(project, agents=["codex"])

    text = (project.root / ".codex/config.toml").read_text()
    assert "[mcp_servers.fastplace-aibrain]" in text


def test_update_resyncs_from_state(project: McpContext):
    (project.root / ".claude").mkdir()
    run_install(project)
    (project.root / "AGENTS.md").unlink()

    report = run_update(project)

    assert (project.root / "AGENTS.md").exists()
    assert any(row.surface == "guidelines" for row in report.rows)


def test_update_without_state_errors(project: McpContext):
    with pytest.raises(RuntimeError, match="never been installed"):
        run_update(project)


def test_install_preserves_other_servers(project: McpContext):
    (project.root / ".claude").mkdir()
    mcp_path = project.root / ".mcp.json"
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))

    run_install(project)

    data = json.loads(mcp_path.read_text())
    assert data["mcpServers"]["other"] == {"command": "x"}
