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


# The corpus overlaps the repo beyond resources/js: the styles, the public
# assets, and the root tooling configs. Every overlapping file must stay
# byte-equal with its repo counterpart unless named in INTENTIONALLY_DIVERGED
# — the repo copy is the verified-working reference (bridge props via
# usePage, a11y fixes), and hand-maintaining two versions of one file rots.
_OVERLAY_DIRS = ("resources", "public")
_OVERLAY_FILES = ("eslint.config.js", "tsconfig.json")

# Corpus files whose repo counterpart is INTENTIONALLY different (keys are
# repo-root-relative). Everything else in the overlay with a repo counterpart
# must stay byte-equal.
INTENTIONALLY_DIVERGED = {
    # Starter-specific rewrites of repo pages (demo nav vs framework docs).
    "resources/js/components/app-sidebar.tsx",
    "resources/js/pages/Home/Index.tsx",
    "resources/js/pages/__tests__/Home.test.tsx",
    # The starter config drops repo-only ignores/aliases (docs/, packages/,
    # agent worktrees) an app template never needs.
    "eslint.config.js",
    "tsconfig.json",
}

# Overlay files that are starter-ONLY (no repo counterpart, pinned by name so
# a repo-side rename/delete of any OTHER file fails loudly instead of
# silently shrinking byte-sync coverage — the relocated twin of drift).
STARTER_ONLY = {
    # The repo dashboard is sample-domain Index.jsx; the starter ships a
    # blank canvas instead.
    "resources/js/pages/Dashboard/Index.tsx",
    "public/robots.txt",
    "public/apple-touch-icon.png",
}


def _overlay_paths():
    for directory in _OVERLAY_DIRS:
        yield from (CORPUS / directory).rglob("*")
    for name in _OVERLAY_FILES:
        yield CORPUS / name


def test_corpus_overlay_stays_byte_synced_with_the_repo():
    repo_root = CORPUS.parents[2]
    drifted = []
    unpaired = []
    for path in sorted(_overlay_paths()):
        if not path.is_file():
            continue
        rel = path.relative_to(CORPUS)
        counterpart = repo_root / rel
        if not counterpart.is_file():
            if str(rel) not in STARTER_ONLY:
                unpaired.append(str(rel))
            continue
        if path.read_bytes() != counterpart.read_bytes():
            drifted.append(str(rel))
    assert unpaired == [], f"corpus files lost their repo counterpart: {unpaired}"
    assert sorted(drifted) == sorted(INTENTIONALLY_DIVERGED)


def test_intentionally_diverged_entries_still_diverge():
    # An allowlisted pair that became byte-equal again (alert.tsx once W5
    # lands) must LEAVE the allowlist — otherwise the list rots into cover
    # for drift nobody re-examines.
    repo_root = CORPUS.parents[2]
    for rel in INTENTIONALLY_DIVERGED:
        corpus_file = CORPUS / rel
        repo_file = repo_root / rel
        assert corpus_file.is_file() and repo_file.is_file(), rel
        assert corpus_file.read_bytes() != repo_file.read_bytes(), rel
