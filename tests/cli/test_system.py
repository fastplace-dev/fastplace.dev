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


# Frozen Tier-3 command names from the CLI-completion spec (command table #57-#63;
# item #61 registers two commands, down and up). Same guard as Tier-1: if this
# goes red, a command failed to register — do not trim the list.
TIER_3_COMMANDS = [
    "schedule:run",
    "schedule:work",
    "schedule:list",
    "schedule:test",
    "down",
    "up",
    "queue:restart",
    "queue:monitor",
]


def test_list_covers_spec_commands():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    names = set(result.stdout.splitlines())
    missing = [name for name in TIER_1_COMMANDS if name not in names]
    assert not missing, f"list --raw missing Tier-1 commands: {missing}"
    missing = [name for name in TIER_2_COMMANDS if name not in names]
    assert not missing, f"list --raw missing Tier-2 commands: {missing}"
    missing = [name for name in TIER_3_COMMANDS if name not in names]
    assert not missing, f"list --raw missing Tier-3 commands: {missing}"


def _nonblank_lines(result) -> list[str]:
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def test_list_renders_namespace_header_lines():
    """Each namespace is a standalone section header, not a table column."""
    result = runner.invoke(cli_app, ["list"])
    assert result.exit_code == 0
    lines = _nonblank_lines(result)
    header = next(i for i, line in enumerate(lines) if line == "cache")
    # the first command of the section follows its header directly
    assert lines[header + 1].startswith("cache:")


def test_list_shows_full_command_names_under_headers():
    """artisan-style sections repeat the namespace — `cache:clear`, not bare `clear`."""
    result = runner.invoke(cli_app, ["list"])
    assert "cache:clear" in result.stdout
    assert "db:doctor" in result.stdout


def test_list_available_group_first_then_namespaces_alphabetical():
    """Un-namespaced commands lead (`available`), then `ai` < `auth` < `cache`."""
    result = runner.invoke(cli_app, ["list"])
    lines = _nonblank_lines(result)
    positions = {
        token: next(i for i, line in enumerate(lines) if line.split(":")[0] == token)
        for token in ("available", "ai", "auth", "cache")
    }
    assert positions["available"] < positions["ai"] < positions["auth"] < positions["cache"]


def test_list_header_line_precedes_sections():
    """The listing opens with a bold `Fastplace commands` banner line."""
    result = runner.invoke(cli_app, ["list"])
    lines = _nonblank_lines(result)
    assert lines[0] == "Fastplace commands"
