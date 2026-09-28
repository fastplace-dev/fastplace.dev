"""CI workflow contract — the compatibility matrix stays wired.

The blueprint's enforcement mechanism ("portable suites run against every
relational backend in CI") lives in .github/workflows/ci.yml. These tests
pin the wiring so a rename or a dropped service cannot silently narrow the
matrix back to sqlite-only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # runtime dep of the framework already

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def ci() -> dict:
    assert WORKFLOW.exists(), "CI workflow must exist at .github/workflows/ci.yml"
    return yaml.safe_load(WORKFLOW.read_text())


def _run_steps(ci: dict, job: str) -> list[str]:
    steps = ci["jobs"][job]["steps"]
    return " ".join(str(step.get("run", "")) for step in steps)


def test_ci_triggers_on_push_and_pr(ci):
    on = ci[True] if True in ci else ci["on"]  # YAML 1.1 parses `on:` as True
    assert "push" in on and "pull_request" in on


def test_boundary_job_gates_the_matrix_and_runs_first(ci):
    jobs = ci["jobs"]
    assert "boundary" in jobs
    assert "lint:modules" in _run_steps(ci, "boundary")
    # Everything that executes tests waits for the boundary gate.
    for name in ("backend", "frontend"):
        assert "boundary" in jobs[name]["needs"]


def test_backend_job_services_every_dialect_database(ci):
    services = ci["jobs"]["backend"]["services"]
    assert set(services) == {"postgres", "mysql", "mongo"}
    # Real pgvector image — the PostgreSQL dialect suite asserts VECTOR columns.
    assert services["postgres"]["image"].startswith("pgvector/pgvector:")


def test_backend_job_exports_the_env_gated_suite_urls(ci):
    env = ci["jobs"]["backend"]["env"]
    assert env["TEST_POSTGRES_URL"].startswith("postgresql://")
    assert env["TEST_MYSQL_URL"].startswith("mysql://")
    assert env["TEST_MONGODB_URL"].startswith("mongodb://")


def test_backend_job_enables_pgvector_and_runs_pytest(ci):
    script = _run_steps(ci, "backend")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in script
    assert "pytest" in script


def test_quality_job_runs_ruff_and_mypy(ci):
    script = _run_steps(ci, "quality")
    assert "ruff check ." in script
    assert "ruff format --check ." in script
    assert "mypy fastplace packages/tenancy/src" in script


def test_tenancy_package_is_installed_by_backend_and_quality(ci):
    """The sibling distribution must be installed wherever its suite or its
    types run — otherwise the tenancy tests collect nothing silently."""
    for job in ("backend", "quality"):
        script = _run_steps(ci, job)
        assert "pip install -e ./packages/tenancy" in script, f"{job} misses tenancy"


def test_frontend_job_runs_the_full_gate_set(ci):
    script = _run_steps(ci, "frontend")
    for gate in (
        "npm run types",
        "npm run lint:check",
        "npm run format:check",
        "npm run test:run",
        "npm run docs:build",  # the docs site builds in CI — rot is a red build
    ):
        assert gate in script


def test_e2e_job_runs_playwright_after_tests(ci):
    jobs = ci["jobs"]
    assert set(jobs["e2e"]["needs"]) == {"backend", "frontend"}
    assert "npx playwright test" in _run_steps(ci, "e2e")


def test_release_smoke_job_boots_a_scaffold_from_the_built_wheel(ci):
    """upg-G1: the release gate — build the wheel, install it into a clean
    venv, then scaffold, boot, and test an app from THAT artifact. Catches a
    wheel the starter kit cannot boot from before it reaches PyPI, not after
    (the 0.1.0 gap: broken published kits discovered by users)."""
    jobs = ci["jobs"]
    assert "release-smoke" in jobs
    assert jobs["release-smoke"]["needs"] == ["boundary", "backend"]
    script = _run_steps(ci, "release-smoke")
    for needle in (
        "python -m build",
        "twine check",
        "python -m venv /tmp/smoke",
        "dist/fastplace-*.whl",  # installs the BUILT wheel, never the checkout
        "fastplace new demo --auth --no-install",
        "from asgi import app",
        'assert "/tmp/smoke" in str(p)',  # fastplace resolves from the venv
        "pytest",
    ):
        assert needle in script, needle
    # The app's own deps (email-validator for the auth scaffold's EmailStr,
    # the queue/webauthn extras) must be installed BEFORE the boot assert —
    # a boot ahead of the editable install crashes on the first EmailStr
    # model import, a state no real user ever runs.
    assert script.index("pip install -e") < script.index("from asgi import app")


def test_no_untrusted_event_text_flows_into_run_scripts(ci):
    """Run steps must interpolate only our own env — never event fields
    (titles, bodies, branch names) that could inject shell text."""
    for name, job in ci["jobs"].items():
        for step in job.get("steps", []):
            run = str(step.get("run", ""))
            assert "github.event" not in run, f"{name} interpolates event text"
