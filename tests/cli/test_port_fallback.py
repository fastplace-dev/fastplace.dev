"""Default bind port (9000) + auto-fallback for `run dev`, strict otherwise.

The built-in default walks 9000, 9001, ... while ports stay free; an
explicit choice (``--port`` / APP_PORT) is strict — a busy port is an
error naming the nearest free alternative. ``serve`` never falls back:
a production bind that silently moves one port up breaks every reverse
proxy aimed at it.
"""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from fastplace.cli import dev

runner = CliRunner()


# --- probe ---------------------------------------------------------------------


def _ephemeral_port() -> int:
    """A port the kernel confirms free right now (bind to port 0)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class _HeldPort:
    """Hold a real TCP listener so the probe must report the port busy."""

    def __init__(self, port: int, host: str = "127.0.0.1", family: int = socket.AF_INET) -> None:
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.bind((host, port))
        self.sock.listen(1)

    def close(self) -> None:
        self.sock.close()


def _ephemeral_port_v6() -> int:
    """An IPv6-loopback port the kernel confirms free right now."""
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    try:
        sock.bind(("::1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def test_port_is_free_on_an_ephemeral_port():
    assert dev._port_is_free("127.0.0.1", _ephemeral_port())


def test_port_is_busy_while_held_and_free_after_release():
    port = _ephemeral_port()
    held = _HeldPort(port)
    try:
        assert not dev._port_is_free("127.0.0.1", port)
    finally:
        held.close()
    assert dev._port_is_free("127.0.0.1", port)


def test_probe_binds_ipv6_for_ipv6_literal_hosts():
    # An AF_INET probe cannot see v6 listeners; the family must follow the
    # host the way uvicorn's own bind does.
    try:
        port = _ephemeral_port_v6()
    except OSError:
        pytest.skip("no IPv6 loopback on this host")
    held = _HeldPort(port, host="::1", family=socket.AF_INET6)
    try:
        assert not dev._port_is_free("::1", port)
    finally:
        held.close()


def test_probe_reports_out_of_range_port_as_busy_not_crash():
    # OverflowError is not an OSError — an unguarded bind on 70000 must not
    # escape the probe as a raw traceback.
    assert dev._port_is_free("127.0.0.1", 70000) is False


def test_probe_defers_host_level_bind_errors_to_the_server():
    # A bind error about the host itself (EADDRNOTAVAIL where the loopback
    # alias is absent, EACCES, ...) must read as free — the server surfaces
    # the real problem; the probe never invents a conflict.
    assert dev._port_is_free("127.0.0.2", _ephemeral_port()) is True


def test_default_port_is_9000():
    assert dev.DEFAULT_PORT == 9000


# --- resolver ------------------------------------------------------------------


def test_resolve_returns_free_preferred_port(monkeypatch):
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: True)
    assert dev._resolve_bind_port("127.0.0.1", 9500, fallback=False) == 9500


def test_resolve_falls_back_to_next_free_port(monkeypatch):
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != 9500)
    assert dev._resolve_bind_port("127.0.0.1", 9500, fallback=True) == 9501


def test_resolve_fallback_exhaustion_raises(monkeypatch):
    from fastplace.errors import ConfigurationError

    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: False)
    with pytest.raises(ConfigurationError) as exc:
        dev._resolve_bind_port("127.0.0.1", 9500, fallback=True)
    message = str(exc.value)
    # The exhaustion error names the whole walked range, bounds included.
    assert "9500" in message and "9509" in message


def test_resolve_rejects_ports_outside_the_valid_range():
    from fastplace.errors import ConfigurationError

    for bad in (0, -1, 65536, 70000):
        with pytest.raises(ConfigurationError, match="1 and 65535"):
            dev._resolve_bind_port("127.0.0.1", bad, fallback=True)


def test_first_free_port_never_walks_past_65535(monkeypatch):
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: False)
    assert dev._first_free_port("127.0.0.1", 65533, 10) is None


def test_resolve_strict_boundary_walk_reports_error_without_hint(monkeypatch):
    # Strict mode at the top of the range: the nearest-free scan is bounded
    # by 65535, so an all-busy tail must give the plain error, no phantom
    # "nearest free port" beyond the valid range.
    from fastplace.errors import ConfigurationError

    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: False)
    with pytest.raises(ConfigurationError) as exc:
        dev._resolve_bind_port("127.0.0.1", 65530, fallback=False)
    message = str(exc.value)
    assert "65530" in message
    assert "nearest free port" not in message


def test_resolve_strict_busy_port_raises_with_nearest_free_hint(monkeypatch):
    from fastplace.errors import ConfigurationError

    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != 9500)
    with pytest.raises(ConfigurationError) as exc:
        dev._resolve_bind_port("127.0.0.1", 9500, fallback=False)
    message = str(exc.value)
    assert "9500" in message and "9501" in message


def test_resolve_fallback_skips_a_run_of_busy_ports(monkeypatch):
    free = {9503}  # 9500, 9501, 9502 busy — the walk must reach 9503
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port in free)
    assert dev._resolve_bind_port("127.0.0.1", 9500, fallback=True) == 9503


# --- run dev wiring ------------------------------------------------------------


def test_run_dev_uses_9000_when_free(spawned, monkeypatch):
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: True)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert backend[backend.index("--port") + 1] == "9000"
    assert "9001" not in result.output  # no fallback notice


def test_run_dev_default_port_falls_back_when_busy(spawned, monkeypatch):
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != dev.DEFAULT_PORT)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert backend[backend.index("--port") + 1] == "9001"
    assert "9001" in result.output  # the fallback notice names the new port


def test_run_dev_cli_port_is_strict_when_busy(spawned, monkeypatch):
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != 8777)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint", "--port", "8777"])
    assert result.exit_code == 1
    assert "8777" in result.output and "8778" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_run_dev_env_port_is_strict_when_busy(spawned, monkeypatch):
    (spawned.root / ".env").write_text("APP_PORT=8779\n")
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != 8779)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 1
    assert "8779" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_run_dev_env_port_free_never_falls_back(spawned, monkeypatch):
    # APP_PORT names a port — the resolver must see exactly that port and
    # never touch the 9000 default on its way in.
    (spawned.root / ".env").write_text("APP_PORT=8780\n")
    calls: list[int] = []

    def only_probe_default_gate(host, port):
        calls.append(port)
        return True

    monkeypatch.setattr(dev, "_port_is_free", only_probe_default_gate)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert backend[backend.index("--port") + 1] == "8780"
    assert calls == [8780]  # the default 9000 was never even probed


def test_run_dev_fallback_notice_warns_about_app_url(spawned, monkeypatch):
    # The shifted port desyncs every APP_URL-derived URL (passkey origins,
    # signed links) — the notice must say so, not just name the new port.
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != dev.DEFAULT_PORT)
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 0, result.output
    assert "APP_URL" in result.output


def test_run_dev_malformed_app_port_is_clean_error(spawned):
    (spawned.root / ".env").write_text("APP_PORT=not-a-port\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 1
    assert "APP_PORT" in result.output and "must be an integer" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_run_dev_rich_markup_in_app_port_is_escaped(spawned):
    # A value like 'x[/]y' must not detonate Rich's markup parser inside
    # the clean-error handler itself.
    (spawned.root / ".env").write_text("APP_PORT=x[/]y\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 1
    assert "must be an integer" in result.output
    assert "MarkupError" not in result.output


def test_run_dev_cli_port_overrides_malformed_app_port(spawned):
    # An explicit --port is how a user escapes a broken .env value — the
    # flag must win without APP_PORT ever being parsed.
    (spawned.root / ".env").write_text("APP_PORT=nope\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint", "--port", "8783"])
    assert result.exit_code == 0, result.output
    backend = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert backend[backend.index("--port") + 1] == "8783"


def test_run_dev_app_port_out_of_range_names_app_port(spawned):
    # An out-of-range value from .env must point at APP_PORT as the
    # source, mirroring the malformed-value error next to it.
    (spawned.root / ".env").write_text("APP_PORT=70000\n")
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 1
    assert "APP_PORT" in result.output and "1 and 65535" in result.output


def test_serve_malformed_app_port_is_clean_error(spawned):
    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\nAPP_PORT=nope\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 1
    assert "APP_PORT" in result.output and "must be an integer" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_cli_port_overrides_malformed_app_port(spawned):
    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\nAPP_PORT=nope\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1", "--port", "8784"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert serve_cmd[serve_cmd.index("--port") + 1] == "8784"


def test_run_dev_out_of_range_port_flag_is_clean_error(spawned):
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint", "--port", "70000"])
    assert result.exit_code == 1
    assert "1 and 65535" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_run_dev_propagates_backend_exit_code(spawned, monkeypatch):
    # A backend that dies on its own bind must fail `run dev` itself — the
    # wrapper's exit code is what scripts and supervisors read.
    from types import SimpleNamespace

    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(
        "fastplace.cli.dev._spawn",
        lambda *a, **k: SimpleNamespace(
            pid=None, returncode=3, wait=lambda timeout=None: 3, poll=lambda: 0
        ),
    )
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 3


def test_run_dev_signal_death_maps_to_shell_convention(spawned, monkeypatch):
    # wait() reports a signal death negative; shells encode it as 128+N,
    # and supervisors key on that convention — not on 256-N.
    from types import SimpleNamespace

    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(
        "fastplace.cli.dev._spawn",
        lambda *a, **k: SimpleNamespace(
            pid=None, returncode=-15, wait=lambda timeout=None: -15, poll=lambda: 0
        ),
    )
    result = runner.invoke(cli_app, ["run", "dev", "--skip-vite", "--skip-lint"])
    assert result.exit_code == 143  # 128 + SIGTERM


def test_serve_signal_death_maps_to_shell_convention(spawned, monkeypatch):
    from types import SimpleNamespace

    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(
        "fastplace.cli.dev._spawn",
        lambda *a, **k: SimpleNamespace(
            pid=None, returncode=-9, wait=lambda timeout=None: -9, poll=lambda: 0
        ),
    )
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 137  # 128 + SIGKILL


# --- serve wiring --------------------------------------------------------------


def test_serve_default_port_busy_is_error_not_fallback(spawned, monkeypatch):
    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: port != dev.DEFAULT_PORT)
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1"])
    assert result.exit_code == 1
    assert "9000" in result.output and "9001" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_explicit_port_free_proceeds(spawned, monkeypatch):
    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    monkeypatch.setattr(dev, "_port_is_free", lambda host, port: True)
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1", "--port", "8781"])
    assert result.exit_code == 0, result.output
    serve_cmd = [cmd for cmd in spawned.commands if "uvicorn" in cmd][0]
    assert serve_cmd[serve_cmd.index("--port") + 1] == "8781"


def test_serve_port_zero_is_rejected_not_swapped(spawned):
    # `serve --port 0` must reach validation like any explicit port — an
    # `or`-style default would silently bind 9000 instead.
    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1", "--port", "0"])
    assert result.exit_code == 1
    assert "1 and 65535" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)


def test_serve_out_of_range_port_flag_is_clean_error(spawned):
    (spawned.root / ".env").write_text("APP_ENV=local\nSESSION_DRIVER=database\n")
    result = runner.invoke(cli_app, ["serve", "--skip-build", "--workers", "1", "--port", "70000"])
    assert result.exit_code == 1
    assert "1 and 65535" in result.output
    assert not any("uvicorn" in cmd for cmd in spawned.commands)
