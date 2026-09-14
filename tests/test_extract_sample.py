"""The sample extractor — dogfood app out, runnable scaffold back.

The dogfood app (app/, routes/, config/, database/, resources/) IS the
sample application the blueprint asks for; the extractor copies it out of
the monorepo into a self-contained directory with run instructions, without
ever touching runtime output (storage/, public/build/) or secrets (.env).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "extract_sample.py"


@pytest.fixture()
def extractor():
    spec = importlib.util.spec_from_file_location("extract_sample", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["extract_sample"] = module
    spec.loader.exec_module(module)
    return module


def test_script_exists():
    assert SCRIPT.is_file()


def test_extracts_the_sample_trees(extractor, tmp_path):
    target = tmp_path / "my-app"
    written = extractor.extract(target)

    assert target.is_dir()
    for tree in ("app", "routes", "config", "database", "resources"):
        assert (target / tree).is_dir(), tree
        assert written > 0
    # The scaffold runs out of the box: env template + instructions included.
    assert (target / ".env.example").is_file()
    assert (target / "README.md").is_file()
    readme = (target / "README.md").read_text()
    assert "pip install" in readme
    assert "fastplace migrate" in readme


def test_runtime_output_and_secrets_never_ride_along(extractor, tmp_path):
    target = tmp_path / "sample"
    extractor.extract(target)

    assert not (target / "storage").exists()
    assert not (target / ".env").exists()
    assert not (target / "node_modules").exists()
    assert not (target / "public" / "build").exists()
    # Python bytecode either — the scaffold is source, not artifacts.
    assert not list(target.rglob("__pycache__"))


def test_refuses_a_non_empty_target(extractor, tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "precious.txt").write_text("user data")
    with pytest.raises(SystemExit, match="refusing"):
        extractor.extract(target)
    # Nothing was touched.
    assert (target / "precious.txt").read_text() == "user data"
    assert list(target.iterdir()) == [target / "precious.txt"]


def test_copies_are_independent_of_the_repo(extractor, tmp_path):
    """The extracted scaffold must not symlink or reference back into the
    monorepo — moving it elsewhere has to keep it runnable."""
    target = tmp_path / "moved-app"
    extractor.extract(target)
    sources = [p for p in ROOT.glob("app/**/*.py") if "conftest" not in p.name]
    probe = sources[0].relative_to(ROOT)
    copied = target / probe
    assert copied.is_file()
    assert copied.read_text() == (ROOT / probe).read_text()
    assert not copied.is_symlink()


def test_refuses_a_target_inside_the_repository(extractor, tmp_path):
    """Writing the scaffold over the monorepo's own trees would duplicate
    (or shadow) framework files — the extractor must refuse outright."""
    for inside in (ROOT / "app" / "demo", ROOT / "extracted-sample"):
        with pytest.raises(SystemExit, match="inside the repository"):
            extractor.extract(inside)
        assert not (ROOT / "app" / "demo").exists()
        assert not (ROOT / "extracted-sample").exists()


def test_refuses_non_directory_targets(extractor, tmp_path):
    """A regular file (or a dangling symlink) as the target would crash with
    a confusing NotADirectoryError mid-copy — refuse with a clear message."""
    file_target = tmp_path / "occupied.txt"
    file_target.write_text("user data")
    with pytest.raises(SystemExit, match="not a directory"):
        extractor.extract(file_target)
    assert file_target.read_text() == "user data"

    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(SystemExit, match="not a directory"):
        extractor.extract(dangling)


def test_exclusions_bite_on_a_dirty_source_tree(extractor, tmp_path):
    """Exclusion logic proven on a controlled fixture, not the (clean) repo —
    secrets at any depth (.env, .env.local, .env.production), runtime output
    (storage/, build/, __pycache__) and OS junk (.DS_Store) never ride along,
    while .env.example and real source files do."""
    src = tmp_path / "fixture"
    (src / "app").mkdir(parents=True)
    (src / "app" / "real.py").write_text("print('ok')")
    (src / "app" / "__pycache__").mkdir()
    (src / "app" / "__pycache__" / "m.pyc").write_bytes(b"")
    (src / "app" / "storage" / "logs").mkdir(parents=True)
    (src / "app" / "storage" / "logs" / "app.log").write_text("log")
    (src / "app" / ".DS_Store").write_bytes(b"junk")
    (src / "config").mkdir()
    (src / "config" / ".env").write_text("SECRET=1")
    (src / "config" / ".env.local").write_text("SECRET=2")
    (src / "config" / ".env.production").write_text("SECRET=3")
    (src / "config" / ".env.example").write_text("KEY=")
    (src / "public" / "build").mkdir(parents=True)
    (src / "public" / "build" / "asset.js").write_text("built")

    target = tmp_path / "out"
    extractor.extract(target, source=src)

    assert (target / "app" / "real.py").is_file()
    assert not list(target.rglob("m.pyc"))  # __pycache__
    assert not (target / "app" / "storage").exists()
    assert not (target / "app" / ".DS_Store").exists()
    assert not list(target.rglob(".env.local"))
    assert not list(target.rglob(".env.production"))
    assert not (target / "config" / ".env").exists()
    assert (target / "config" / ".env.example").is_file()  # the template rides along
    assert not (target / "public" / "build").exists()


def test_scaffold_boots_standalone(extractor, tmp_path):
    """The README's run steps must be true: `fastplace run dev` needs an ASGI
    entry point and a standalone frontend toolchain — not the monorepo's
    workspace-wired package.json. The install line targets the published
    package (the scaffold carries no pyproject.toml)."""
    target = tmp_path / "boots-app"
    extractor.extract(target)

    assert (target / "asgi.py").is_file()
    assert (target / "package.json").is_file()
    assert (target / "vite.config.js").is_file()
    assert (target / "index.html").is_file()

    readme = (target / "README.md").read_text(encoding="utf-8")
    assert 'pip install "fastplace[queue,ai]"' in readme
    assert "pip install -e" not in readme  # no local manifest to install
    package_json = (target / "package.json").read_text()
    assert '"@fastplace/react"' in package_json
    assert "workspaces" not in package_json  # standalone, not monorepo wiring


def test_embedded_templates_match_the_generator(extractor):
    """The standalone package.json / vite.config.js / index.html written by
    the extractor are the same contracts `fastplace new` generates — drift
    here would give the sample app a second, stale frontend toolchain."""
    from fastplace.cli import generators

    assert extractor._PACKAGE_JSON_TEMPLATE == generators._PACKAGE_JSON_TEMPLATE
    assert extractor._VITE_CONFIG_TEMPLATE == generators._VITE_CONFIG_TEMPLATE
    assert extractor._INDEX_HTML_TEMPLATE == generators._INDEX_HTML_TEMPLATE
