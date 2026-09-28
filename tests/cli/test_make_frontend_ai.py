"""Frontend + AI scaffolders — make:vector-store, make:component,
make:layout, make:hook (spec #51–#54)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Autouse fixture: clean db/model/module state per test (see _isolation.py) —
# the vector-store registration test imports the scaffolded project's app.*.
from tests.cli._isolation import isolate_project_state  # noqa: F401

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_vector_registry():
    """Vector-store tests mutate the global registry — reset around each."""
    from fastplace.ai import reset_vector_registry

    reset_vector_registry()
    yield
    reset_vector_registry()


def _invoke(monkeypatch, tmp_path, *args: str):
    monkeypatch.chdir(tmp_path)
    return runner.invoke(cli_app, list(args))


# (argv, expected project-relative path, must-appear content markers)
_CASES = [
    pytest.param(
        ["make:vector-store", "docs"],
        "app/ai/vectors/docs.py",
        [
            "from fastplace.ai.vectors import register_vector_store",
            '@register_vector_store("docs")',
            "class DocsVectorStore:",
            "async def search(self, model_cls, embedding, limit=10):",
        ],
        id="vector-store",
    ),
    pytest.param(
        ["make:component", "Card"],
        "resources/js/components/Card.jsx",
        [
            "import React from",
            "export function Card({ children })",
            "bg-surface",
            "text-ink",
            "border-line",
        ],
        id="component",
    ),
    pytest.param(
        ["make:layout", "Admin"],
        "resources/js/layouts/AdminLayout.jsx",
        [
            "import React from",
            "export default function AdminLayout({ children })",
            "{children}",
        ],
        id="layout",
    ),
    pytest.param(
        ["make:hook", "useDebounce"],
        "resources/js/hooks/useDebounce.js",
        [
            'import { useState } from "react"',
            "export function useDebounce(",
        ],
        id="hook",
    ),
]


class TestScaffolds:
    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_creates_stub_with_content_markers(self, tmp_path, monkeypatch, argv, rel, markers):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        path = tmp_path / rel
        assert path.is_file(), f"missing {rel}"
        source = path.read_text()
        for marker in markers:
            assert marker in source, f"{rel} lacks {marker!r}"
        assert rel in result.output

    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_refuses_to_overwrite_without_force(self, tmp_path, monkeypatch, argv, rel, markers):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        path = tmp_path / rel
        path.write_text("// SENTINEL — hand edits must survive\n")
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output
        assert "exists" in result.output
        assert "SENTINEL" in path.read_text()

    @pytest.mark.parametrize(("argv", "rel", "markers"), _CASES)
    def test_force_overwrites_the_stub(self, tmp_path, monkeypatch, argv, rel, markers):
        path = tmp_path / rel
        path.parent.mkdir(parents=True)
        path.write_text("// SENTINEL — replaced under --force\n")

        result = _invoke(monkeypatch, tmp_path, *argv, "--force")
        assert result.exit_code == 0, result.output
        source = path.read_text()
        assert "SENTINEL" not in source
        for marker in markers:
            assert marker in source, f"{rel} lacks {marker!r}"


class TestMakeVectorStore:
    def test_scaffolded_store_registers_via_import_vector_stores(self, tmp_path, monkeypatch):
        """The stub must mirror the vectors.py auto-import convention exactly:
        importing the module registers the backend under its dispatch name."""
        import asyncio

        from fastplace.ai.vectors import import_vector_stores, vector_store

        result = _invoke(monkeypatch, tmp_path, "make:vector-store", "docs")
        assert result.exit_code == 0, result.output

        names = import_vector_stores(tmp_path)
        assert "docs" in names
        store = vector_store("docs")
        assert type(store).__name__ == "DocsVectorStore"
        assert asyncio.run(store.search(None, [0.1, 0.2], limit=3)) == []

    def test_package_markers_are_created(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:vector-store", "docs")
        assert result.exit_code == 0, result.output
        for marker in (
            "app/__init__.py",
            "app/ai/__init__.py",
            "app/ai/vectors/__init__.py",
        ):
            assert (tmp_path / marker).is_file(), f"missing {marker}"

    def test_rejects_path_shaped_names(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:vector-store", "../evil")
        assert result.exit_code == 1
        assert not (tmp_path / "app" / "ai" / "vectors" / "evil.py").exists()


class TestMakeComponent:
    def test_stub_matches_repo_exemplar_conventions(self, tmp_path, monkeypatch):
        """Named export (AppHeader/Breadcrumbs style) + theme-token utility
        classes from resources/css/app.css, header comment like make:page."""
        result = _invoke(monkeypatch, tmp_path, "make:component", "UserCard")
        assert result.exit_code == 0, result.output

        source = (tmp_path / "resources" / "js" / "components" / "UserCard.jsx").read_text()
        assert "export function UserCard({ children })" in source
        assert "export default" not in source
        assert "bg-surface" in source
        assert "text-ink" in source
        assert "border-line" in source
        assert "make:component UserCard" in source

    def test_rejects_path_shaped_names(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:component", "../evil")
        assert result.exit_code == 1
        assert not (tmp_path / "resources" / "js" / "components").exists()


class TestMakeLayout:
    def test_stub_matches_repo_exemplar_conventions(self, tmp_path, monkeypatch):
        """Default export with a children prop — the app/auth layout style."""
        result = _invoke(monkeypatch, tmp_path, "make:layout", "Admin")
        assert result.exit_code == 0, result.output

        source = (tmp_path / "resources" / "js" / "layouts" / "AdminLayout.jsx").read_text()
        assert "export default function AdminLayout({ children })" in source
        assert "{children}" in source
        assert "make:layout Admin" in source

    def test_double_layout_suffix_is_not_doubled(self, tmp_path, monkeypatch):
        """``make:layout AdminLayout`` mirrors make:controller's suffix rule."""
        result = _invoke(monkeypatch, tmp_path, "make:layout", "AdminLayout")
        assert result.exit_code == 0, result.output
        path = tmp_path / "resources" / "js" / "layouts" / "AdminLayout.jsx"
        assert path.is_file()
        assert not (tmp_path / "resources" / "js" / "layouts" / "AdminLayoutLayout.jsx").exists()
        assert "export default function AdminLayout(" in path.read_text()

    def test_rejects_path_shaped_names(self, tmp_path, monkeypatch):
        result = _invoke(monkeypatch, tmp_path, "make:layout", "../evil")
        assert result.exit_code == 1
        assert not (tmp_path / "resources" / "js" / "layouts").exists()


