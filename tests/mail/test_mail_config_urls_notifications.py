"""Mail config defaults, absolute URLs, notification emails."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

import fastplace.http.urls as urls
from fastplace.config import config
from fastplace.errors import ConfigurationError
from fastplace.http.request import Request
from fastplace.http.urls import build_absolute_url
from fastplace.mail.notifications import reset_password_message, verify_email_message

MAIL_KEYS = [
    "MAIL_DRIVER",
    "MAIL_FROM_ADDRESS",
    "MAIL_HOST",
    "MAIL_PORT",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_ENCRYPTION",
]


def test_mail_config_defaults(monkeypatch):
    for key in MAIL_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert config("MAIL_DRIVER") == "log"
    assert config("MAIL_FROM_ADDRESS") == "fastplace@localhost"
    assert config("MAIL_HOST") == "127.0.0.1"
    assert config("MAIL_PORT") == 25
    assert config("MAIL_USERNAME") is None
    assert config("MAIL_PASSWORD") is None
    assert config("MAIL_ENCRYPTION") == "none"


def _urls_config(state):
    return lambda key, default=None: state.get(key, default)


def test_app_url_wins(monkeypatch):
    monkeypatch.setattr(urls, "config", _urls_config({"APP_URL": "https://app.example.test"}))
    assert build_absolute_url("/reset") == "https://app.example.test/reset"


def test_trusted_request_host_used_when_app_url_empty(monkeypatch):
    monkeypatch.setattr(urls, "config", _urls_config({"TRUSTED_HOSTS": ["app.example.test"]}))
    request = cast(Request, SimpleNamespace(url="https://app.example.test/login?x=1"))
    assert build_absolute_url("/reset", request=request) == "https://app.example.test/reset"


def test_untrusted_request_host_refused(monkeypatch):
    monkeypatch.setattr(urls, "config", _urls_config({"TRUSTED_HOSTS": ["app.example.test"]}))
    request = cast(Request, SimpleNamespace(url="https://evil.example.test/login"))
    with pytest.raises(ConfigurationError):
        build_absolute_url("/reset", request=request)


def test_comma_separated_trusted_hosts_string(monkeypatch):
    monkeypatch.setattr(
        urls, "config", _urls_config({"TRUSTED_HOSTS": "other.test, app.example.test"})
    )
    request = cast(Request, SimpleNamespace(url="https://app.example.test/login"))
    assert build_absolute_url("/reset", request=request) == "https://app.example.test/reset"


def test_no_origin_refused(monkeypatch):
    monkeypatch.setattr(urls, "config", _urls_config({}))
    with pytest.raises(ConfigurationError):
        build_absolute_url("/reset")


def test_verify_email_message_content():
    message = verify_email_message(
        "user@example.test", "http://app.test/email/verify/1/abc?expires=123"
    )
    assert message.subject == "Verify your email address"
    assert message.to == "user@example.test"
    assert "/email/verify/1/abc?expires=123" in message.text
    assert "60 minutes" in message.text
    assert "no further action is required" in message.text


def test_reset_password_message_content():
    message = reset_password_message("user@example.test", "http://app.test/reset-password/tok")
    assert message.subject == "Reset your password"
    assert "/reset-password/tok" in message.text
    assert "60 minutes" in message.text
