"""The shipped starter corpus: what must exist, what must never exist."""

from __future__ import annotations

from pathlib import Path

import pytest

from fastplace.cli.generators import scaffold_templates_dir

CORPUS = Path(scaffold_templates_dir())

MUST_EXIST = (
    "resources/js/main.jsx",
    "resources/css/app.css",
    "resources/js/pages/Auth/Login.tsx",
    "resources/js/pages/Auth/Register.tsx",
    "resources/js/pages/Home/Index.tsx",
    "resources/js/pages/Dashboard/Index.tsx",
    "resources/js/pages/Settings/Profile.tsx",
    "resources/js/layouts/app-layout.tsx",
    "resources/js/layouts/auth-layout.tsx",
    "resources/js/components/ui/button.tsx",
    "resources/js/components/ui/sidebar.tsx",
    "resources/js/components/app-sidebar.tsx",
    "resources/js/hooks/use-appearance.ts",
    "resources/js/lib/utils.ts",
    "resources/js/types/index.ts",
    "public/fastplace-logo.svg",
    "public/favicon.ico",
    "tsconfig.json",
    "eslint.config.js",
)

MUST_NOT_EXIST = (
    "resources/js/layouts/AppLayout.jsx",
    "resources/js/layouts/AdminLayout.jsx",
    "resources/js/components/Card.jsx",
    "resources/js/hooks/useDebounce.js",
    "resources/js/pages/About",
    "resources/js/pages/Assistant",
    "resources/js/pages/Knowledge",
    "resources/js/pages/Projects",
)


@pytest.mark.parametrize("rel", MUST_EXIST)
def test_corpus_carries_the_starter(rel):
    assert (CORPUS / rel).is_file(), f"corpus missing {rel}"


@pytest.mark.parametrize("rel", MUST_NOT_EXIST)
def test_corpus_excludes_sample_domain_and_legacy(rel):
    assert not (CORPUS / rel).exists(), f"corpus must not carry {rel}"


def test_corpus_has_no_admin_literal():
    hits = [
        str(p.relative_to(CORPUS))
        for p in CORPUS.rglob("*")
        if p.is_file()
        and p.suffix in {".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json"}
        and "admin@example.com" in p.read_text(errors="ignore")
    ]
    assert hits == []


def test_corpus_has_no_sample_domain_routes():
    # Sample-domain pages are cut; no shipped runtime file may link to their
    # routes. (Test files are exempt — generic component tests pass dummy
    # hrefs like "/projects" as inert fixture props.)
    hits = [
        str(p.relative_to(CORPUS))
        for p in (CORPUS / "resources/js").rglob("*.tsx")
        if ".test." not in p.name
        and "__tests__" not in p.parts
        and (
            '"/projects"' in p.read_text(errors="ignore")
            or '"/knowledge"' in p.read_text(errors="ignore")
            or '"/assistant"' in p.read_text(errors="ignore")
            or '"/about"' in p.read_text(errors="ignore")
        )
    ]
    assert hits == []


def test_corpus_tests_never_import_cut_pages():
    # A test left behind that imports a cut page fails at vitest collection
    # time in every scaffolded app — the cut must remove its tests too.
    hits = [
        str(p.relative_to(CORPUS))
        for p in CORPUS.rglob("*.test.*")
        if any(
            segment in p.read_text(errors="ignore")
            for segment in ("../Projects", "../Knowledge", "../Assistant", "../About")
        )
    ]
    assert hits == []
