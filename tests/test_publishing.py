"""Publishing surface — the repo-side half of roadmap P7.

Everything pinned here is repo-side only: link metadata, package READMEs,
the docs-site toolchain/content, the deployment runbook, and CI wiring.
Actual external deployment (fastplace.dev DNS, GitHub Pages enable, PyPI/npm
publish) is the user's runbook — never attempted from the repo.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "docs" / "site"
REPO_URL = "https://github.com/fastplace-dev/fastplace.dev"
HOMEPAGE = "https://fastplace.dev"

#: Build output for the artifact-content tests — under gitignored storage/
#: so repeated runs never pollute the checkout (a root dist/ would).
_BUILD_DIR = ROOT / "storage" / "test-build"

#: Every page the docs site commits to shipping. A missing page is a broken
#: sidebar link, so the set is pinned exactly.
SITE_PAGES = {
    "index.md",
    "getting-started.md",
    "guides/pages-and-the-bridge.md",
    "guides/database.md",
    "guides/auth.md",
    "guides/ai.md",
    "guides/tenancy.md",
    "guides/background-and-cache.md",
    "guides/app-testing.md",
    "guides/testing.md",
    "guides/deployment.md",
    "guides/upgrading.md",
    "guides/versioning.md",
    "api/overview.md",
}

#: Runbook sections — each names one external system the user alone controls.
# Pinned as headings, not loose keywords: the words "PyPI"/"npm" appear all
# over the file, so a deleted section would otherwise go unnoticed.
RUNBOOK_SECTIONS = (
    "## 1. Docs site — the fastplace-docs app",
    "## 2. Domain — point fastplace.dev at the docs app",
    "## 3. PyPI",
    "## 4. npm",
    "## 5. Secrets",
)


def test_fastplace_pyproject_declares_project_urls():
    text = (ROOT / "pyproject.toml").read_text()
    urls_block = text.split("[project.urls]", 1)[1].split("[", 1)[0]
    assert f'Homepage = "{HOMEPAGE}"' in urls_block
    assert f'Repository = "{REPO_URL}"' in urls_block
    assert f'Issues = "{REPO_URL}/issues"' in urls_block


def test_python_and_npm_versions_move_in_lockstep():
    """The compatibility contract (upg-G3): python and npm packages release
    together under one version — ``fastplace X.Y.Z`` pairs with
    ``@fastplace/react X.Y.Z`` and ``@fastplace/ai-react X.Y.Z``. The
    scaffolded npm pin derives from the running Python distribution, so a
    repo where the three drift apart ships apps with untested combos."""
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    react = json.loads((ROOT / "packages" / "react" / "package.json").read_text())
    ai_react = json.loads((ROOT / "packages" / "ai-react" / "package.json").read_text())
    version = pyproject["project"]["version"]
    assert react["version"] == version, "@fastplace/react must match pyproject"
    assert ai_react["version"] == version, "@fastplace/ai-react must match pyproject"


def test_dunder_version_matches_pyproject():
    """``fastplace --version`` and doctor report ``fastplace.__version__``,
    not the pyproject value — the two drifted through 0.2.0 (dunder stuck at
    0.1.0) and shipped a CLI that understated the installed release."""
    import tomllib

    from fastplace import __version__

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]


def test_tenancy_rides_the_framework_release_train():
    """CI exercises fastplace-tenancy solely against the same commit's
    fastplace, so the published combination doctrine ("tested combinations
    only") means tenancy must release at the framework's version with its
    dependency floor on that exact release. The package sat at 0.1.0 through
    the 0.2.0 release; this contract keeps it on the train from here on."""
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    tenancy = tomllib.loads((ROOT / "packages" / "tenancy" / "pyproject.toml").read_text())
    version = pyproject["project"]["version"]
    assert tenancy["project"]["version"] == version
    assert tenancy["project"]["dependencies"] == [f"fastplace>={version}"]


def test_pyproject_license_uses_pep639_spdx_form():
    """The ``license = { text = ... }`` TOML table is deprecated (setuptools
    warns it stops being supported); PEP 639 wants the SPDX string plus an
    explicit ``license-files`` list."""
    text = (ROOT / "pyproject.toml").read_text()
    assert 'license = "MIT"' in text
    assert 'license-files = ["LICENSE"]' in text
    assert "license = {" not in text


def test_dev_extra_installs_email_validator():
    """The emitted auth scaffold's requests use pydantic ``EmailStr``, and the
    CLI test-suite boots those scaffolded apps in-process — so running pytest
    from a fresh checkout needs email-validator importable, which only holds
    if the framework's own dev extra declares it."""
    text = (ROOT / "pyproject.toml").read_text()
    dev_block = text.split("dev = [", 1)[1].split("]", 1)[0]
    assert "email-validator" in dev_block


def test_sqlalchemy_dep_is_open_above_the_2_0_floor():
    """The morph-to-many selectin fix pins ``omit_join=False``, which holds
    the join-based loader — and the full owner-type primaryjoin — on every
    2.x (see tests/orm/test_through_and_morph_pivot.py). The freeze bound
    that carried 0.2.0 through the 2.1 rename is therefore lifted: the
    dependency stays open above the tested 2.0.36 floor."""
    text = (ROOT / "pyproject.toml").read_text()
    assert '"sqlalchemy[asyncio]>=2.0.36"' in text
    assert "<2.1" not in text


def test_pyproject_declares_trove_classifiers():
    text = (ROOT / "pyproject.toml").read_text()
    classifiers_block = text.split("classifiers = [", 1)[1].split("]", 1)[0]
    for needle in (
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ):
        assert needle in classifiers_block, needle


@pytest.fixture(scope="module")
def built_artifacts():
    """One wheel + sdist build shared by the artifact-content tests.

    A full ``python -m build`` costs tens of seconds — the artifact tests
    must not each pay it."""
    pytest.importorskip("build", reason="packaging toolchain (build) not installed")
    import subprocess
    import tarfile
    import zipfile

    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(_BUILD_DIR), "."],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    # The dir keeps artifacts from every past release, so "first glob match"
    # once selected a stale 0.3.1 wheel over the just-built one and failed
    # these tests against content that was never built. Pin the selection to
    # the version pyproject actually declares.
    declared = re.search(
        r'^version\s*=\s*"(.+?)"',
        (ROOT / "pyproject.toml").read_text(),
        re.MULTILINE,
    )
    assert declared, "pyproject.toml carries no version"
    version = declared.group(1)
    wheel = _BUILD_DIR / f"fastplace-{version}-py3-none-any.whl"
    sdist = _BUILD_DIR / f"fastplace-{version}.tar.gz"
    assert wheel.is_file(), f"build produced no wheel for {version}: {wheel.name}"
    assert sdist.is_file(), f"build produced no sdist for {version}: {sdist.name}"
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
    with tarfile.open(sdist) as archive:
        sdist_names = {member.name.split("/", 1)[-1] for member in archive.getmembers()}
    return wheel, sdist, wheel_names, sdist_names