class TestMakeHook:
    def test_stub_matches_repo_exemplar_conventions(self, tmp_path, monkeypatch):
        """Named export (use-clipboard/use-initials style) on a .js file —
        the make:page family's plain-JS default."""
        result = _invoke(monkeypatch, tmp_path, "make:hook", "useDebounce")
        assert result.exit_code == 0, result.output

        source = (tmp_path / "resources" / "js" / "hooks" / "useDebounce.js").read_text()
        assert 'import { useState } from "react"' in source
        assert "export function useDebounce(" in source
        assert "export default" not in source
        assert "make:hook useDebounce" in source

    def test_rejects_hyphenated_names(self, tmp_path, monkeypatch):
        """A hook module name must itself be a valid JS identifier — the
        kebab-case file convention of the exemplars cannot be imported."""
        result = _invoke(monkeypatch, tmp_path, "make:hook", "use-debounce")
        assert result.exit_code == 1
        assert not (tmp_path / "resources" / "js" / "hooks").exists()


class TestRepoExemplarsStayUnderFrontendGate:
    """The repo tree carries one exemplar stub per frontend maker so the
    generated templates stay permanently under the repo-level lint/type
    gates (plan ruling); this pins them to the actual template output."""

    @pytest.mark.parametrize(
        ("argv", "rel"),
        [
            pytest.param(
                ["make:component", "Card"], "resources/js/components/Card.jsx", id="component"
            ),
            pytest.param(
                ["make:layout", "Admin"], "resources/js/layouts/AdminLayout.jsx", id="layout"
            ),
            pytest.param(
                ["make:hook", "useDebounce"], "resources/js/hooks/useDebounce.js", id="hook"
            ),
        ],
    )
    def test_committed_exemplar_matches_generated_template(self, tmp_path, monkeypatch, argv, rel):
        result = _invoke(monkeypatch, tmp_path, *argv)
        assert result.exit_code == 0, result.output

        repo_root = Path(__file__).resolve().parents[2]
        committed = repo_root / rel
        assert committed.is_file(), f"missing committed exemplar {rel}"
        assert committed.read_text() == (tmp_path / rel).read_text()
