"""`mail:doctor` — mail stack diagnosis (roadmap spec, app plane)."""

from __future__ import annotations

import os
import re

import pytest
from typer.testing import CliRunner

from fastplace.cli import app as cli_app
from tests.cli._isolation import (  # noqa: F401 — autouse + fixture by name
    isolate_project_state,
    park_project_modules,
)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()

_SMTP_ENV = (
    "MAIL_DRIVER=smtp\n"
    "MAIL_HOST=smtp.example.test\n"
    "MAIL_PORT=587\n"
    "MAIL_USERNAME=alert@example.test\n"
    "MAIL_PASSWORD=hunter2-secret\n"
)


@pytest.fixture(autouse=True)
def _hermetic_environ():
    """Confine os.environ changes to the test that caused them."""
    env_before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture(autouse=True)
def _wide_output(monkeypatch):
    """Pin the Rich console width so fix/detail hints never wrap mid-word."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _restored_config():
    """Rebind the process config singleton after each test (test_log_prune)."""
    import fastplace.config as config_module

    saved = config_module._default_config
    yield
    config_module._default_config = saved


@pytest.fixture(autouse=True)
def _clean_job_registry():
    """Drop test-registered job handlers so the global registry stays clean."""
    from fastplace.queue import registry

    yield
    registry.pop("mail_send", None)


def _fake_aiosmtplib_present(monkeypatch):
    """Pretend aiosmtplib is importable so extra-rows simulate a full install."""
    import importlib.util

    real = importlib.util.find_spec

    def fake(name: str, *args, **kwargs):
        if name == "aiosmtplib":
            return object()  # any non-None spec reads as "installed"
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def _out(result) -> str:
    return ANSI_RE.sub("", result.stdout)


def _make_project(tmp_path, monkeypatch, env_text: str = "") -> object:
    """A cwd carrying the project marker + .env; chdir'd into."""
    (tmp_path / "asgi.py").write_text("# marker\n")
    if env_text:
        (tmp_path / ".env").write_text(env_text)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_mail_doctor_registered():
    result = runner.invoke(cli_app, ["list", "--raw"])
    assert result.exit_code == 0
    assert "mail:doctor" in result.stdout


def test_mail_doctor_help_exits_zero():
    result = runner.invoke(cli_app, ["mail:doctor", "--help"])
    assert result.exit_code == 0
    assert "usage" in _out(result).lower()
    assert "--connect" in _out(result)


def test_log_driver_project_passes(tmp_path, monkeypatch):
    """Default log driver needs no credentials and no extra: exit 0."""
    _make_project(tmp_path, monkeypatch, env_text="MAIL_DRIVER=log\n")

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "FAIL" not in out
    assert "log" in out.lower()


def test_unknown_mail_driver_fails(tmp_path, monkeypatch):
    _make_project(tmp_path, monkeypatch, env_text="MAIL_DRIVER=pigeon\n")

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "pigeon" in out.lower() or "unknown" in out.lower()


def test_smtp_without_extra_fails_with_install_hint(tmp_path, monkeypatch):
    """MAIL_DRIVER=smtp with aiosmtplib absent (this env): exact extra hint."""
    _make_project(tmp_path, monkeypatch, env_text=_SMTP_ENV)

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "fastplace[mail]" in out


def test_smtp_missing_credentials_fails(tmp_path, monkeypatch):
    _fake_aiosmtplib_present(monkeypatch)
    _make_project(
        tmp_path,
        monkeypatch,
        env_text="MAIL_DRIVER=smtp\nMAIL_HOST=smtp.example.test\n",  # no user/password
    )

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 1
    out = _out(result)
    assert "MAIL_USERNAME" in out or "MAIL_PASSWORD" in out


def test_smtp_credentials_masked_in_summary(tmp_path, monkeypatch):
    """Config summary prints host/user but never the password value."""
    _fake_aiosmtplib_present(monkeypatch)
    _make_project(tmp_path, monkeypatch, env_text=_SMTP_ENV)

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "smtp.example.test:587" in out
    assert "alert@example.test" in out
    assert "hunter2-secret" not in out  # the password never reaches the table


def test_queued_smtp_warns_about_worker(tmp_path, monkeypatch):
    """smtp + saq queues delivery — without a running worker it piles up."""
    _fake_aiosmtplib_present(monkeypatch)
    _make_project(tmp_path, monkeypatch, env_text=_SMTP_ENV + "QUEUE_DRIVER=saq\n")

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "WARN" in out
    assert "queue:work" in out


def test_unregistered_mail_send_handler_warns(
    tmp_path,
    monkeypatch,
    park_project_modules,  # noqa: F811 — fixture param
):
    """Queued smtp with no mail_send handler: mail would be dropped — WARN."""
    _fake_aiosmtplib_present(monkeypatch)
    _make_project(tmp_path, monkeypatch, env_text=_SMTP_ENV + "QUEUE_DRIVER=saq\n")

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "mail_send" in out  # names the handler the queue would drop


def test_registered_mail_send_handler_passes(
    tmp_path,
    monkeypatch,
    park_project_modules,  # noqa: F811 — fixture param
):
    _fake_aiosmtplib_present(monkeypatch)
    root = _make_project(tmp_path, monkeypatch, env_text=_SMTP_ENV + "QUEUE_DRIVER=saq\n")
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "jobs").mkdir()
    (root / "app" / "jobs" / "__init__.py").write_text("")
    (root / "app" / "jobs" / "mailer.py").write_text(
        "from fastplace.mail import message_from_dict, send_via_smtp\n"
        "from fastplace.queue import Job\n"
        "\n"
        "\n"
        '@Job(name="mail_send")\n'
        "async def mail_send(message: dict) -> None:\n"
        "    await send_via_smtp(message_from_dict(message))\n"
    )

    result = runner.invoke(cli_app, ["mail:doctor"])

    assert result.exit_code == 0, _out(result)
    out = _out(result)
    assert "FAIL" not in out
    assert "dropped" not in out.lower()


def test_connect_flag_fails_on_closed_port(tmp_path, monkeypatch):
    """--connect opens a real wire: a refused port is a FAIL row, exit 1."""
    _fake_aiosmtplib_present(monkeypatch)
    _make_project(
        tmp_path,
        monkeypatch,
        env_text=_SMTP_ENV.replace("smtp.example.test", "127.0.0.1").replace("587", "1"),
    )

    result = runner.invoke(cli_app, ["mail:doctor", "--connect"])

    assert result.exit_code == 1
    out = _out(result)
    assert "127.0.0.1" in out or "connect" in out.lower()