def test_sdist_and_wheel_carry_the_migration_templates(built_artifacts):
    """``fastplace new`` (and ``migrate`` on a fresh project) copies the Alembic
    env/script templates out of the *installed* package. A wheel or sdist that
    omits them crashes the primary onboarding path with FileNotFoundError —
    found the hard way via a clean-room wheel install."""
    wheel, sdist, wheel_names, sdist_names = built_artifacts
    templates = {
        "fastplace/orm/migrations/templates/env.py.tpl",
        "fastplace/orm/migrations/templates/script.py.mako",
    }
    missing = templates - wheel_names
    assert not missing, f"wheel {wheel.name} omits: {sorted(missing)}"
    missing = templates - sdist_names
    assert not missing, f"sdist {sdist.name} omits: {sorted(missing)}"


def test_sdist_and_wheel_carry_the_scaffold_templates(built_artifacts):
    """The frontend starter corpus is package-data under ``fastplace.cli`` —
    an installed ``fastplace new`` walks it verbatim. A wheel or sdist that
    omits even one file silently ships a degraded starter kit."""
    from fastplace.cli.generators import scaffold_templates_dir

    wheel, sdist, wheel_names, sdist_names = built_artifacts
    corpus = Path(scaffold_templates_dir())
    expected = sorted(
        str(Path("fastplace") / p.relative_to(corpus.parent.parent))
        for p in corpus.rglob("*")
        if p.is_file()
    )
    assert expected, "corpus must not be empty"
    missing = [name for name in expected if name not in wheel_names]
    assert not missing, f"wheel {wheel.name} omits: {missing[:5]} (+{len(missing) - 5} more)"
    missing = [name for name in expected if name not in sdist_names]
    assert not missing, f"sdist {sdist.name} omits: {missing[:5]} (+{len(missing) - 5} more)"


