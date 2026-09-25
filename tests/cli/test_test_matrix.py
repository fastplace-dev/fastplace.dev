"""`test:matrix` disposable-container ORM suite runner (roadmap spec #13)."""

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    test:matrix composes child-process env from os.environ, and config
    bootstrap writes the cwd .env's keys into the REAL os.environ — a
    mutation no monkeypatch sees or undoes. Snapshot before, restore
    after (verbatim pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    for key in ("TEST_POSTGRES_URL", "TEST_MYSQL_URL", "TEST_MONGODB_URL"):
        os.environ.pop(key, None)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich table width so detail cells never wrap mid-assertion.

    The command renders through the shared global console; under CliRunner
    it falls back to 80 columns and phrases like "defaults only" split
    across cell lines. COLUMNS is read live per render, so pinning it here
    makes every table one-line-per-cell (monkeypatch restores it).
    """
    monkeypatch.setenv("COLUMNS", "200")


class _Proc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeDocker:
    """Records every child invocation; answers from canned patterns."""

    def __init__(self, health_failures=0):
        self.calls: list[list] = []
        self.health_failures = health_failures

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if argv[0:2] == ["docker", "--version"]:
            return _Proc(stdout="Docker version 24.0.0\n")
        if argv[0:2] == ["docker", "run"]:
            return _Proc(stdout="deadbeefcafe\n")
        if argv[0:2] == ["docker", "exec"]:
            if "pg_isready" in joined or "mysqladmin" in joined or "mongosh" in joined:
                if self.health_failures > 0:
                    self.health_failures -= 1
                    return _Proc(returncode=1)
                return _Proc(stdout="1\n")
            return _Proc()
        if argv[0:2] == ["docker", "port"]:
            return _Proc(stdout="127.0.0.1:49153\n")
        if argv[0] == "pytest-marker" or argv[1:3] == ["-m", "pytest"]:
            return _Proc(stdout="10 passed\n")
        return _Proc()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(testing, "_sleep", lambda seconds: None)


@pytest.fixture
def drivers_ok(monkeypatch):
    monkeypatch.setattr(testing, "_driver_available", lambda module: True)


@pytest.fixture
def pgvector_ok(monkeypatch):
    """Record _ensure_pgvector calls instead of touching asyncpg."""
    seen: list[str] = []

    async def _fake(url: str) -> None:
        seen.append(url)

    monkeypatch.setattr(testing, "_ensure_pgvector", _fake)
    return seen


def test_test_matrix_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "test:matrix" in result.stdout


def test_matrix_missing_docker_exits_with_guidance(monkeypatch):
    def _no_docker(argv, **kwargs):
        return _Proc(returncode=127)

    monkeypatch.setattr(testing, "_subprocess_run", _no_docker)
    result = runner.invoke(cli_app, ["test:matrix", "--backends", "postgres"])
    assert result.exit_code == 1
    assert "docker" in _out(result).lower()


def test_matrix_single_backend_invokes_scoped_pytest(
    monkeypatch, no_sleep, drivers_ok, pgvector_ok
):
    fake = _FakeDocker()
    monkeypatch.setattr(testing, "_subprocess_run", fake)
    result = runner.invoke(cli_app, ["test:matrix", "--backends", "postgres"])
    assert result.exit_code == 0, result.stdout
    pytest_calls = [c for c in fake.calls if c[1:3] == ["-m", "pytest"]]
    assert len(pytest_calls) == 1
    assert "tests/orm/portable" in pytest_calls[0]
    assert "tests/orm/postgresql" in pytest_calls[0]
    run_calls = [c for c in fake.calls if c[0:2] == ["docker", "run"]]
    assert any("pgvector/pgvector:pg16" in " ".join(c) for c in run_calls)
    assert pgvector_ok and "49153" in pgvector_ok[0]  # extension ran against the mapped URL
    teardown = [c for c in fake.calls if c[0:2] == ["docker", "rm"]]
    assert teardown and "-f" in teardown[0]


def test_matrix_driver_missing_fails_fast(monkeypatch, no_sleep):
    fake = _FakeDocker()
    monkeypatch.setattr(testing, "_subprocess_run", fake)
    monkeypatch.setattr(testing, "_driver_available", lambda module: False)
    result = runner.invoke(cli_app, ["test:matrix", "--backends", "mysql"])
    assert result.exit_code == 1
    out = _out(result)
    assert "asyncmy" in out and "pip install" in out
    assert not [c for c in fake.calls if c[0:2] == ["docker", "run"]]  # never started


def test_matrix_mysql_health_patience(monkeypatch, no_sleep, drivers_ok, pgvector_ok):
    fake = _FakeDocker(health_failures=2)  # first two polls fail, third succeeds
    monkeypatch.setattr(testing, "_subprocess_run", fake)
    result = runner.invoke(cli_app, ["test:matrix", "--backends", "mysql"])
    assert result.exit_code == 0, result.stdout
    assert any("mysql:8" in " ".join(c) for c in fake.calls if c[0:2] == ["docker", "run"])


def test_matrix_keep_skips_teardown_and_prints_url(monkeypatch, no_sleep, drivers_ok, pgvector_ok):
    fake = _FakeDocker()
    monkeypatch.setattr(testing, "_subprocess_run", fake)
    result = runner.invoke(cli_app, ["test:matrix", "--backends", "postgres", "--keep"])
    assert result.exit_code == 0, result.stdout
    assert not [c for c in fake.calls if c[0:2] == ["docker", "rm"]]
    assert "49153" in _out(result)  # connection URL surfaced for the kept container


def test_matrix_exported_url_runs_against_existing_stack(
    monkeypatch, no_sleep, drivers_ok, pgvector_ok
):
    monkeypatch.setenv("TEST_MYSQL_URL", "mysql+asyncmy://existing:3306/db")
    fake = _FakeDocker()
    monkeypatch.setattr(testing, "_subprocess_run", fake)
    result = runner.invoke(cli_app, ["test:matrix"])
    assert result.exit_code == 0, result.stdout
    assert not [c for c in fake.calls if c[0:2] == ["docker", "run"]]  # stack exists: no container
    assert len([c for c in fake.calls if c[1:3] == ["-m", "pytest"]]) == 1


def test_matrix_backend_failure_exits_one_and_still_cleans_up(
    monkeypatch, no_sleep, drivers_ok, pgvector_ok
):
    class _FailingPytest(_FakeDocker):
        def __call__(self, argv, **kwargs):
            proc = super().__call__(argv, **kwargs)
            if argv[1:3] == ["-m", "pytest"]:
                proc.returncode = 1
            return proc

    fake = _FailingPytest()
    monkeypatch.setattr(testing, "_subprocess_run", fake)
    result = runner.invoke(cli_app, ["test:matrix", "--backends", "postgres"])
    assert result.exit_code == 1
    assert [c for c in fake.calls if c[0:2] == ["docker", "rm"]]  # teardown despite failure
