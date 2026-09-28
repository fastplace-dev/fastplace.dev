"""Vite asset resolution for the bridge HTML shell.

Development mode points at the Vite dev server (HMR); production resolves
hashed assets from ``public/build/manifest.json``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DEV_ENVS = {"local", "dev", "development"}

# Parsed manifests cached by (path → mtime, data) — the shell renders on every
# initial-load request and must not re-read the manifest each time. A new
# build bumps mtime and invalidates the entry.
_manifest_cache: dict[Path, tuple[int, dict | None]] = {}


def asset_tags(project_root: str | Path, *, vite_dev_url: str | None, app_env: str) -> str:
    """Return <script>/<link> tags for the app entry point."""
    if (
        vite_dev_url
        and app_env.lower() in _DEV_ENVS
        # `serve` is a real server even under APP_ENV=local: no Vite process
        # answers at the dev URL, so dev tags would 404 the whole SPA. The
        # runtime marker (set by the CLI on the serve child) wins over env.
        and os.environ.get("FASTPLACE_RUNTIME", "").lower() != "serve"
    ):
        base = vite_dev_url.rstrip("/")
        # The react-refresh preamble normally rides Vite's transformIndexHtml;
        # this shell never passes through Vite, so the framework injects it
        # itself. Module scripts run in document order — the preamble must
        # precede the entry module or every JSX module throws
        # "@vitejs/plugin-react can't detect preamble".
        return (
            f'<script type="module" src="{base}/@vite/client"></script>\n'
            f'    <script type="module">\n'
            f'      import RefreshRuntime from "{base}/@react-refresh"\n'
            f"      RefreshRuntime.injectIntoGlobalHook(window)\n"
            f"      window.$RefreshReg$ = () => {{}}\n"
            f"      window.$RefreshSig$ = () => (type) => type\n"
            f"      window.__vite_plugin_react_preamble_installed__ = true\n"
            f"    </script>\n"
            f'    <script type="module" src="{base}/resources/js/main.jsx"></script>'
        )
    return _production_tags(project_root)


def _manifest_path(project_root: str | Path) -> Path | None:
    """Locate the build manifest — Vite 6 nests it under .vite/."""
    build = Path(project_root) / "public" / "build"
    for candidate in (build / "manifest.json", build / ".vite" / "manifest.json"):
        if candidate.exists():
            return candidate
    return None


def _load_manifest(path: Path) -> dict | None:
    """Parse ``path`` once per mtime generation; ``None`` when unreadable."""
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _manifest_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        manifest: dict | None = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        manifest = None
    _manifest_cache[path] = (mtime, manifest)
    return manifest


def _production_tags(project_root: str | Path) -> str:
    manifest_path = _manifest_path(project_root)
    if manifest_path is None:
        return "<!-- fastplace: no build manifest; run `npm run build` or start Vite -->"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
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
