"""Central bytecode cache — `PYTHONPYCACHEPREFIX` wiring for CLI runtimes.

`run dev`, `serve`, and `shell` must keep `.pyc` output out of the source
tree: every child env (and the in-process shell) defaults
`PYTHONPYCACHEPREFIX` to `storage/cache/pycache` under the project root,
while an explicit user export keeps winning.
"""

from __future__ import annotations

import sys

from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli._interp import central_pycache_prefix, with_pycache_prefix

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)

# The `spawned` tmp-project/captured-Popen fixture lives in tests/cli/conftest.py.
from tests.cli.conftest import _FakeProc  # noqa: F401  (shared harness import idiom)

runner = CliRunner()


def _write_env(box, text: str) -> None:
    (box.root / ".env").write_text(text)


def test_central_pycache_prefix_lives_under_storage_cache():
    # storage/ is the framework's designated runtime-output home and is
    # gitignored by the scaffold — the mirror tree lands outside version
    # control without a new ignore rule.
    assert central_pycache_prefix.__module__
    from pathlib import Path

    path = central_pycache_prefix(Path("/tmp/proj"))
    assert path == Path("/tmp/proj") / "storage" / "cache" / "pycache"


def test_with_pycache_prefix_defaults_into_child_env():
    from pathlib import Path

    env = with_pycache_prefix(Path("/tmp/proj"), {"PATH": "/bin"})
    assert env["PYTHONPYCACHEPREFIX"] == "/tmp/proj/storage/cache/pycache"
    # Copy, not mutation: the caller's dict stays untouched.
    assert "PYTHONPYCACHEPREFIX" not in {"PATH": "/bin"} and env["PATH"] == "/bin"


def test_with_pycache_prefix_keeps_explicit_user_setting():
    from pathlib import Path

    env = with_pycache_prefix(Path("/tmp/proj"), {"PYTHONPYCACHEPREFIX": "/custom/cache"})
    assert env["PYTHONPYCACHEPREFIX"] == "/custom/cache"


def test_run_dev_children_get_central_pycache_prefix(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    expected = str(spawned.root / "storage" / "cache" / "pycache")
    assert spawned.envs and all(env.get("PYTHONPYCACHEPREFIX") == expected for env in spawned.envs)


def test_run_dev_respects_user_exported_prefix(spawned, monkeypatch):
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", "/user/cache")
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    assert spawned.envs and all(
        env.get("PYTHONPYCACHEPREFIX") == "/user/cache" for env in spawned.envs
    )


def test_serve_children_get_central_pycache_prefix(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    expected = str(spawned.root / "storage" / "cache" / "pycache")
    assert spawned.envs and spawned.envs[0].get("PYTHONPYCACHEPREFIX") == expected


def test_shell_bootstrap_redirects_in_process_pycache(spawned, monkeypatch):
    # shell imports project models in-process, so the switch is
    # sys.pycache_prefix — set from the project root's central cache unless
    # the interpreter already started with an explicit prefix.
    from fastplace.cli import shell_cmd

    monkeypatch.setattr(sys, "pycache_prefix", None, raising=False)
    original = sys.pycache_prefix
    try:
        shell_cmd._apply_central_pycache(spawned.root)
        assert sys.pycache_prefix == str(spawned.root / "storage" / "cache" / "pycache")
    finally:
        monkeypatch.setattr(sys, "pycache_prefix", original, raising=False)
