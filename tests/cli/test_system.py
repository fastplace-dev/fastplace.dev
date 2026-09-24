# tests/cli/test_system.py
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli.system import iter_command_names

runner = CliRunner()


def test_list_raw_includes_known_commands():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    names = result.stdout.splitlines()
    assert "migrate" in names
    assert "make:model" in names
    assert "queue:work" in names


def test_list_grouped_shows_namespace_rows():
    result = runner.invoke(cli_app, ["list"])
    assert result.exit_code == 0
    assert "make" in result.stdout  # namespace column exists


def test_env_prints_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    result = runner.invoke(cli_app, ["env"])
    assert result.exit_code == 0
    assert "testing" in result.stdout


def test_iter_command_names_flat_and_nested():
    names = [n for n, _ in iter_command_names(cli_app)]
    assert "about" in names and "make:model" in names


# Frozen Tier-1 command names from the CLI-completion spec (command table #1-#33).
# This meta-test guards `fastplace list --raw` completeness: every Tier-1 command
# must stay registered. If this goes red, a command failed to register — a real
# defect — do not trim this list to make it pass.
TIER_1_COMMANDS = [
    "list",
    "env",
    "route:list",
    "config:show",
    "migrate:status",
    "migrate:reset",
    "migrate",
    "db:seed",
    "db:wipe",
    "make:seeder",
    "make:job",
    "make:request",
    "make:middleware",
    "make:policy",
    "make:test",
    "make:scope",
    "make:config",
    "make:mail",
    "make:class",
    "make:enum",
    "make:exception",
    "make:interface",
    "model:list",
    "model:show",
    "event:list",
    "module:list",
    "gate:list",
    "cache:clear",
    "cache:forget",
    "ai:tools",
    "ai:vectors",
    "key:generate",
    "log:tail",
]


# Frozen Tier-2 command names from the CLI-completion spec (command table #34-#56;
# item #55 registers two commands, env:encrypt and env:decrypt). Same guard as
# Tier-1: if this goes red, a command failed to register — do not trim the list.
TIER_2_COMMANDS = [
    "queue:failed",
    "queue:retry",
    "queue:flush",
    "queue:forget",
    "queue:prune-failed",
    "queue:clear",
    "db:show",
    "db:table",
    "db:cli",
    "db:documents",
    "mail:test",
    "token:create",
    "token:revoke",
    "user:create",
    "auth:logout-everywhere",
    "throttle:clear",
    "search:status",
    "make:vector-store",
    "make:component",
    "make:layout",
    "make:hook",
    "env:encrypt",
    "env:decrypt",
    "make:command",
]


def test_list_covers_spec_commands():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    names = set(result.stdout.splitlines())
    missing = [name for name in TIER_1_COMMANDS if name not in names]
    assert not missing, f"list --raw missing Tier-1 commands: {missing}"
    missing = [name for name in TIER_2_COMMANDS if name not in names]
    assert not missing, f"list --raw missing Tier-2 commands: {missing}"