def test_tenancy_pyproject_declares_project_urls():
    text = (ROOT / "packages" / "tenancy" / "pyproject.toml").read_text()
    urls_block = text.split("[project.urls]", 1)[1].split("[", 1)[0]
    assert f'Homepage = "{HOMEPAGE}"' in urls_block
    # The monorepo subtree path — the package's own directory, not the root.
    assert f'Repository = "{REPO_URL}/tree/master/packages/tenancy"' in urls_block


def test_tenancy_pyproject_license_uses_pep639_spdx_form():
    text = (ROOT / "packages" / "tenancy" / "pyproject.toml").read_text()
    assert 'license = "MIT"' in text
    assert "license = {" not in text


def test_frontend_packages_are_publishable():
    """Static publish contracts for the scoped npm packages: scoped first
    publishes default to restricted (E402 on free accounts) without
    ``publishConfig.access``, and a tarball without license terms is a legal
    hole — the bundle even embeds MIT-licensed React code."""
    for name in ("react", "ai-react"):
        pkg = ROOT / "packages" / name
        manifest = json.loads((pkg / "package.json").read_text())
        assert manifest["publishConfig"]["access"] == "public", name
        assert manifest["license"] == "MIT", name
        assert (pkg / "LICENSE").is_file(), name
        assert "dist" in manifest["files"], name
        # Types entries must not point at files the build never emits: the
        # build script must produce declarations before dist ships.
        assert "tsc" in manifest["scripts"]["build"], name


def test_frontend_packages_build_before_publish():
    """upg-G5: ``npm publish`` ships whatever sits in ``dist/`` — a forgotten
    rebuild publishes the PREVIOUS release's bundle under a new version
    number. ``prepublishOnly`` rebuilds from src at publish time, so the
    tarball can never go stale relative to the committed source."""
    for name in ("react", "ai-react"):
        manifest = json.loads((ROOT / "packages" / name / "package.json").read_text())
        assert manifest["scripts"].get("prepublishOnly") == "npm run build", name


def test_frontend_builds_externalize_react():
    """Both library builds must treat every react entry point (and its
    scheduler dependency) as external. Exact-name externals miss subpath
    specifiers — react/jsx-runtime silently inlines a second React copy into
    dist, which crashes host apps with "Invalid hook call"."""
    for name in ("react", "ai-react"):
        config = (ROOT / "packages" / name / "vite.config.ts").read_text()
        assert "rollupOptions" in config, name
        rollup_block = config.split("rollupOptions", 1)[1].split("outDir", 1)[0]
        assert "/^react($|\\/)/" in rollup_block, name
        assert "/^scheduler($|\\/)/" in rollup_block, name
        if name == "react":
            assert "/^react-dom($|\\/)/" in rollup_block, name


def test_frontend_packages_declare_repository_links():
    for name in ("react", "ai-react"):
        manifest = json.loads((ROOT / "packages" / name / "package.json").read_text())
        # npm's git+https convention prefixes the plain URL.
        assert REPO_URL in manifest["repository"]["url"], name
        assert manifest["homepage"].startswith(HOMEPAGE), name
        assert manifest["bugs"]["url"].startswith(REPO_URL), name
        assert manifest["repository"]["directory"] == f"packages/{name}", name


def test_frontend_packages_ship_readmes():
    expectations = {
        # "ESM-only": the packages ship no CJS build — CommonJS hosts need a
        # bundler, and the README must say so before install time.
        "react": ("usePage", "render(", "X-Fastplace-Request", "ESM"),
        "ai-react": ("useAIStream", "useAgent", "SSE", "ESM"),
    }
    for name, needles in expectations.items():
        readme = (ROOT / "packages" / name / "README.md").read_text()
        assert len(readme.splitlines()) >= 40, name
        for needle in needles:
            assert needle in readme, (name, needle)
        # Source links must name the real default branch (master).
        assert f"{REPO_URL}/tree/master/packages/{name}" in readme, name


