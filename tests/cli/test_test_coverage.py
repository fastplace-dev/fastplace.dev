# tests/cli/test_test_coverage.py
"""`test:coverage` forwarding + JSON gate (roadmap spec #15)."""

import json
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

    test:coverage composes the child env from os.environ after load_env()
    has leaked the cwd .env into it — a mutation no monkeypatch sees or
    undoes. Snapshot before, restore after (verbatim pattern from
    tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so long lines never wrap mid-assertion.

    Under CliRunner the shared console falls back to 80 columns and a
    module path or gate message would split across lines (verbatim
    pattern from tests/cli/test_test_matrix.py).
    """
    monkeypatch.setenv("COLUMNS", "200")


COV_JSON = {
    "totals": {"percent_covered": 61.5},
    "files": {
        "fastplace/mail/transports.py": {"summary": {"percent_covered": 10.0}},
        "fastplace/cli/keys.py": {"summary": {"percent_covered": 42.0}},
        "fastplace/http/kernel.py": {"summary": {"percent_covered": 88.0}},
    },
}


class _Proc:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _make_project(tmp_path, monkeypatch, cov_json=None):
    (tmp_path / "asgi.py").write_text("")
    if cov_json is not None:
        storage = tmp_path / "storage"
        storage.mkdir(exist_ok=True)
        (storage / "coverage.json").write_text(json.dumps(cov_json))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_test_coverage_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "test:coverage" in result.stdout


def test_forwards_cov_argv_and_renders_table(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, COV_JSON)
    calls: list = []

    def _fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return _Proc()

    monkeypatch.setattr(testing, "_subprocess_run", _fake_run)
    result = runner.invoke(cli_app, ["test:coverage"])
    assert result.exit_code == 0, result.stdout
    argv = calls[0][0]
    assert "--cov=fastplace" in argv
    assert any(a.startswith("--cov-report=json:") and a.endswith("coverage.json") for a in argv)
    out = _out(result)
    # least-covered first: transports before keys before kernel
    assert out.index("transports.py") < out.index("keys.py") < out.index("kernel.py")
    assert "61.5" in out  # totals line


def test_min_satisfied_exits_zero(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, COV_JSON)
    monkeypatch.setattr(testing, "_subprocess_run", lambda argv, **kw: _Proc())
    result = runner.invoke(cli_app, ["test:coverage", "--min", "60"])
    assert result.exit_code == 0, result.stdout


def test_min_shortfall_exits_one(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, COV_JSON)
    monkeypatch.setattr(testing, "_subprocess_run", lambda argv, **kw: _Proc())
    result = runner.invoke(cli_app, ["test:coverage", "--min", "90"])
    assert result.exit_code == 1
    out = _out(result)
    assert "90" in out and "61.5" in out  # threshold and actual both shown


def test_child_failure_propagates_exit_code(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, COV_JSON)
    monkeypatch.setattr(testing, "_subprocess_run", lambda argv, **kw: _Proc(returncode=3))
    result = runner.invoke(cli_app, ["test:coverage"])
    assert result.exit_code == 3


def test_missing_json_after_run_fails_cleanly(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch)  # no coverage.json written
    monkeypatch.setattr(testing, "_subprocess_run", lambda argv, **kw: _Proc())
    result = runner.invoke(cli_app, ["test:coverage"])
    assert result.exit_code == 1
    assert "coverage.json" in _out(result)
