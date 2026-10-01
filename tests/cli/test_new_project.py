"""``fastplace new`` — the modular-monolith project scaffolder (blueprint §3).

The generated skeleton must be the canonical layout the rest of the CLI and
the kernel already assume: module-first ``app/``, thin ``routes/``, config
modules, SQLite-by-default env, and a bootable ASGI entry point.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest
import typer

from fastplace.cli import app as cli_app

_DIRECTORIES = (
    "app/http/controllers",
    "app/http/requests",
    "app/http/middleware",
    "app/modules",
    "app/ai/agents",
    "app/ai/tools",
    "app/ai/vectors",
    "app/jobs",
    "app/models",
    "database/migrations",
    "database/seeders",
    "resources/js/components",
    "resources/js/hooks",
    "resources/js/layouts",
    "resources/js/pages",
    "resources/css",
    "routes",
    "config",
    "public",
    "storage",
)

_FILES = (
    "app/http/controllers/__init__.py",
    "app/http/controllers/home_controller.py",
    "app/http/requests/__init__.py",
    "app/http/middleware/__init__.py",
    "app/modules/__init__.py",
    "app/ai/agents/__init__.py",
    "app/ai/tools/__init__.py",
    "app/ai/vectors/__init__.py",
    "app/jobs/__init__.py",
    "app/models/__init__.py",
    "resources/js/main.jsx",
    "resources/js/layouts/app-layout.tsx",
    "resources/js/pages/Home/Index.tsx",
    "resources/js/pages/Auth/Login.tsx",
    "resources/js/pages/Dashboard/Index.tsx",
    "resources/js/components/ui/button.tsx",
    "resources/css/app.css",
    "public/fastplace-logo.svg",
    "tsconfig.json",
    "routes/__init__.py",
    "routes/web.py",
    "routes/api.py",
    "routes/ai.py",
    "config/__init__.py",
    "config/app.py",
    "config/database.py",
    "config/ai.py",
    "config/auth.py",
    "public/.gitkeep",
    "storage/.gitkeep",
    "database/seeders/.gitkeep",
    ".env",
    ".env.example",
    ".gitignore",
    "index.html",
    "asgi.py",
    "pyproject.toml",
    "package.json",
    "vite.config.js",
    "README.md",
)


def _invoke(tmp_path: Path, monkeypatch, name: str):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_app, ["new", name])
    return result, tmp_path


def test_new_scaffolds_the_canonical_tree(tmp_path, monkeypatch):
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    project = root / "blog"
    for rel in _DIRECTORIES:
        assert (project / rel).is_dir(), f"missing directory {rel}"
    for rel in _FILES:
        assert (project / rel).is_file(), f"missing file {rel}"


def test_readme_names_the_settings_form_targets(tmp_path, monkeypatch):
    # The settings UI ships complete AND routed; the README must say so (and
    # still name the one genuinely parked target: passkeys) so the first
    # `fastplace migrate && run dev` session never mystifies.
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    readme = (root / "blog" / "README.md").read_text()
    for target in (
        "Settings flows",
        "PATCH",
        "/settings/profile",
        "/settings/password",
        "/user/passkeys",
    ):
        assert target in readme, f"README missing {target}"
    assert "Parked form targets" not in readme


def test_new_shared_kernel_init_docstring_has_one_period(tmp_path, monkeypatch):
    """_INIT_TEMPLATE already appends the period — a trailing dot in the doc
    argument used to render the shared-kernel docstring as ``classes..``."""
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    init = (root / "blog" / "app" / "models" / "__init__.py").read_text()
    assert init == '"""Shared base model classes."""\n'


def test_new_slugifies_the_project_name(tmp_path, monkeypatch):
    result, root = _invoke(tmp_path, monkeypatch, "My Blog App")
    assert result.exit_code == 0, result.output
    assert (root / "my-blog-app").is_dir()


def test_new_env_defaults_to_sqlite_and_generates_a_key(tmp_path, monkeypatch):
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    env = (root / "blog" / ".env").read_text()
    example = (root / "blog" / ".env.example").read_text()

    assert "sqlite+aiosqlite" in env
    assert "sqlite+aiosqlite" in example
    # .env carries a generated signing key; the committed example stays empty.
    key_line = next(line for line in env.splitlines() if line.startswith("APP_KEY="))
    assert len(key_line.removeprefix("APP_KEY=")) >= 32
    assert "APP_KEY=\n" in example or example.endswith("APP_KEY=")
    # Config defaults mirror the canonical project shape.
    assert 'APP_ENV = "local"' in (root / "blog" / "config" / "app.py").read_text()


def test_new_env_pins_session_and_cache_drivers(tmp_path, monkeypatch):
    """The scaffold must boot warn-free: run dev warns on memory sessions
    (every reload drops the store) and serve's multi-worker guard refuses
    them outright — so the default .env pins SESSION_DRIVER=database (the
    zero-config SQLite store) instead of leaving the implicit memory default."""
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    env = (root / "blog" / ".env").read_text()
    example = (root / "blog" / ".env.example").read_text()

    assert "SESSION_DRIVER=database" in env
    assert "SESSION_DRIVER=database" in example
    # Cache has no dev-time warning; the explicit line documents the knob a
    # production deploy must move off memory (serve refuses it there).
    assert "CACHE_DRIVER=memory" in env
    assert "CACHE_DRIVER=memory" in example


def test_new_env_example_carries_the_guide_documented_knobs(tmp_path, monkeypatch):
    """The guides tell users to set queue/redis/mail/broadcast/S3/log knobs
    in .env — the scaffolded .env.example must list them. It used to ship a
    ~20-key subset, so every switch the deployment and background-and-cache
    guides name was unfindable in the file the getting-started page points
    at. Every new entry stays commented: defaults keep living in config/*.py
    and a fresh project boots exactly as before. Mail is the one exception —
    the block ships active and byte-identical to make:auth's `_augment_env`
    append, whose "already present" guard then stays a true no-op instead of
    duplicating (or, with a commented block, silently skipping) the keys."""
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    env = (root / "blog" / ".env").read_text()
    example = (root / "blog" / ".env.example").read_text()

    for key in (
        "APP_HOST",  # bind interface (run dev: 127.0.0.1, serve: 0.0.0.0)
        "ASSET_VERSION",
        "PRERENDER_ROUTES",
        "QUEUE_DRIVER",
        "QUEUE_REDIS_URL",
        "QUEUE_TRIES",
        "QUEUE_TIMEOUT",
        "QUEUE_BACKOFF",
        "QUEUE_TTL",
        "QUEUE_DASHBOARD_ENABLED",
        "REDIS_URL",
        "BROADCAST_DRIVER",
        "S3_BUCKET",
        "LOG_LEVEL",
        "QUERY_SLOW_MS",
    ):
        assert f"{key}=" in example, f".env.example missing {key}"
        # Documented, never switched on: an uncommented line would land in
        # the generated .env and change how a fresh project boots.
        active = [line for line in env.splitlines() if line.startswith(f"{key}=")]
        assert active == [], f"{key} must ship commented, not active"

    # Mail: active exactly once, matching make:auth's own block verbatim.
    mail_lines = [line for line in env.splitlines() if line.startswith("MAIL_DRIVER=")]
    assert mail_lines == ["MAIL_DRIVER=log"]


def test_new_gitignore_covers_encrypted_env_variants(tmp_path, monkeypatch):
    """``fastplace key:rotate`` writes ``.env.encrypted`` and keeps
    ``.env.encrypted.bak`` on drift — a scaffold ignoring only ``.env`` and
    ``.env.bak`` leaves the ciphertext committable. The Environment block
    mirrors the framework repo's own: ``.env.*`` covers every variant,
    ``!.env.example`` keeps the starter template trackable."""
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    gitignore = (root / "blog" / ".gitignore").read_text()
    block = gitignore.split("# Environment", 1)[1].split("# OS", 1)[0]
    assert ".env.*" in block
    assert "!.env.example" in block


def test_new_refuses_a_non_empty_directory(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "blog"
    target.mkdir()
    (target / "keep.txt").write_text("precious")
    result = CliRunner().invoke(cli_app, ["new", "blog"])

    assert result.exit_code != 0
    assert (target / "keep.txt").read_text() == "precious"
    # Refusal happens before any scaffolding.
    assert not (target / "asgi.py").exists()


def test_new_refuses_unsafe_names(tmp_path, monkeypatch):
    result, _ = _invoke(tmp_path, monkeypatch, "../escape")
    assert result.exit_code != 0
    assert "name" in result.output.lower()


def test_new_prints_next_steps(tmp_path, monkeypatch):
    result, _ = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output
    for step in ("cd blog", "pip install", "npm install", "fastplace migrate", "fastplace run dev"):
        assert step in result.output


def test_new_web_route_serves_the_home_page(tmp_path, monkeypatch):
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    web = (root / "blog" / "routes" / "web.py").read_text()
    assert "router" in web
    assert "HomeController" in web


def test_scaffolded_project_boots(tmp_path, monkeypatch):
    """The strongest guarantee: ``create_app()`` imports routes/config and
    builds the ASGI app inside the generated tree, in a clean interpreter."""
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    project = root / "blog"

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastplace.http import create_app; app = create_app(); print('BOOT_OK', len(app.routes))",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "BOOT_OK" in proc.stdout


def test_new_preconfigures_the_migration_environment(tmp_path, monkeypatch):
    """``fastplace migrate`` must work on a fresh scaffold with no extra
    setup — the Alembic env comes pre-configured (like ``db:configure``)."""
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    project = root / "blog"

    assert (project / "database" / "migrations" / "env.py").is_file()
    assert (project / "database" / "migrations" / "script.py.mako").is_file()
    assert (project / "database" / "migrations" / "versions").is_dir()

    from fastplace.orm.migrations import MigrationsManager

    assert MigrationsManager(project).configured


def test_scaffolded_pyproject_declares_fastplace(tmp_path, monkeypatch):
    _, root = _invoke(tmp_path, monkeypatch, "blog")
    pyproject = (root / "blog" / "pyproject.toml").read_text()
    package_json = (root / "blog" / "package.json").read_text()
    vite_config = (root / "blog" / "vite.config.js").read_text()

    assert "fastplace" in pyproject
    assert '"build"' in package_json
    assert "resources/js/main.jsx" in vite_config


@pytest.mark.parametrize(
    "rel",
    [
        "routes/web.py",
        "routes/api.py",
        "asgi.py",
        "config/app.py",
        "config/database.py",
        "config/ai.py",
        "config/auth.py",
        "app/http/controllers/home_controller.py",
    ],
)
def test_scaffolded_python_parses(tmp_path, monkeypatch, rel):
    """Every generated Python file must at least be syntactically valid."""
    import ast

    _, root = _invoke(tmp_path, monkeypatch, "blog")
    source = (root / "blog" / rel).read_text()
    ast.parse(source)


def test_scaffolded_vite_config_is_valid_javascript(tmp_path, monkeypatch):
    """The vite template's doubled braces are for .format(); a missing format
    call ships literal {{ }} — invalid JS that kills `npm run dev`."""
    import shutil
    import subprocess

    _, root = _invoke(tmp_path, monkeypatch, "blog")
    vite = root / "blog" / "vite.config.js"
    if shutil.which("node") is None:  # pragma: no cover — dev/CI always has node
        source = vite.read_text()
        assert "{{" not in source and "}}" not in source
        return

    proc = subprocess.run(
        ["node", "--check", str(vite)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr


def test_new_refuses_when_target_exists_as_a_file(tmp_path, monkeypatch):
    """A plain file at the target must produce a clean refusal — not an
    unhandled NotADirectoryError traceback."""
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    (tmp_path / "blog").write_text("i am a file")
    result = CliRunner().invoke(cli_app, ["new", "blog"])

    assert result.exit_code != 0
    assert "blog" in result.output
    # A clean CLI error, not a crash: the exception is Typer's exit path.
    assert not isinstance(result.exception, NotADirectoryError)


def test_new_refuses_overlong_names(tmp_path, monkeypatch):
    """Names beyond filesystem component limits must fail cleanly up front."""
    result, _ = _invoke(tmp_path, monkeypatch, "a" * 300)
    assert result.exit_code != 0
    assert not isinstance(result.exception, OSError)


def test_new_dependency_carries_version_floor_when_installed(tmp_path, monkeypatch):
    """From a published install (no framework checkout on disk) the scaffolded
    app must pin a floor — ``fastplace[queue,webauthn]>=<version>`` — never a bare
    specifier: a bare dependency silently tracks future breaking releases,
    and the npm side caret-pins the same version (lockstep python/npm releases).
    The extras stay pinned so the passkey surface and the mail queue are importable as-is."""
    from fastplace.cli import generators

    monkeypatch.setattr(generators, "_framework_checkout", lambda: None)
    result, root = _invoke(tmp_path, monkeypatch, "blog")
    assert result.exit_code == 0, result.output

    pyproject = (root / "blog" / "pyproject.toml").read_text()
    assert '"fastplace[queue,webauthn]>=' in pyproject, pyproject
    # A bare specifier or a leaked local path is the regression.
    assert '"fastplace"' not in pyproject
    assert "file://" not in pyproject


def test_new_react_pin_tracks_the_fastplace_version(tmp_path, monkeypatch):
    """Pin symmetry (upg-G3): the npm caret pin derives from the running
    fastplace distribution — python and npm release in lockstep, so a
    scaffolded app can never mix a pip floor of one release with an npm
    caret of another."""
    import json
    from importlib.metadata import version

    from fastplace.cli import generators

    monkeypatch.setattr(generators, "_framework_checkout", lambda: None)
    _, root = _invoke(tmp_path, monkeypatch, "blog")

    package_json = json.loads((root / "blog" / "package.json").read_text())
    assert package_json["dependencies"]["@fastplace/react"] == f"^{version('fastplace')}"


def test_scaffolded_project_is_locally_installable(tmp_path, monkeypatch):
    """Running from the framework checkout, the generated project must wire
    local paths so both `pip install -e .` and `npm install` can resolve:
    a setuptools packages=[] block (flat-layout apps are not distributions)
    plus file: dependencies for the unpublished packages."""
    import fastplace

    checkout = Path(fastplace.__file__).resolve().parents[1]
    from_source_checkout = (checkout / "packages" / "react").is_dir()
    # The npm file: wiring requires a BUILT packages/react/dist (its entry
    # points live there); a bare source checkout falls back to the registry.
    dist_built = (checkout / "packages/react/dist/fastplace-react.js").is_file()

    _, root = _invoke(tmp_path, monkeypatch, "blog")
    pyproject = (root / "blog" / "pyproject.toml").read_text()
    package_json = (root / "blog" / "package.json").read_text()

    # The flat-layout discovery error ("Multiple top-level packages discovered")
    # is disabled explicitly — the app is not a distribution.
    assert "[tool.setuptools]" in pyproject
    if from_source_checkout:
        assert f"fastplace[queue,webauthn] @ file://{checkout}" in pyproject
    else:  # pragma: no cover — CI/dev always runs from the checkout
        assert '"fastplace[queue,webauthn]>=' in pyproject
    if from_source_checkout and dist_built:
        assert '"@fastplace/react": "file:' in package_json
    else:  # pragma: no cover — CI/dev always runs from the checkout
        from importlib.metadata import version

        pin = f'"@fastplace/react": "^{version("fastplace")}"'
        assert pin in package_json


def _invoke_with_input(tmp_path, monkeypatch, name: str, *args: str, input: str | None = None):
    from typer.testing import CliRunner

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli_app, ["new", name, *args], input=input)
    return result, tmp_path


def test_new_with_auth_flag_scaffolds_auth_files(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0
    assert (root / "blog" / "routes" / "auth.py").is_file()
    assert (root / "blog" / "app" / "auth" / "gates.py").is_file()


def test_new_with_no_auth_flag_matches_minimal_tree(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0
    assert not (root / "blog" / "routes" / "auth.py").exists()
    # The minimal tree is unchanged from before this feature.
    for rel in _FILES:
        assert (root / "blog" / rel).is_file(), f"missing {rel}"


def _corpus_files(corpus: Path) -> list[Path]:
    """The files `new` is expected to copy, under the generator's own noise
    filters (generators.py skips .DS_Store, .pyc, and __pycache__)."""
    return [
        p
        for p in corpus.rglob("*")
        if p.is_file()
        and ".DS_Store" not in p.name
        and p.suffix != ".pyc"
        and "__pycache__" not in p.parts
    ]


def test_new_carries_the_whole_starter_corpus(tmp_path, monkeypatch):
    # R2: every new app gets the complete frontend starter, verbatim — the
    # walk must not drop design-system files, tests, or public assets.
    from fastplace.cli.generators import scaffold_templates_dir

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0
    corpus = Path(scaffold_templates_dir())
    shipped = _corpus_files(corpus)
    assert shipped  # the corpus itself must never silently empty out
    for src in shipped:
        rel = src.relative_to(corpus)
        assert (root / "blog" / rel).is_file(), f"new did not write {rel}"


def test_corpus_parity_walk_skips_the_same_noise_as_the_generator(tmp_path, monkeypatch):
    """The parity walk and the generator must share one noise filter.

    A stale __pycache__/.pyc artifact inside the corpus is skipped by the
    generator (a .pyc read as text would crash the scaffold) — the walk
    must skip it too, not demand it back into the fresh app.
    """
    import shutil

    from fastplace.cli.generators import scaffold_templates_dir

    staged = tmp_path / "corpus"
    shutil.copytree(Path(scaffold_templates_dir()), staged)
    (staged / "__pycache__").mkdir()
    (staged / "__pycache__" / "stale.pyc").write_bytes(b"\x00noise")
    (staged / "stray.pyc").write_bytes(b"\x00noise")

    import fastplace.cli.generators as generators_mod

    monkeypatch.setattr(generators_mod, "scaffold_templates_dir", lambda: str(staged))

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0

    shipped = _corpus_files(staged)
    assert shipped
    for src in shipped:
        rel = src.relative_to(staged)
        assert (root / "blog" / rel).is_file(), f"new did not write {rel}"
    # And the noise never lands in the app.
    assert not (root / "blog" / "__pycache__").exists()
    assert not (root / "blog" / "stray.pyc").exists()


def test_new_package_json_carries_the_ui_stack(tmp_path, monkeypatch):
    import json

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0
    pkg = json.loads((root / "blog" / "package.json").read_text())
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    scripts = pkg.get("scripts", {})
    for wanted in (
        "@radix-ui/react-dialog",
        "@radix-ui/react-slot",
        "class-variance-authority",
        "clsx",
        "tailwind-merge",
        "lucide-react",
        "sonner",
        "input-otp",
        "tw-animate-css",
        "typescript",
        "vitest",
        "jsdom",
        "@testing-library/react",
        "@testing-library/jest-dom",
        "@testing-library/user-event",
        "@types/react",
        "eslint",
        "typescript-eslint",
        "prettier",
    ):
        assert wanted in deps, f"package.json missing {wanted}"
    for script in ("types", "test", "test:watch", "lint", "lint:check", "format", "format:check"):
        assert script in scripts, f"package.json scripts missing {script}"


def test_new_vite_config_has_js_alias_and_vitest(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0
    vite = (root / "blog" / "vite.config.js").read_text()
    assert '"@": path.resolve(__dirname, "resources/js")' in vite
    assert 'environment: "jsdom"' in vite
    assert "resources/js/**/__tests__/**/*.{test,spec}.{ts,tsx,js,jsx}" in vite


