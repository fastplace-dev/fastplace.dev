"""Vite asset resolution for the bridge HTML shell."""

from __future__ import annotations

import json

from fastplace.http.assets import asset_tags


def test_production_tags_read_vite6_dot_vite_manifest(tmp_path):
    """Vite 6 writes the manifest to public/build/.vite/manifest.json."""
    build = tmp_path / "public" / "build" / ".vite"
    build.mkdir(parents=True)
    (build / "manifest.json").write_text(
        json.dumps(
            {
                "resources/js/main.jsx": {
                    "file": "assets/main-ABC.js",
                    "css": ["assets/main-XYZ.css"],
                    "imports": [],
                }
            }
        )
    )

    tags = asset_tags(tmp_path, vite_dev_url="http://localhost:5173", app_env="production")
    assert '<script type="module" src="/build/assets/main-ABC.js"></script>' in tags
    assert '<link rel="stylesheet" href="/build/assets/main-XYZ.css">' in tags


def test_production_tags_read_classic_manifest_location(tmp_path):
    build = tmp_path / "public" / "build"
    build.mkdir(parents=True)
    (build / "manifest.json").write_text(
        json.dumps({"resources/js/main.jsx": {"file": "assets/main-OLD.js"}})
    )

    tags = asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    assert "/build/assets/main-OLD.js" in tags


def test_dev_mode_uses_vite_server(tmp_path):
    tags = asset_tags(tmp_path, vite_dev_url="http://localhost:5173/", app_env="local")
    assert 'src="http://localhost:5173/@vite/client"' in tags
    assert 'src="http://localhost:5173/resources/js/main.jsx"' in tags


def test_dev_mode_installs_react_refresh_preamble_before_entry(tmp_path):
    """@vitejs/plugin-react needs its preamble before the first JSX module.

    The bridge shell is written by the framework — it never passes through
    Vite's transformIndexHtml, where the plugin normally injects the
    preamble. Without it every transformed module throws "can't detect
    preamble" and the SPA never mounts.
    """
    tags = asset_tags(tmp_path, vite_dev_url="http://localhost:5173/", app_env="local")
    assert 'from "http://localhost:5173/@react-refresh"' in tags
    assert "window.__vite_plugin_react_preamble_installed__ = true" in tags
    # Module scripts execute in document order: the preamble must precede
    # the entry module.
    assert tags.index("@react-refresh") < tags.index("resources/js/main.jsx")


def test_missing_manifest_leaves_comment(tmp_path):
    tags = asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    assert "no build manifest" in tags
