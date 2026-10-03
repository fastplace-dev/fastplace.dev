"""MCP config file writers: JSON merge, dotted keys, TOML sections, JSONC."""

from __future__ import annotations

import json

import pytest

from fastplace.mcp.install.writers import (
    json_server_config,
    opencode_server_config,
    write_json_config,
    write_toml_config,
)

SERVER = {"command": "/venv/bin/fastplace", "args": ["mcp", "start"]}


def test_new_json_file_created(tmp_path):
    path = tmp_path / ".mcp.json"

    write_json_config(path, "mcpServers", "fastplace-aibrain", SERVER)

    data = json.loads(path.read_text())
    assert data["mcpServers"]["fastplace-aibrain"] == SERVER


def test_existing_json_merged_without_clobbering(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {"other": {"command": "x"}},
                "unrelated": True,
            }
        )
    )

    write_json_config(path, "mcpServers", "fastplace-aibrain", SERVER)

    data = json.loads(path.read_text())
    assert data["unrelated"] is True
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["fastplace-aibrain"] == SERVER


def test_reinstall_replaces_same_server(tmp_path):
    path = tmp_path / ".mcp.json"
    write_json_config(path, "mcpServers", "fastplace-aibrain", {"command": "old"})
    write_json_config(path, "mcpServers", "fastplace-aibrain", SERVER)

    data = json.loads(path.read_text())
    assert data["mcpServers"]["fastplace-aibrain"] == SERVER
    assert len(data["mcpServers"]) == 1


def test_dotted_key_amp(tmp_path):
    path = tmp_path / ".amp/settings.json"

    write_json_config(path, "amp.mcpServers", "fastplace-aibrain", SERVER)

    data = json.loads(path.read_text())
    assert data["amp"]["mcpServers"]["fastplace-aibrain"] == SERVER


def test_empty_values_filtered(tmp_path):
    path = tmp_path / ".mcp.json"

    write_json_config(
        path,
        "mcpServers",
        "fastplace-aibrain",
        {"command": "x", "args": [], "env": {}},
    )

    data = json.loads(path.read_text())
    assert data["mcpServers"]["fastplace-aibrain"] == {"command": "x"}


def test_new_toml_file_created(tmp_path):
    path = tmp_path / ".codex/config.toml"

    write_toml_config(path, "mcp_servers", "fastplace-aibrain", SERVER)

    text = path.read_text()
    assert "[mcp_servers.fastplace-aibrain]" in text
    assert 'command = "/venv/bin/fastplace"' in text
    assert 'args = ["mcp", "start"]' in text


def test_toml_appends_preserving_existing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# user header\n[profiles.dev]\nmodel = "x"\n')

    write_toml_config(path, "mcp_servers", "fastplace-aibrain", SERVER)

    text = path.read_text()
    assert text.startswith("# user header\n")
    assert "[profiles.dev]" in text
    assert "[mcp_servers.fastplace-aibrain]" in text


def test_toml_reinstall_replaces_same_section(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_config(path, "mcp_servers", "fastplace-aibrain", {"command": "old"})
    write_toml_config(path, "mcp_servers", "fastplace-aibrain", SERVER)

    text = path.read_text()
    assert text.count("[mcp_servers.fastplace-aibrain]") == 1
    assert 'command = "old"' not in text
    assert 'command = "/venv/bin/fastplace"' in text


def test_toml_reinstall_keeps_other_sections(tmp_path):
    path = tmp_path / "config.toml"
    write_toml_config(path, "mcp_servers", "other-server", {"command": "y"})
    write_toml_config(path, "mcp_servers", "fastplace-aibrain", SERVER)

    text = path.read_text()
    assert "[mcp_servers.other-server]" in text
    assert "[mcp_servers.fastplace-aibrain]" in text


def test_opencode_server_shape():
    config = opencode_server_config(SERVER["command"], SERVER["args"])

    assert config == {
        "type": "local",
        "enabled": True,
        "command": ["/venv/bin/fastplace", "mcp", "start"],
        "environment": {},
    }
    assert json_server_config(SERVER["command"], SERVER["args"]) == {
        "command": "/venv/bin/fastplace",
        "args": ["mcp", "start"],
    }


def test_jsonc_comments_survive_as_structure(tmp_path):
    path = tmp_path / "opencode.jsonc"
    path.write_text('// top comment\n{\n  // which model\n  "model": "x",\n  "mcp": {}\n}\n')

    write_json_config(
        path,
        "mcp",
        "fastplace-aibrain",
        opencode_server_config("fp", ["mcp", "start"]),
        jsonc=True,
    )

    text = path.read_text()
    data = json.loads(text)
    assert data["model"] == "x"
    assert data["mcp"]["fastplace-aibrain"]["type"] == "local"


def test_invalid_json_raises(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("{ not json")

    with pytest.raises(ValueError):
        write_json_config(path, "mcpServers", "fastplace-aibrain", SERVER)


def test_empty_json_file_treated_as_new(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("")

    write_json_config(path, "mcpServers", "fastplace-aibrain", SERVER)

    assert json.loads(path.read_text())["mcpServers"]["fastplace-aibrain"] == SERVER
