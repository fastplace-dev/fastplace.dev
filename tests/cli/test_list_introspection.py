"""``fastplace event:list`` / ``module:list`` / ``gate:list`` (spec #25–#27)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.events import listen, reset_listeners
from tests.cli._isolation import park_app_modules

# Rich colorizes when the environment forces color; strip codes before matching.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[2]


def record_signup(event):
    """Listener for the event registry tests (module-level for a stable name)."""


def notify_team(event):
    """Second listener on the same event (one row per handler)."""


@pytest.fixture
def fresh_project(tmp_path, monkeypatch):
    """A freshly scaffolded project cwd; env/config and the app.* slice restored.

    ``gate:list`` and ``event:list`` import the fixture project's own modules
    (auth gates, job listeners), which also evicts foreign cached ``app.*``
    entries — ``park_app_modules`` restores the pre-test slice so later
    suites keep finding theirs cached.
    """
    cwd_before = Path.cwd().resolve()
    env_before = dict(os.environ)

    monkeypatch.chdir(tmp_path)
    # --no-auth keeps the minimal tree these empty-state assertions expect.
    result = runner.invoke(cli_app, ["new", "blog", "--no-auth"])
    assert result.exit_code == 0, result.output

    root = tmp_path / "blog"
    # The scaffold's app/ is a namespace package; a regular package anywhere
    # else on sys.path (this repo's own app/) would win resolution and shadow
    # the fixture project's jobs/gates. Make it regular — the same workaround
    # tests/cli/test_route_list.py applies.
    (root / "app" / "__init__.py").write_text("")
    monkeypatch.chdir(root)
    try:
        with park_app_modules():
            yield root
    finally:
        os.environ.clear()
        os.environ.update(env_before)
        from fastplace.config import reset_config

        reset_config(cwd_before)


@pytest.fixture
def repo_project(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    return REPO_ROOT


@pytest.fixture
def clean_event_registry():
    reset_listeners()
    yield
    reset_listeners()


@pytest.fixture
def clean_gate_registry():
    from fastplace.authz.gate import gate

    gate.reset_shared()
    yield
    gate.reset_shared()


def _run(*args):
    result = runner.invoke(cli_app, list(args))
    return result.exit_code, ANSI_RE.sub("", result.output)


# --- events registry accessor (framework unit test) -------------------------


def test_registered_listeners_maps_events_to_sorted_handler_names(clean_event_registry):
    from fastplace.events import registered_listeners

    listen("signup.recorded", record_signup)
    listen("signup.recorded", notify_team)
    listen("invoice.paid", record_signup)

    names = registered_listeners()
    assert set(names) == {"signup.recorded", "invoice.paid"}
    assert len(names["signup.recorded"]) == 2
    assert names["signup.recorded"] == sorted(names["signup.recorded"])
    for handlers in names.values():
        for name in handlers:
            assert name.endswith(("record_signup", "notify_team"))


def test_registered_listeners_empty_when_nothing_listens(clean_event_registry):
    from fastplace.events import registered_listeners

    assert registered_listeners() == {}


# --- gate registry accessors (framework unit tests) --------------------------


def test_registered_abilities_lists_defined_gates_sorted(clean_gate_registry):
    from fastplace.authz.gate import gate

    @gate.define("publish-posts")
    async def publish(user, post):
        return True

    @gate.define("delete-posts")
    async def delete(user, post):
        return True

    assert gate.registered_abilities() == ["delete-posts", "publish-posts"]


def test_registered_abilities_empty_after_reset(clean_gate_registry):
    from fastplace.authz.gate import gate

    assert gate.registered_abilities() == []


def test_registered_policies_lists_bound_model_names(clean_gate_registry):
    from fastplace.authz.gate import gate

    class FakePost:
        pass

    class PostPolicy:
        pass

    gate.policy(FakePost, PostPolicy)
    assert gate.registered_policies() == {"FakePost": "PostPolicy"}


# --- event:list --------------------------------------------------------------


def test_event_list_shows_in_process_listeners(fresh_project, clean_event_registry):
    listen("signup.recorded", record_signup)
    code, out = _run("event:list")
    assert code == 0, out
    assert "signup.recorded" in out
    assert "record_signup" in out


def test_event_list_empty_state(fresh_project, clean_event_registry):
    code, out = _run("event:list")
    assert code == 0, out
    assert "no event listeners" in out


def test_event_list_imports_the_projects_job_listeners(fresh_project, clean_event_registry):
    (fresh_project / "app" / "jobs" / "notify.py").write_text(
        "from fastplace.events import listen\n\n"
        "async def send_welcome(user_id: int):\n"
        "    return None\n\n"
        "listen('user.registered', send_welcome)\n"
    )
    code, out = _run("event:list")
    assert code == 0, out
    assert "user.registered" in out
    assert "send_welcome" in out


def test_event_list_degrades_gracefully_when_project_import_fails(
    fresh_project, clean_event_registry
):
    (fresh_project / "app" / "jobs" / "broken.py").write_text("raise RuntimeError('boom')\n")
    listen("invoice.paid", record_signup)
    code, out = _run("event:list")
    assert code == 0, out
    assert "project import unavailable" in out
    assert "invoice.paid" in out  # the in-process registry view still shows
    assert "record_signup" in out


# --- module:list -------------------------------------------------------------


def test_module_list_shows_repo_modules_and_csr_layers(repo_project):
    code, out = _run("module:list")
    assert code == 0, out
    for name in ("accounts", "knowledge", "projects", "about", "dashboard"):
        assert name in out
    assert "models" in out  # CSR layer columns are named
    assert "repositories" in out
    assert "services" in out


def test_module_list_empty_state(fresh_project):
    code, out = _run("module:list")
    assert code == 0, out
    assert "no bounded modules" in out


# --- gate:list ---------------------------------------------------------------


def test_gate_list_shows_in_process_abilities(fresh_project, clean_gate_registry):
    from fastplace.authz.gate import gate

    @gate.define("publish-posts")
    async def publish(user, post):
        return True

    code, out = _run("gate:list")
    assert code == 0, out
    assert "publish-posts" in out


def test_gate_list_reads_the_project_gates_file(fresh_project, clean_gate_registry):
    gates = fresh_project / "app" / "auth" / "gates.py"
    gates.parent.mkdir(parents=True, exist_ok=True)
    gates.write_text(
        "from fastplace.authz import gate\n\n"
        "@gate.define('delete-posts')\n"
        "async def delete_posts(user, post):\n"
        "    return True\n"
    )
    code, out = _run("gate:list")
    assert code == 0, out
    assert "delete-posts" in out
    # the loader caches the module under its canonical dotted name;
    # park_app_modules' teardown sweeps the adopted app.* entry away.


def test_gate_list_empty_state(fresh_project, clean_gate_registry):
    code, out = _run("gate:list")
    assert code == 0, out
    assert "no gate abilities" in out


# --- outside-project guards --------------------------------------------------


@pytest.mark.parametrize("command", [["event:list"], ["module:list"], ["gate:list"]])
def test_list_commands_outside_a_project_fail_friendly(tmp_path, monkeypatch, command):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, command)
    assert result.exit_code == 1
    assert "not inside a Fastplace project" in ANSI_RE.sub("", result.output)
