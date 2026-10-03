"""MCP config file writers — JSON (with dotted keys and JSONC input) and TOML.

JSON writers merge into existing configs without clobbering unknown keys or
other agents' servers; empty values are dropped so installed payloads stay
minimal. TOML is written textually (append a ``[key.name]`` section, replace
an existing one) — Python 3.12's ``tomllib`` is read-only, and textual
editing preserves everything the user already has byte-for-byte except the
one section we own. JSONC input is parsed after stripping comments and
written back as strict JSON (valid for every ``.jsonc`` consumer).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

JsonDict = dict[str, Any]


def _clean(payload: JsonDict) -> JsonDict:
    """Drop empty lists/dicts/None values (Boost parity: minimal payloads)."""
    return {key: value for key, value in payload.items() if value not in ([], {}, None, "")}


def json_server_config(command: str, args: list[str]) -> JsonDict:
    return _clean({"command": command, "args": list(args), "env": {}})


def opencode_server_config(command: str, args: list[str]) -> JsonDict:
    return {
        "type": "local",
        "enabled": True,
        "command": [command, *args],
        "environment": {},
    }


_JSONC_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_JSONC_LINE_COMMENT = re.compile(r"(^|(?<=\s))//[^\n]*")


def _strip_jsonc_comments(text: str) -> str:
    # A cheaper, correct-enough pass for config files: drop block comments
    # first, then line comments not inside a URL-like "://".
    without_blocks = _JSONC_BLOCK_COMMENT.sub("", text)
    lines = []
    for line in without_blocks.splitlines():
        if "//" in line:
            scheme_index = line.find("://")
            comment_index = line.find("//")
            if comment_index != -1 and (scheme_index == -1 or comment_index < scheme_index):
                line = line[:comment_index]
        lines.append(line)
    return "\n".join(lines)


def _parse_json_or_jsonc(text: str) -> JsonDict:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = json.loads(_strip_jsonc_comments(text))
    if not isinstance(parsed, dict):
        raise ValueError("MCP config root must be a JSON object")
    return parsed


def _store_dotted(config: JsonDict, dotted_key: str, name: str, payload: JsonDict) -> None:
    node = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    leaf = node.get(parts[-1])
    if not isinstance(leaf, dict):
        leaf = {}
        node[parts[-1]] = leaf
    leaf[name] = payload


def write_json_config(
    path: Path,
    key: str,
    name: str,
    server: JsonDict,
    *,
    jsonc: bool = False,
    base: tuple[tuple[str, Any], ...] = (),
) -> None:
    """Create or merge ``{key}.{name}`` into a JSON/JSONC config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    config = (
        _parse_json_or_jsonc(path.read_text()) if path.exists() and path.read_text().strip() else {}
    )
    for base_key, base_value in base:
        config.setdefault(base_key, base_value)
    _store_dotted(config, key, name, _clean(dict(server)))
    path.write_text(json.dumps(config, indent=2) + "\n")


def _toml_inline(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_inline(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def write_toml_config(
    path: Path,
    key: str,
    name: str,
    server: JsonDict,
) -> None:
    """Create or replace the ``[key.name]`` section in a TOML config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""

    section = f"[{key}.{name}]"
    lines = [section]
    for field_name, value in _clean(dict(server)).items():
        lines.append(f"{field_name} = {_toml_inline(value)}")
    block = "\n".join(lines)

    if section not in existing:
        trimmed = existing.rstrip("\n")
        separator = "\n\n" if trimmed else ""
        path.write_text(f"{trimmed}{separator}{block}\n")
        return

    # Replace the whole section: from its header up to the next header/EOF.
    pattern = re.compile(
        rf"^\[{re.escape(key)}\.{re.escape(name)}\]\s*$.*?(?=^\[|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    path.write_text(pattern.sub(block + "\n", existing))