def test_new_index_html_has_prepaint(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0
    html = (root / "blog" / "index.html").read_text()
    assert "fastplace-appearance-prepaint" in html
    assert 'localStorage.getItem("fastplace-appearance")' in html


def test_new_prompts_for_auth_when_flag_absent(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", input="\n")  # Enter = yes
    assert result.exit_code == 0
    assert "authentication" in result.output.lower()
    assert (root / "blog" / "routes" / "auth.py").is_file()


def test_new_prompt_no_disables_auth(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", input="n\n")
    assert result.exit_code == 0
    assert not (root / "blog" / "routes" / "auth.py").exists()


def _boot(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from fastplace.http import create_app; app = create_app(); print('BOOT_OK', len(app.routes))",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_new_auth_project_boots(tmp_path, monkeypatch):
    """The auth variant must boot, not just exist: the kernel resolves
    ROUTE_MIDDLEWARE aliases eagerly at mount, so a missing registry (or one
    alias short) kills create_app() — the boot gap the sample app hit."""
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output
    proc = _boot(root / "blog")
    assert "BOOT_OK" in proc.stdout, proc.stderr


def test_new_auth_config_carries_middleware_registry(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output
    cfg = (root / "blog" / "config" / "app.py").read_text()
    assert "ROUTE_MIDDLEWARE" in cfg and '"guest"' in cfg and '"can"' in cfg
    assert "ResolveUserMiddleware" in cfg
    assert "SharedAbilitiesMiddleware" in cfg
    auth_cfg = (root / "blog" / "config" / "auth.py").read_text()
    assert '"driver": "orm"' in auth_cfg
    assert "app.modules.accounts.models.User" in auth_cfg


def test_new_auth_generates_users_migration(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output
    versions = root / "blog" / "database" / "migrations" / "versions"
    made = list(versions.glob("*create_users_table*.py"))
    assert made, "users migration missing"


def test_new_auth_provider_path_resolves_user(tmp_path, monkeypatch):
    """The ORM provider dotted path in the scaffolded config/auth.py —
    ``app.modules.accounts.models.User`` — must resolve in the generated
    tree. An empty models ``__init__`` passes create_app() (the provider
    import is lazy) and then 500s every authenticated request post-register."""
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.modules.accounts.models import User; print('USER_OK', User.__tablename__)",
        ],
        cwd=root / "blog",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "USER_OK users" in proc.stdout


def test_new_auth_leaves_no_scaffold_modules_cached(tmp_path, monkeypatch):
    """Autogenerating the users migration must not import the scaffold's
    modules into this process.

    env.py's model discovery imports every app.* module of the project it
    generates for. Run in-process, those modules stay cached in sys.modules
    and shadow the host project's own for the rest of the CLI process's
    life — `route:list` boots and every later model import misbehaves
    (duplicate declarative classes, wrong gates). The autogenerate step
    therefore runs in a subprocess.
    """
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None) if "app" in sys.modules else None
    leaked_before = {name for name in sys.modules if name.startswith("app.")}

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output

    leaked = {name for name in sys.modules if name.startswith("app.")} - leaked_before
    assert not leaked, f"scaffold modules leaked into sys.modules: {sorted(leaked)}"


def test_new_auth_emits_page_routes_and_controllers(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output
    for rel in (
        "app/http/controllers/auth_page_controller.py",
        "app/http/controllers/dashboard_controller.py",
        "app/http/controllers/settings_pages_controller.py",
        "app/http/controllers/settings_appearance_controller.py",
    ):
        assert (root / "blog" / rel).is_file(), f"missing {rel}"
    web = (root / "blog" / "routes" / "web.py").read_text()
    assert '"/register"' in web and '"guest"' in web
    assert '"/dashboard"' in web and '"verified"' in web
    assert '"/settings/profile"' in web


def test_new_auth_next_steps_mention_register(tmp_path, monkeypatch):
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output
    assert "/register" in result.output
    assert "admin" in result.output.lower()


def test_new_no_auth_keeps_minimal_web_routes(tmp_path, monkeypatch):
    """The page layer belongs to the auth variant only — --no-auth keeps the
    minimal two-route web.py and no extra controllers."""
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0, result.output
    assert not (root / "blog" / "app" / "http" / "controllers" / "auth_page_controller.py").exists()
    web = (root / "blog" / "routes" / "web.py").read_text()
    assert "/register" not in web and "/dashboard" not in web


# ---------------------------------------------------------------------------
# Installer UX — banner, named step groups, ready panel, --install
# ---------------------------------------------------------------------------


def test_new_prints_the_fastplace_banner(tmp_path, monkeypatch):
    """The installer opens with the FASTPLACE wordmark and a subtitle — the
    first thing on the wire, before any file work."""
    from fastplace.cli.generators import _BANNER

    assert len(_BANNER.splitlines()) == 7  # one row per block-letter line
    assert max(len(row) for row in _BANNER.splitlines()) <= 79  # survives an 80-col terminal

    result, _ = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0, result.output
    assert "█" in result.output, result.output[:80]
    assert "Fastplace application installer" in result.output


def test_new_groups_output_into_named_steps(tmp_path, monkeypatch):
    """Each phase runs under a ● header and closes with a ✓ result line."""
    result, _ = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0, result.output
    for line in (
        "● Creating application files",
        "✓ Application created",
        "● Preparing database",
        "✓ Migrations configured",
    ):
        assert line in result.output, result.output


def test_new_auth_step_reports_the_surface_and_users_table(tmp_path, monkeypatch):
    result, _ = _invoke_with_input(tmp_path, monkeypatch, "blog", "--auth")
    assert result.exit_code == 0, result.output
    for line in (
        "● Installing authentication",
        "✓ Authentication installed",
        "✓ Users table migration created",
    ):
        assert line in result.output, result.output


def test_new_prints_the_ready_panel(tmp_path, monkeypatch):
    """The finale is a boxed ready panel with the full manual setup when the
    dependencies were not installed."""
    result, _ = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-auth")
    assert result.exit_code == 0, result.output
    assert "Application ready" in result.output
    assert "Build something great!" in result.output
    for step in (
        "cd blog",
        "python -m venv .venv",
        "pip install",
        "npm install",
        "fastplace migrate",
    ):
        assert step in result.output, result.output


def test_new_install_prompt_defaults_to_no_on_enter(tmp_path, monkeypatch):
    """Two Enters at the prompts: auth yes (default), install no (default) —
    no venv appears and the manual panel still lists every step."""
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", input="\n\n")
    assert result.exit_code == 0, result.output
    assert "Install dependencies now" in result.output
    assert not (root / "blog" / ".venv").exists()
    assert "pip install" in result.output


def test_new_install_prompt_falls_back_to_no_on_closed_stdin(tmp_path, monkeypatch):
    """Pipes/CI close stdin: the install prompt skips (no hang, no crash) —
    the opposite of the auth prompt's YES fallback, deliberate: installing
    costs network and minutes, so unattended runs must not opt in."""
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog")  # no input
    assert result.exit_code == 0, result.output
    assert "skipping dependency install" in result.output
    assert not (root / "blog" / ".venv").exists()


def test_new_install_flag_runs_the_dependency_steps(tmp_path, monkeypatch):
    """--install hands the fresh project to _install_dependencies; success
    shortens the ready panel — no manual pip/npm/migrate steps remain."""
    from fastplace.cli import generators

    called: list[Path] = []

    def fake_install(target: Path) -> bool:
        called.append(target)
        return True

    monkeypatch.setattr(generators, "_install_dependencies", fake_install)

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--install")
    assert result.exit_code == 0, result.output
    assert called == [root / "blog"]
    assert "source .venv/bin/activate" in result.output
    assert "pip install" not in result.output
    assert "fastplace run dev" in result.output


def test_new_install_failure_keeps_the_manual_panel(tmp_path, monkeypatch):
    """A failed toolchain run must not print the shortened panel — the manual
    steps stay so the user can finish by hand."""
    from fastplace.cli import generators

    monkeypatch.setattr(generators, "_install_dependencies", lambda target: False)

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--install")
    assert result.exit_code == 0, result.output
    assert "pip install" in result.output
    assert "Project created — finish setup below" in result.output  # yellow panel title
    assert "Application ready" not in result.output


# ---------------------------------------------------------------------------
# _run_step_command / _install_dependencies — the --install toolchain
# ---------------------------------------------------------------------------


def test_run_step_command_reports_success_and_failure(tmp_path, monkeypatch):
    """The wrapper reports ✓/✗ with a short indented tail — success, non-zero
    exit (last three non-blank output lines), spawn OSError, and a timeout
    that kills the child and still returns instead of hanging."""
    from fastplace.cli import generators

    class FakeProc:
        def __init__(self, out="", err="", returncode=0):
            self.out, self.err, self.returncode = out, err, returncode
            self.pid = 4242
            self.killed = False

        def kill(self):
            self.killed = True

        def communicate(self, timeout=None):
            assert timeout is not None, "every drain must be bounded"
            return self.out, self.err

    monkeypatch.setattr(generators.subprocess, "Popen", lambda *a, **kw: FakeProc())
    ok, detail = generators._run_step_command(["x"], tmp_path, timeout=5)
    assert ok and detail == ""

    bad = FakeProc(err="one\n\nboom happened\nthree\nfour\nfive", returncode=3)
    monkeypatch.setattr(generators.subprocess, "Popen", lambda *a, **kw: bad)
    ok, detail = generators._run_step_command(["x"], tmp_path)
    assert not ok
    assert detail.splitlines() == ["    three", "    four", "    five"]  # last 3 non-blank
    assert "boom happened" not in detail  # earlier noise dropped

    def gone(*a, **kw):
        raise OSError("gone")

    monkeypatch.setattr(generators.subprocess, "Popen", gone)
    ok, detail = generators._run_step_command(["x"], tmp_path)
    assert not ok
    assert detail == "    gone"

    slow = FakeProc(err="partial death")

    def timeout_first_drain(timeout=None):
        if slow.killed:
            return slow.out, slow.err
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    slow.communicate = timeout_first_drain
    monkeypatch.setattr(generators.subprocess, "Popen", lambda *a, **kw: slow)
    ok, detail = generators._run_step_command(["x"], tmp_path, timeout=7)
    assert not ok
    assert slow.killed
    assert detail.startswith("    timed out after 7s"), detail
    assert "partial death" in detail


def test_install_dependencies_runs_the_full_toolchain(tmp_path, monkeypatch):
    """Success path: venv → pip install -e . → migrate → npm install → build,
    in that order, and True only when every step ran."""
    from fastplace.cli import generators

    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd, cwd, timeout):  # signature of _run_step_command's target
        calls.append((list(cmd), cwd))
        return True, ""

    monkeypatch.setattr(generators, "_run_step_command", fake_run)
    monkeypatch.setattr(generators.shutil, "which", lambda name: "/usr/bin/npm")

    target = tmp_path / "blog"
    assert generators._install_dependencies(target) is True
    commands = [cmd for cmd, _ in calls]
    assert all(cwd == target for _, cwd in calls)  # every step runs inside the new project
    heads = [Path(cmd[0]).name for cmd in commands]
    assert heads[0].startswith("python") and commands[0][1:4] == ["-m", "venv", ".venv"]
    assert commands[1][0] == str(target / ".venv" / "bin" / "python")
    assert commands[1][1:] == ["-m", "pip", "install", "-e", "."]
    assert commands[2][0] == str(target / ".venv" / "bin" / "fastplace")
    assert commands[2][1:] == ["migrate"]
    assert commands[3] == ["/usr/bin/npm", "install"]
    assert commands[4] == ["/usr/bin/npm", "run", "build"]


def test_install_dependencies_installs_the_dev_extra_when_defined(tmp_path, monkeypatch):
    """A project whose pyproject declares [project.optional-dependencies] (the
    auth scaffold ships a dev extra with pytest) gets it installed up front —
    day-one `.venv/bin/python -m pytest` must work without a second pip run.
    A project without the extra keeps the plain `-e .` install."""
    from fastplace.cli import generators

    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        calls.append(list(cmd))
        return True, ""

    monkeypatch.setattr(generators, "_run_step_command", fake_run)
    monkeypatch.setattr(generators.shutil, "which", lambda name: None)

    # Without the extra: plain editable install (toolchain stops at npm-missing).
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "plain"\ndependencies = ["fastplace"]\n'
    )
    assert generators._install_dependencies(tmp_path) is False  # npm missing on purpose
    assert calls[1][1:] == ["-m", "pip", "install", "-e", "."]

    # With the dev extra: same step now installs `.[dev]`.
    calls.clear()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastplace"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8.0"]\n'
    )
    assert generators._install_dependencies(tmp_path) is False  # npm missing on purpose
    assert calls[1][1:] == ["-m", "pip", "install", "-e", ".[dev]"]


def test_install_dependencies_stops_after_a_python_side_failure(tmp_path, monkeypatch):
    from fastplace.cli import generators

    commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        commands.append(list(cmd))
        if "-m" in cmd and "pip" in cmd:
            return False, "pip exploded"
        return True, ""

    monkeypatch.setattr(generators, "_run_step_command", fake_run)
    monkeypatch.setattr(generators.shutil, "which", lambda name: "/usr/bin/npm")

    assert generators._install_dependencies(tmp_path) is False
    assert len(commands) == 2  # venv + pip; migrate/npm never attempted


def test_install_dependencies_without_npm_reports_and_stays_false(tmp_path, monkeypatch):
    """No npm on PATH: the Python toolchain still runs (app is usable), but
    the result is not "everything installed" — the manual panel returns."""
    from fastplace.cli import generators

    commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        commands.append(list(cmd))
        return True, ""

    monkeypatch.setattr(generators, "_run_step_command", fake_run)
    monkeypatch.setattr(generators.shutil, "which", lambda name: None)

    assert generators._install_dependencies(tmp_path) is False
    assert len(commands) == 3  # venv, pip, migrate — npm side skipped


def test_install_dependencies_stops_after_an_npm_side_failure(tmp_path, monkeypatch):
    """The likeliest real-world break (no network, bad build): the npm side
    reports failure and nothing after the failing npm step runs."""
    from fastplace.cli import generators

    commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout):
        commands.append(list(cmd))
        if cmd[-2:] == ["run", "build"]:
            return False, "npm boom"
        return True, ""

    monkeypatch.setattr(generators, "_run_step_command", fake_run)
    monkeypatch.setattr(generators.shutil, "which", lambda name: "/usr/bin/npm")

    assert generators._install_dependencies(tmp_path) is False
    assert len(commands) == 5  # the failing build is the last command; nothing runs after


