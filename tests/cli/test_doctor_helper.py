# tests/cli/test_doctor_helper.py
"""The shared doctor check-runner foundation (roadmap Task 0)."""

import io
import os

import pytest
from rich.console import Console

from fastplace.cli._doctor import Check, run_checks


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them.

    CLI commands bootstrap config via load_env(), and python-dotenv writes
    the cwd .env's keys straight into the REAL os.environ — a mutation no
    monkeypatch sees or undoes. Snapshot before, restore after (verbatim
    pattern from tests/cli/test_cache_cmds.py:26-41).
    """
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


def _capture() -> Console:
    buffer = io.StringIO()
    return Console(file=buffer, width=200, force_terminal=False)


def _out(c: Console) -> str:
    return str(c.file.getvalue())


def test_pass_only_renders_and_exits_zero():
    c = _capture()
    code = run_checks("all good", [lambda: Check("one", "pass", "fine")], console=c)
    assert code == 0
    assert "one" in _out(c) and "PASS" in _out(c)


def test_warn_alone_exits_zero():
    c = _capture()
    code = run_checks("warn", [lambda: Check("w", "warn", "hmm")], console=c)
    assert code == 0
    assert "WARN" in _out(c)


def test_any_fail_exits_one():
    c = _capture()
    code = run_checks("bad", [lambda: Check("ok", "pass"), lambda: Check("bad", "fail", "boom")], console=c)
    assert code == 1
    assert "FAIL" in _out(c)


def test_list_check_spreads_into_rows():
    c = _capture()
    code = run_checks(
        "spread",
        [lambda: [Check("a", "pass"), Check("b", "warn", "note")]],
        console=c,
    )
    assert code == 0
    out = _out(c)
    assert "a" in out and "b" in out


def test_raising_check_becomes_fail_row_and_rest_still_runs():
    c = _capture()
    ran = []

    def _boom():
        raise RuntimeError("boom")

    def _fine():
        ran.append(1)
        return Check("after", "pass")

    code = run_checks("raise", [_boom, _fine], console=c)
    assert code == 1
    out = _out(c)
    assert "RuntimeError: boom" in out
    assert ran == [1]


def test_fallback_name_strips_underscore_prefix():
    c = _capture()

    def _check_env_parse():
        raise RuntimeError("x")

    code = run_checks("name", [_check_env_parse], console=c)
    assert code == 1
    assert "env_parse" in _out(c)


def test_empty_check_list_exits_zero():
    c = _capture()
    assert run_checks("empty", [], console=c) == 0


def test_summary_line_counts():
    c = _capture()
    run_checks(
        "mixed",
        [
            lambda: Check("p1", "pass"),
            lambda: [Check("p2", "pass"), Check("w1", "warn")],
            lambda: Check("f1", "fail", "x"),
        ],
        console=c,
    )
    assert "2 pass" in _out(c) and "1 warn" in _out(c) and "1 fail" in _out(c)


def test_fix_hint_rendered_dim():
    c = _capture()
    run_checks("fix", [lambda: Check("k", "fail", "broken", fix="run: fastplace key:generate")], console=c)
    assert "run: fastplace key:generate" in _out(c)


def test_default_console_path_smoke(capsys):
    code = run_checks("default console", [lambda: Check("d", "pass")])
    assert code == 0
    assert "d" in capsys.readouterr().out
