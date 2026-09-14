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


# ---------------------------------------------------------------------------
# Static serving of public/build
# ---------------------------------------------------------------------------


def test_build_mount_serves_assets_created_after_boot(tmp_path):
    """Assets built after app startup must still be served.

    A fresh clone has no ``public/`` at all (nothing under it is tracked),
    and CI smoke runs can execute the first ``vite build`` after the server
    has already booted. The ``/build`` mount must be installed unconditionally
    and resolve files at request time.
    """
    from fastapi import FastAPI

    from fastplace.http.kernel import _install_static_mounts

    app = FastAPI()
    _install_static_mounts(app, tmp_path)  # tmp project: no public/ yet

    # The build lands after boot, as on a fresh CI checkout.
    asset = tmp_path / "public" / "build" / "assets" / "app.js"
    asset.parent.mkdir(parents=True)
    asset.write_text("console.log('hydrated');")

    from starlette.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/build/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log('hydrated');"


def test_build_mount_404s_gracefully_without_a_build(tmp_path):
    """No build output → clean 404, not a startup crash."""
    from fastapi import FastAPI

    from fastplace.http.kernel import _install_static_mounts

    app = FastAPI()
    _install_static_mounts(app, tmp_path)

    from starlette.testclient import TestClient

    with TestClient(app) as client:
        assert client.get("/build/assets/never-built.js").status_code == 404
