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


# ---------------------------------------------------------------------------
# Serve runtime forces built assets (ssr-G5 / tfa-G7)
# ---------------------------------------------------------------------------


def test_serve_runtime_ignores_the_vite_dev_url(tmp_path, monkeypatch):
    """FASTPLACE_RUNTIME=serve (set by `fastplace serve`) beats APP_ENV=local.

    A local-env serve used to emit dead localhost:5173 script tags — or
    worse, execute whatever foreign project owned that port.
    """
    manifest = tmp_path / "public" / "build" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"resources/js/main.jsx": {"file": "assets/main-Ab12Cd.js"}}')
    monkeypatch.setenv("FASTPLACE_RUNTIME", "serve")
    tags = asset_tags(tmp_path, vite_dev_url="http://localhost:5173", app_env="local")
    assert "/build/assets/main-Ab12Cd.js" in tags
    assert "localhost:5173" not in tags


def test_dev_runtime_keeps_dev_tags(tmp_path, monkeypatch):
    manifest = tmp_path / "public" / "build" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"resources/js/main.jsx": {"file": "assets/main-Ab12Cd.js"}}')
    monkeypatch.delenv("FASTPLACE_RUNTIME", raising=False)
    tags = asset_tags(tmp_path, vite_dev_url="http://localhost:5173", app_env="local")
    assert "localhost:5173/resources/js/main.jsx" in tags


# ---------------------------------------------------------------------------
# Cache-Control on static mounts (serve-G5)
# ---------------------------------------------------------------------------


def test_hashed_build_assets_are_immutable(tmp_path):
    from fastapi import FastAPI

    from fastplace.http.kernel import _install_static_mounts

    app = FastAPI()
    asset = tmp_path / "public" / "build" / "assets" / "main-Ab12Cd.js"
    asset.parent.mkdir(parents=True)
    asset.write_text("console.log('v1');")
    root_file = tmp_path / "public" / "build" / "manifest.json"
    root_file.write_text("{}")
    _install_static_mounts(app, tmp_path)

    from starlette.testclient import TestClient

    with TestClient(app) as client:
        hashed = client.get("/build/assets/main-Ab12Cd.js")
        unhashed = client.get("/build/manifest.json")
    assert hashed.status_code == 200
    assert hashed.headers["cache-control"] == "public, max-age=31536000, immutable"
    # Non-hashed build output revalidates quickly — no year-long trap.
    assert unhashed.status_code == 200
    assert unhashed.headers["cache-control"] == "public, max-age=300"


def test_public_root_files_get_short_cache(tmp_path):
    from fastapi import FastAPI

    from fastplace.http.kernel import _install_static_mounts

    app = FastAPI()
    robots = tmp_path / "public" / "robots.txt"
    robots.parent.mkdir(parents=True)
    robots.write_text("User-agent: *\n")
    _install_static_mounts(app, tmp_path)

    from starlette.testclient import TestClient

    with TestClient(app) as client:
        resp = client.get("/robots.txt")
        missing = client.get("/nope.txt")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=300"
    # Missing files carry no cache policy at all.
    assert missing.status_code == 404
    assert "cache-control" not in missing.headers


def test_public_root_assets_dir_is_not_immutable(tmp_path):
    """Only the build mount hashes its assets/.

    Vite's publicDir copies files into the public root verbatim — a file
    named ``assets/foo.js`` there can change between deploys, so it must
    keep the short revalidation window, never the year-long policy.
    """
    from fastapi import FastAPI

    from fastplace.http.kernel import _install_static_mounts

    app = FastAPI()
    copied = tmp_path / "public" / "assets" / "legacy.js"
    copied.parent.mkdir(parents=True)
    copied.write_text("// un-hashed publicDir copy\n")
    _install_static_mounts(app, tmp_path)

    from starlette.testclient import TestClient

    with TestClient(app) as client:
        resp = client.get("/assets/legacy.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=300"


def test_symlinked_project_root_still_gets_immutable_assets(tmp_path):
    """resolve() keeps the prefix check working through a symlink.

    StaticFiles realpaths the directory when serving; without a matching
    resolve in CachedStaticFiles a symlinked checkout silently lost the
    immutable policy on every hashed asset.
    """
    import os

    from fastapi import FastAPI

    from fastplace.http.kernel import CachedStaticFiles

    real = tmp_path / "real-project" / "public" / "build"
    (real / "assets").mkdir(parents=True)
    (real / "assets" / "main-Ab12Cd.js").write_text("console.log('v1');")
    link = tmp_path / "linked-project"
    os.symlink(tmp_path / "real-project", link)

    from starlette.testclient import TestClient

    app = FastAPI()
    app.mount("/build", CachedStaticFiles(directory=str(link / "public" / "build")))
    with TestClient(app) as client:
        resp = client.get("/build/assets/main-Ab12Cd.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