def test_package_readmes_document_the_real_hook_api():
    """The READMEs are the npm landing pages — a hook API that does not
    match @fastplace/ai-react's exports is a support ticket waiting."""
    readme = (ROOT / "packages" / "ai-react" / "README.md").read_text()
    # useAIStream's actual result shape (packages/ai-react/src/index.ts).
    assert "handleSubmit, isStreaming } = useAIStream" in readme
    for fiction in ("onSubmit={send}", "disabled={streaming}", 'useAgent("'):
        assert fiction not in readme, fiction
    # useAgent takes an options object, not a bare endpoint string.
    assert "useAgent({ endpoint:" in readme


def test_docs_site_documents_the_real_api():
    """Docs code blocks are copy-paste contracts: config keys, method names,
    import paths and hook shapes must match the framework exactly. Each
    needle below was verified against the source before writing this test."""
    pages = {p.name: p.read_text() for p in (SITE / "guides").glob("*.md")}
    pages["api/overview.md"] = (SITE / "api" / "overview.md").read_text()
    pages["getting-started.md"] = (SITE / "getting-started.md").read_text()
    blob = "\n".join(pages.values())

    must_appear = {
        # the cache config key the framework actually reads (fastplace/cache.py)
        "CACHE_DRIVER",
        # soft-delete escape is only_deleted (never only_trashed)
        "only_deleted()",
        # hashing surface is make/check
        "Hash.check",
        # CSRF middleware ships in fastplace.auth, not app.http
        "fastplace.auth.middleware.CsrfMiddleware",
        # TokenGuard needs its secret (APP_KEY)
        'secret=config("APP_KEY")',
        # useAIStream's real result shape
        "handleSubmit, isStreaming } = useAIStream",
        # agents stream from app factories, unawaited
        'assistant_agent().stream_response(data["message"]',
        # embed() takes one string and returns its vector
        "await embed(item.body)",
        # the passkey guard accessor the docs must name verbatim
        "passkey_guard()",
    }
    for needle in must_appear:
        assert needle in blob, needle

    must_not_appear = {
        "CACHE_STORE",
        "only_trashed",
        "Hash.verify",
        "app.http.middleware.csrf",
        # no agent(name) registry fn exists in fastplace.ai
        "from fastplace.ai import agent",
        'useAgent("',
    }
    for fiction in must_not_appear:
        assert fiction not in blob, fiction


def test_docs_site_ships_the_pinned_pages():
    for page in SITE_PAGES:
        assert (SITE / page).is_file(), page


def test_docs_site_declares_the_sidebar_and_nav():
    config = (SITE / ".vitepress" / "config.ts").read_text()
    # Every pinned page must be reachable from the built sidebar, not orphaned.
    for page in SITE_PAGES:
        slug = page.removesuffix(".md")
        assert slug in config, page
    assert HOMEPAGE in config or "fastplace.dev" in config


def test_docs_toolchain_is_wired_into_the_monorepo():
    package = json.loads((ROOT / "package.json").read_text())
    assert "vitepress" in package["devDependencies"]
    assert package["scripts"]["docs:build"].startswith("vitepress build")
    assert "docs:dev" in package["scripts"]
    # Build output is runtime output — never committed.
    gitignore = (ROOT / ".gitignore").read_text()
    assert ".vitepress/dist" in gitignore
    assert ".vitepress/cache" in gitignore


def test_lint_ignores_docs_build_output():
    """Anyone who builds the docs locally then runs lint:check would lint the
    minified VitePress bundle — hundreds of errors from generated code. The
    eslint ignores must cover it, same rule as public/build."""
    eslint = (ROOT / "eslint.config.js").read_text()
    assert "docs/site/.vitepress/dist" in eslint
    assert "docs/site/.vitepress/cache" in eslint


