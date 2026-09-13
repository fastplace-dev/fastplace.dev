"""Vite asset resolution for the bridge HTML shell.

Development mode points at the Vite dev server (HMR); production resolves
hashed assets from ``public/build/manifest.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

_DEV_ENVS = {"local", "dev", "development"}


def asset_tags(project_root: str | Path, *, vite_dev_url: str | None, app_env: str) -> str:
    """Return <script>/<link> tags for the app entry point."""
    if vite_dev_url and app_env.lower() in _DEV_ENVS:
        base = vite_dev_url.rstrip("/")
        return (
            f'<script type="module" src="{base}/@vite/client"></script>\n'
            f'    <script type="module" src="{base}/resources/js/main.jsx"></script>'
        )
    return _production_tags(project_root)


def _production_tags(project_root: str | Path) -> str:
    manifest_path = Path(project_root) / "public" / "build" / "manifest.json"
    if not manifest_path.exists():
        return "<!-- fastplace: no build manifest; run `npm run build` or start Vite -->"
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return "<!-- fastplace: invalid build manifest -->"

    tags: list[str] = []
    _collect_entry_tags(manifest, "resources/js/main.jsx", tags, seen=set())
    if not tags:
        return "<!-- fastplace: entry resources/js/main.jsx missing from manifest -->"
    return "\n    ".join(tags)


def _collect_entry_tags(manifest: dict, entry: str, tags: list[str], seen: set) -> None:
    chunk = manifest.get(entry)
    if not chunk or entry in seen:
        return
    seen.add(entry)
    for css in chunk.get("css", []) or []:
        tags.append(f'<link rel="stylesheet" href="/build/{css}">')
    for imp in chunk.get("imports", []) or []:
        _collect_entry_tags(manifest, imp, tags, seen)
    file = chunk.get("file")
    if file and file.endswith((".js", ".mjs")):
        tags.append(f'<script type="module" src="/build/{file}"></script>')
