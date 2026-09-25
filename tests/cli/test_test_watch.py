# tests/cli/test_test_watch.py
"""`test:watch` affected-suite re-run on save (roadmap spec #16)."""

import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import testing as testing_mod

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    test:watch bootstraps config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
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


@pytest.fixture(autouse=True)
def _fake_pytest(monkeypatch):
    """No real pytest child ever runs: stub the module-level seam."""
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return _Proc()

    monkeypatch.setattr(testing_mod, "_subprocess_run", fake_run)
    return calls


def _make_project(tmp_path, monkeypatch):
    (tmp_path / "asgi.py").write_text("")
    (tmp_path / ".env").write_text("APP_ENV=local\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_test_watch_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "test:watch" in result.stdout


# --- pure helpers -----------------------------------------------------------


def test_relevant_change_accepts_test_and_source_trees():
    assert testing_mod._relevant_change("tests/cli/test_x.py") is True
    assert testing_mod._relevant_change("app/http/foo.py") is True
    assert testing_mod._relevant_change("fastplace/cli/testing.py") is True
    assert testing_mod._relevant_change("routes/web.py") is True


def test_relevant_change_rejects_non_python_and_outside():
    assert testing_mod._relevant_change("package.json") is False
    assert testing_mod._relevant_change("docs/guide.md") is False
    assert testing_mod._relevant_change("e2e/auth-reset.spec.ts") is False
    assert testing_mod._relevant_change("node_modules/pkg/x.py") is False


def test_watch_dirs_filters_missing_and_never_repo_root(tmp_path):
    (tmp_path / "tests").mkdir()
    # app/ absent here — filtered out, not fatal
    dirs = testing_mod._watch_dirs(tmp_path)
    assert dirs == [tmp_path / "tests"]
    assert tmp_path not in dirs  # repo root NEVER watched (thousands of handles)


def test_watch_dirs_all_present(tmp_path):
    for name in ("tests", "app", "fastplace", "routes"):
        (tmp_path / name).mkdir()
    dirs = testing_mod._watch_dirs(tmp_path)
    assert sorted(d.name for d in dirs) == ["app", "fastplace", "routes", "tests"]


def test_suite_for_test_file_targets_that_file():
    argv = testing_mod._suite_for(Path("tests/cli/test_x.py"))
    assert argv == ["tests/cli/test_x.py"]


def test_suite_for_source_runs_whole_suite_with_x():
    argv = testing_mod._suite_for(Path("app/http/foo.py"))
    assert argv == ["-x"]


# --- --once door (single pass; the watchfiles loop is never test-driven) -----


def test_once_runs_initial_pass_and_exits(tmp_path, monkeypatch, _fake_pytest):
    _make_project(tmp_path, monkeypatch)
    result = runner.invoke(cli_app, ["test:watch", "--once"])
    assert result.exit_code == 0, result.stdout
    assert len(_fake_pytest) == 1
    argv = _fake_pytest[0]
    assert argv[1:3] == ["-m", "pytest"]


def test_once_child_failure_propagates_exit_code(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)

    class _Fail:
        returncode = 2

    monkeypatch.setattr(testing_mod, "_subprocess_run", lambda *a, **k: _Fail())
    result = runner.invoke(cli_app, ["test:watch", "--once"])
    assert result.exit_code == 2


def test_help_documents_watch_behavior():
    result = runner.invoke(cli_app, ["test:watch", "--help"])
    assert result.exit_code == 0
    out = _out(result)
    assert "--once" in out
