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
    tags = asset_tags(
        tmp_path, vite_dev_url="http://localhost:5173/", app_env="local"
    )
    assert 'src="http://localhost:5173/@vite/client"' in tags
    assert 'src="http://localhost:5173/resources/js/main.jsx"' in tags


def test_missing_manifest_leaves_comment(tmp_path):
    tags = asset_tags(tmp_path, vite_dev_url=None, app_env="production")
    assert "no build manifest" in tags