def test_new_no_install_flag_skips_the_prompt_entirely(tmp_path, monkeypatch):
    """--no-install pre-answers the ask: no prompt, no fallback notice, no
    .venv — the manual panel is the whole story."""
    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog", "--no-install")
    assert result.exit_code == 0, result.output
    assert "Install dependencies now" not in result.output
    assert "skipping dependency install" not in result.output
    assert not (root / "blog" / ".venv").exists()
    assert "pip install" in result.output


def test_new_users_migration_failure_stays_non_fatal(tmp_path, monkeypatch):
    """A broken users-table bootstrap must not kill the scaffold: the ✗ line
    prints with the error tail, the ready panel still arrives, exit stays 0."""
    from fastplace.cli import generators

    def broken_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="autogen broke")

    monkeypatch.setattr(generators.subprocess, "run", broken_run)
    monkeypatch.setattr(generators, "_install_dependencies", lambda target: True)

    result, _ = _invoke_with_input(tmp_path, monkeypatch, "blog")  # auth defaults to yes
    assert result.exit_code == 0, result.output
    assert "✗ Users table migration failed" in result.output
    assert "autogen broke" in result.output
    assert "Application ready" in result.output


def test_new_interrupt_at_a_tty_prompt_aborts(tmp_path, monkeypatch):
    """Ctrl+C at the auth prompt on a real terminal must abort the command —
    only the non-tty case (pipes, CI) falls back to a default."""
    from fastplace.cli import generators

    class FakeStdin:
        def isatty(self):
            return True

    monkeypatch.setattr(
        generators.typer,
        "confirm",
        lambda *a, **kw: (_ for _ in ()).throw(typer.exceptions.Abort()),
    )
    monkeypatch.setattr(generators, "sys", types.SimpleNamespace(stdin=FakeStdin()))

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog")
    assert result.exit_code != 0
    assert "Aborted" in result.output
    assert not (root / "blog").exists()  # nothing was written before the interrupt


def test_new_interrupt_at_the_install_tty_prompt_aborts(tmp_path, monkeypatch):
    """Same contract at the install prompt: tty Abort re-raises, skipping the
    install while keeping the files already scaffolded."""
    from fastplace.cli import generators

    class FakeStdin:
        def isatty(self):
            return True

    calls = {"n": 0}

    def confirm(prompt, default):
        calls["n"] += 1
        if calls["n"] == 1:
            return default  # auth prompt answered yes; the interrupt lands on install
        raise typer.exceptions.Abort()

    monkeypatch.setattr(generators.typer, "confirm", confirm)
    monkeypatch.setattr(generators, "sys", types.SimpleNamespace(stdin=FakeStdin()))

    result, root = _invoke_with_input(tmp_path, monkeypatch, "blog")
    assert result.exit_code != 0
    assert "Aborted" in result.output
    assert not (root / "blog" / ".venv").exists()
