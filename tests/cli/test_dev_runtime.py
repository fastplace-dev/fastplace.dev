"""`run dev` / `serve` runtime safety — env loading, driver guards, signals.

Covers the W3 runtime-safety wave: .env honored by both entrypoints
(supp-2-G2), multi-worker memory-session refusal (serve-G1/tfa-G6),
production memory-cache refusal at serve boot (ssr-G4), the reload-session
hint (supp-2-G4), signal-driven child-tree cleanup (supp-2-G1/serve-G3),
the serve runtime marker for asset resolution (ssr-G5/tfa-G7), and
proxy-header passthrough (serve-G6).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401  (reset_db + module parking)

# The `spawned` tmp-project/captured-Popen fixture lives in tests/cli/conftest.py.
from tests.cli.conftest import _FakeProc

runner = CliRunner()


def _write_env(box, text: str) -> None:
    (box.root / ".env").write_text(text)


# --- .env honored (supp-2-G2) ------------------------------------------------


def test_run_dev_reads_ports_from_env_file(spawned):
    _write_env(spawned, "APP_HOST=127.0.0.2\nAPP_PORT=8123\nVITE_PORT=5199\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert "--host" in backend and backend[backend.index("--host") + 1] == "127.0.0.2"
    assert "--port" in backend and backend[backend.index("--port") + 1] == "8123"


def test_serve_reads_ports_from_env_file(spawned):
    _write_env(spawned, "APP_HOST=127.0.0.2\nAPP_PORT=8124\nAPP_WORKERS=1\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert serve_cmd[serve_cmd.index("--port") + 1] == "8124"
    assert serve_cmd[serve_cmd.index("--host") + 1] == "127.0.0.2"


def test_env_file_does_not_override_real_env(spawned, monkeypatch):
    monkeypatch.setenv("APP_PORT", "8125")
    _write_env(spawned, "APP_PORT=9999\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert serve_cmd[serve_cmd.index("--port") + 1] == "8125"


# --- multi-worker session guard (serve-G1 / tfa-G6) --------------------------


def test_serve_refuses_memory_sessions_with_multiple_workers(spawned):
    # Scaffold-default environment: APP_ENV=local, no SESSION_DRIVER —
    # resolves to the per-process memory store.
    _write_env(spawned, "APP_ENV=local\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "2"])
    assert result.exit_code == 1
    assert "SESSION_DRIVER" in result.output
    assert "SESSION_ALLOW_MEMORY_MULTIWORKER" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_allows_memory_sessions_with_ack_env(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_ALLOW_MEMORY_MULTIWORKER=1\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "2"])
    assert result.exit_code == 0, result.output
    assert any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_ack_flag_zero_still_refuses(spawned):
    # `=0` must mean "keep the guard", not the old any-nonempty bypass.
    _write_env(spawned, "APP_ENV=local\nSESSION_ALLOW_MEMORY_MULTIWORKER=0\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "2"])
    assert result.exit_code == 1
    assert "SESSION_DRIVER" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_memory_session_single_worker_allowed(spawned):
    _write_env(spawned, "APP_ENV=local\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_database_session_multi_worker_allowed(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "4"])
    assert result.exit_code == 0, result.output
    assert any("uvicorn" in cmd for cmd in spawned.commands)


# --- production cache guard at serve boot (ssr-G4) ---------------------------


def test_serve_refuses_memory_cache_in_production(spawned):
    _write_env(spawned, "APP_ENV=production\nAPP_KEY=" + "k" * 48 + "\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 1
    assert "CACHE_DRIVER" in result.output
    assert "CACHE_ALLOW_MEMORY_IN_PRODUCTION" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_allows_memory_cache_in_production_with_ack(spawned):
    _write_env(
        spawned,
        "APP_ENV=production\nAPP_KEY=" + "k" * 48 + "\nCACHE_ALLOW_MEMORY_IN_PRODUCTION=1\n",
    )
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_cache_ack_flag_zero_still_refuses(spawned):
    _write_env(
        spawned,
        "APP_ENV=production\nAPP_KEY=" + "k" * 48 + "\nCACHE_ALLOW_MEMORY_IN_PRODUCTION=off\n",
    )
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 1
    assert "CACHE_DRIVER" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_allows_database_cache_in_production(spawned):
    _write_env(spawned, "APP_ENV=production\nAPP_KEY=" + "k" * 48 + "\nCACHE_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert any("uvicorn" in cmd for cmd in spawned.commands)


# --- reload logout hint (supp-2-G4) ------------------------------------------


def test_run_dev_warns_about_memory_sessions_under_reload(spawned):
    _write_env(spawned, "APP_ENV=local\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    assert "SESSION_DRIVER" in result.output


def test_run_dev_no_warning_with_database_sessions(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    assert "will log you out" not in result.output


# --- dev entrypoint + runtime marker (supp-2-G5 / ssr-G5 / tfa-G7) -----------


def test_run_dev_serves_the_dev_shell_entrypoint(spawned):
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert any("fastplace.http.dev_shell:app" in part for part in backend)


def test_serve_marks_the_runtime_for_asset_resolution(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output
    assert spawned.envs and spawned.envs[0].get("FASTPLACE_RUNTIME") == "serve"


def test_run_dev_marks_dev_runtime_not_serve(spawned):
    # Stronger than the old "no marker" contract: dev declares "dev"
    # explicitly so PrerenderStaticFiles stays out of the stack (one stale
    # prerender run must not shadow live pages) — and "serve" never leaks
    # into a dev child, whatever the shell exported.
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    assert spawned.envs and all(env.get("FASTPLACE_RUNTIME") == "dev" for env in spawned.envs)


# --- proxy-header passthrough (serve-G6) -------------------------------------


def test_serve_passes_forwarded_allow_ips_through(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(
        cli_app,
        ["serve", "--skip-build", "--forwarded-allow-ips", "10.0.0.0/8"],
    )
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert serve_cmd[serve_cmd.index("--forwarded-allow-ips") + 1] == "10.0.0.0/8"


def test_serve_can_disable_proxy_headers(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--no-proxy-headers"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert "--no-proxy-headers" in serve_cmd
    assert "--proxy-headers" not in serve_cmd


def test_serve_proxy_headers_on_by_default(spawned):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    # uvicorn's own default is proxy-headers on; the CLI stays out of the way.
    assert "--no-proxy-headers" not in serve_cmd
    assert "--forwarded-allow-ips" not in serve_cmd


def test_serve_propagates_child_exit_code(spawned, monkeypatch):
    # A crashed uvicorn must fail the serve command itself — systemd and
    # deploy scripts key on the wrapper's exit code, not the child's.
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(
        "fastplace.cli.dev._spawn",
        lambda *a, **k: SimpleNamespace(
            pid=None, returncode=3, wait=lambda timeout=None: 3, poll=lambda: 0
        ),
    )
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 3


def test_serve_clean_child_exit_is_success(spawned, monkeypatch):
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(
        "fastplace.cli.dev._spawn",
        lambda *a, **k: SimpleNamespace(
            pid=None, returncode=0, wait=lambda timeout=None: 0, poll=lambda: 0
        ),
    )
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 0, result.output


# --- npm child wiring (dev vite leg + serve build leg) ------------------------


def test_run_dev_spawns_npm_run_dev_with_vite_port(spawned, monkeypatch):
    # package.json present and npm resolvable → the Vite dev server must be
    # spawned as exactly `npm run dev`, with PORT carrying VITE_PORT's value.
    (spawned.root / "package.json").write_text("{}\n")
    monkeypatch.setattr(
        "shutil.which", lambda name: "/fakebin/npm" if name == "npm" else None, raising=False
    )
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\nVITE_PORT=5199\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-lint"])
    assert result.exit_code == 0, result.output
    assert ["/fakebin/npm", "run", "dev"] in spawned.commands
    index = spawned.commands.index(["/fakebin/npm", "run", "dev"])
    assert spawned.envs[index].get("PORT") == "5199"


def test_serve_fails_loudly_when_frontend_build_fails(spawned, monkeypatch):
    # A failing `npm run build` must stop serve with exit 1 and a readable
    # failure line — never spawn uvicorn against stale assets.
    (spawned.root / "package.json").write_text("{}\n")
    monkeypatch.setattr(
        "shutil.which", lambda name: "/fakebin/npm" if name == "npm" else None, raising=False
    )
    monkeypatch.setattr(
        "fastplace.cli.dev.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=1),
    )
    _write_env(spawned, "APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve"])
    assert result.exit_code == 1
    assert "Frontend build failed" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


# --- child-tree supervision (supp-2-G1 / serve-G3) ---------------------------


def test_spawn_detaches_children_into_their_own_process_group(spawned, monkeypatch):
    from fastplace.cli import dev

    captured: list[dict] = []
    real_popen = subprocess.Popen

    def recording_popen(command, *args, **kwargs):  # noqa: ANN002, ANN003
        captured.append(kwargs)
        return real_popen([sys.executable, "-c", "pass"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    dev._spawn(["anything"], cwd=str(spawned.root), env=dict(os.environ)).wait()
    # Detached into a new session: the wrapper can signal the whole child
    # tree via the process group without touching itself.
    assert captured and captured[0].get("start_new_session") is True


def test_terminate_tree_stops_a_real_process_group():
    from fastplace.cli.dev import _spawn, _terminate_tree

    child = _spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=os.getcwd(),
        env=dict(os.environ),
    )
    _terminate_tree(child)
    assert child.poll() is not None
    assert child.returncode is not None and child.returncode != 0


def test_signal_handlers_installed_for_term_and_hup():
    from fastplace.cli.dev import _install_signal_handlers

    saved_term = signal.getsignal(signal.SIGTERM)
    saved_hup = signal.getsignal(signal.SIGHUP)
    try:
        _install_signal_handlers()
        term_handler = signal.getsignal(signal.SIGTERM)
        hup_handler = signal.getsignal(signal.SIGHUP)
        assert callable(term_handler) and term_handler not in (signal.SIG_DFL, signal.SIG_IGN)
        assert callable(hup_handler) and hup_handler not in (signal.SIG_DFL, signal.SIG_IGN)
        # The handler routes through the normal cleanup path: SystemExit.
        with pytest.raises(SystemExit):
            term_handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, saved_term)
        signal.signal(signal.SIGHUP, saved_hup)


def test_terminate_tree_tolerates_fake_children():
    from fastplace.cli.dev import _terminate_tree

    fake = _FakeProc(["sleep", "1"])
    _terminate_tree(fake)
    assert fake.terminated