def test_docs_build_output_is_not_committed():
    """The build runs locally and in CI — its output existing on disk is
    normal. What must never happen is it landing in the tree."""
    import subprocess

    if not (ROOT / ".git").exists():
        pytest.skip("requires a git checkout")

    tracked = subprocess.run(
        ["git", "ls-files", "docs/site/.vitepress"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout.split()
    # Subset, not equality: config.ts may be staged-but-not-yet-committed in
    # a fresh checkout; any tracked dist/cache file is the regression.
    assert set(tracked) <= {"docs/site/.vitepress/config.ts"}, tracked
    # One call per path (a combined call passes if EITHER path is ignored)
    # and with a trailing slash so dir-patterns match even before the first
    # local build creates the directory.
    for part in ("dist", "cache"):
        result = subprocess.run(
            ["git", "check-ignore", f"docs/site/.vitepress/{part}/"],
            cwd=ROOT,
        )
        assert result.returncode == 0, f"docs/site/.vitepress/{part} must be gitignored"


def test_no_pages_workflow_can_clobber_the_production_domain():
    """G3: fastplace.dev is served by the separate fastplace-docs app —
    this repo's GitHub Pages was never enabled (verified: the Pages API
    404s under an admin token). A Pages workflow left in the tree
    self-activates the day the repo flips public, and a stray docs/site
    push would then publish a stale site (and fight the custom domain).
    The VitePress site still compiles in CI (the frontend job's
    docs:build step) — only the deploy path is retired."""
    assert not (ROOT / ".github" / "workflows" / "docs.yml").exists(), (
        "docs.yml must not exist: production docs deploy from the separate "
        "fastplace-docs app, never from this repo's Pages"
    )


def test_all_extra_composes_every_shippable_extra():
    """#20: ``pip install fastplace[all]`` is the everything knob — every
    optional extra must be composed into it (``dev`` excluded: tooling, not
    shippable surface). pwdlib[argon2] shipped as an extra but never made it
    into ``all``, so the argon2 password hasher silently missed the
    everything install."""
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    extras = pyproject["project"]["optional-dependencies"]
    for name, requirements in extras.items():
        if name in ("all", "dev"):
            continue
        for requirement in requirements:
            assert requirement in extras["all"], f"extra '{name}' missing from 'all': {requirement}"


def test_community_health_files_pin_support_and_gates():
    """G6: SECURITY.md and CONTRIBUTING.md exist and carry the essentials —
    vulnerability reporting through GitHub's private channel (no plaintext
    email addresses to harvest), and the exact dev gates a contributor is
    held to. Asserted as content anchors, not filenames, so an emptied-out
    stub cannot pass."""
    security = (ROOT / "SECURITY.md").read_text()
    assert "private vulnerability reporting" in security.lower()
    assert "github.com/fastplace-dev/fastplace.dev/security" in security
    assert "@" not in security.replace("https://", ""), "no plaintext emails"

    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    for gate in ("pytest", "ruff", "mypy", "lint:modules", "npm run test:run"):
        assert gate in contributing, gate
    # The branding guardrail, restated neutrally: no other framework's name
    # may appear in code, docs, or commits — pattern names only.
    assert "neutral" in contributing.lower()


def test_deployment_runbook_names_every_external_step():
    runbook_path = ROOT / "docs" / "deployment-runbook.md"
    if not runbook_path.exists():
        # The runbook is maintainer-local (gitignored, never published);
        # a public clone has nothing to check.
        pytest.skip("deployment runbook is maintainer-local")
    runbook = runbook_path.read_text()
    for section in RUNBOOK_SECTIONS:
        assert section in runbook, section
    # The extractor is the runbook's local half — it must be named.
    assert "extract_sample" in runbook
    # Publishing credentials are referenced as CI secrets, not inline values.
    assert "PYPI_API_TOKEN" in runbook or "NPM_TOKEN" in runbook
    # No secret ever lands in the runbook itself.
    assert "pypi-AgEIcH" not in runbook
    assert "npm_" not in runbook


@pytest.mark.parametrize(
    "page",
    sorted(SITE_PAGES),
)
def test_site_pages_are_real_content(page):
    """A page under ~15 lines is a placeholder, not a guide."""
    body = (SITE / page).read_text()
    assert len([line for line in body.splitlines() if line.strip()]) >= 15, page


def test_home_hero_banner_is_a_served_asset():
    """The home hero must reference a banner VitePress actually serves —
    hero images live under docs/site/public/ (the site's static dir), not
    in repo-only locations the site build cannot resolve."""
    home = (SITE / "index.md").read_text()
    m = re.search(r"image:\s*\n(?:\s+\w+:.*\n)*?\s+src:\s*(\S+)", home)
    assert m, "index.md hero must declare an image.src"
    asset = SITE / "public" / m.group(1).lstrip("/")
    assert asset.is_file(), f"hero image missing from site public dir: {asset}"
