"""``fastplace mcp install`` / ``update`` — wire fastplace-aibrain into AI agents.

For every requested (or auto-detected) agent this writes three surfaces:

- **MCP config** — the ``fastplace-aibrain`` stdio server entry in the
  agent's config file (merged, never clobbering other servers);
- **Guidelines** — the ``<fastplace-guidelines>`` block in ``AGENTS.md``;
- **Skills** — the five bundled skills under the agent's skills directory.

A ``.fastplace/mcp.json`` state file records what was installed per agent so
``fastplace mcp update`` can re-sync after a framework upgrade.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastplace import __version__
from fastplace.mcp.install.agents import AgentSpec, specs_for
from fastplace.mcp.install.guidelines import compose_guidelines, write_guidelines
from fastplace.mcp.install.skills import SKILLS, sync_skill
from fastplace.mcp.install.writers import (
    json_server_config,
    opencode_server_config,
    write_json_config,
    write_toml_config,
)

SERVER_KEY = "fastplace-aibrain"

Status = Literal["written", "updated", "unchanged"]


@dataclass(frozen=True)
class Row:
    agent: str
    surface: str
    status: Status


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    def add(self, agent: str, surface: str, status: Status) -> None:
        self.rows.append(Row(agent, surface, status))


# --- state -----------------------------------------------------------


def _state_path(root: Path) -> Path:
    return root / ".fastplace" / "mcp.json"


def read_state(root: Path) -> dict:
    path = _state_path(root)
    if not path.exists():
        return {"version": 1, "agents": {}}
    return json.loads(path.read_text())


def _write_state(root: Path, agents: dict[str, dict[str, bool]]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "installed_at": _dt.datetime.now(_dt.UTC).isoformat(),
                "fastplace_version": __version__,
                "agents": agents,
            },
            indent=2,
        )
        + "\n"
    )


# --- command resolution ----------------------------------------------


def resolve_fastplace_command() -> tuple[str, list[str]]:
    """The executable + args agent configs should launch.

    Prefers the absolute path of the running ``fastplace`` console script so
    the MCP server keeps working from any working directory and any shell
    without PATH assumptions.
    """
    candidate = Path(sys.argv[0]).resolve()
    if candidate.name == "fastplace" and candidate.exists():
        return str(candidate), ["mcp", "start"]
    which = shutil.which("fastplace")
    if which:
        return which, ["mcp", "start"]
    return "fastplace", ["mcp", "start"]


# --- install ---------------------------------------------------------


def _install_mcp(root: Path, spec: AgentSpec, report: Report, *, force: bool) -> bool:
    if spec.mcp_config_path is None:
        return False
    path = root / spec.mcp_config_path
    command, args = resolve_fastplace_command()
    payload = (
        opencode_server_config(command, args)
        if spec.server_config == "opencode"
        else json_server_config(command, args)
    )
    if spec.toml:
        write_toml_config(path, spec.mcp_config_key, SERVER_KEY, payload)
        report.add(spec.key, "mcp", "written")
        return True
    if not force and _json_server_matches(
        path, spec.mcp_config_key, SERVER_KEY, payload, spec.jsonc
    ):
        report.add(spec.key, "mcp", "unchanged")
        return True
    write_json_config(
        path,
        spec.mcp_config_key,
        SERVER_KEY,
        payload,
        jsonc=spec.jsonc,
        base=spec.default_config,
    )
    report.add(spec.key, "mcp", "written")
    return True


def _json_server_matches(path: Path, key: str, name: str, payload: dict, jsonc: bool) -> bool:
    if not path.exists():
        return False
    try:
        from fastplace.mcp.install.writers import _parse_json_or_jsonc

        config = _parse_json_or_jsonc(path.read_text())
        node: object = config
        for part in key.split("."):
            if not isinstance(node, dict):
                return False
            node = node.get(part)
        return isinstance(node, dict) and node.get(name) == payload
    except (ValueError, OSError):
        return False


def run_install(ctx, agents: list[str] | None = None, *, force: bool = False) -> Report:
    """Install MCP config + guidelines + skills for the requested agents."""
    root = ctx.root
    specs = specs_for(agents, project=root)
    report = Report()
    state_agents: dict[str, dict[str, bool]] = {}

    from fastplace.config import Config

    app_name = str(Config(root).get("APP_NAME", default="Fastplace"))

    for spec in specs:
        surfaces: dict[str, bool] = {}
        has_mcp = _install_mcp(root, spec, report, force=force)
        surfaces["mcp"] = has_mcp

        guidelines_path = root / (spec.guidelines_path or "AGENTS.md")
        guidelines = compose_guidelines(root=root, fastplace_version=__version__, app_name=app_name)
        if not force and guidelines_path.exists():
            existing = guidelines_path.read_text()
            if guidelines.strip() in existing:
                report.add(spec.key, "guidelines", "unchanged")
            else:
                write_guidelines(guidelines_path, guidelines)
                report.add(spec.key, "guidelines", "updated")
        else:
            write_guidelines(guidelines_path, guidelines)
            report.add(spec.key, "guidelines", "written")
        surfaces["guidelines"] = True

        skills_base = root / (spec.skills_path or ".fastplace/skills")
        skill_statuses = {sync_skill(skill, skills_base, overwrite=force) for skill in SKILLS}
        if "created" in skill_statuses:
            report.add(spec.key, "skills", "written")
        elif "updated" in skill_statuses:
            report.add(spec.key, "skills", "updated")
        else:
            report.add(spec.key, "skills", "unchanged")
        surfaces["skills"] = True

        state_agents[spec.key] = surfaces

    _write_state(root, state_agents)
    return report


def run_update(ctx) -> Report:
    """Re-sync every agent recorded in the state file (or detected now)."""
    root = ctx.root
    state = read_state(root)
    recorded = sorted(state.get("agents", {}))
    if not recorded:
        raise RuntimeError(
            "fastplace-aibrain has never been installed here — run `fastplace mcp install` first."
        )
    return run_install(ctx, agents=recorded, force=True)
