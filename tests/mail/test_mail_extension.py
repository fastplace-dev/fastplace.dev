"""Public transport registry — third-party mail drivers via MAIL_DRIVER=<name>."""

from __future__ import annotations

import pytest

from fastplace.errors import ConfigurationError
from fastplace.mail import Mail, MailMessage
from fastplace.mail.transports import (
    register_transport,
    transport_for,
    unregister_transport,
)

_REGISTERED: list[str] = []

_BUILTINS = ("log", "memory", "smtp")


@pytest.fixture(autouse=True)
def _restore_registry():
    from fastplace.mail import transports

    yield
    # A test may have replaced a built-in sender — put the originals back
    # before dropping this test's own registrations.
    for name, sender in (
        ("log", transports.send_via_log),
        ("memory", transports.send_via_memory),
        ("smtp", transports.send_via_smtp),
    ):
        transports.register_transport(name, sender)
    for name in _REGISTERED:
        if name not in _BUILTINS:
            unregister_transport(name)
    _REGISTERED.clear()


def _register(name: str, sender) -> None:
    register_transport(name, sender)
    _REGISTERED.append(name)


async def test_registered_transport_delivers(monkeypatch):
    delivered: list[MailMessage] = []

    async def send_acme(message: MailMessage) -> None:
        delivered.append(message)

    _register("acme", send_acme)
    monkeypatch.setenv("MAIL_DRIVER", "acme")
    final = await Mail.to("user@example.test").send(
        MailMessage(subject="Hello", text="Body", to="x@example.test")
    )
    assert delivered[0].to == "user@example.test"
    assert delivered[0].subject == final.subject


async def test_registered_transport_is_used_by_transport_for():
    async def send_acme(message: MailMessage) -> None:
        return None

    _register("acme", send_acme)
    assert transport_for("acme") is send_acme


def test_unknown_transport_still_raises():
    with pytest.raises(ConfigurationError, match="unknown MAIL_DRIVER 'acme-missing'"):
        transport_for("acme-missing")


def test_builtin_drivers_resolve_through_the_registry():
    for name in ("log", "memory", "smtp"):
        assert callable(transport_for(name))


def test_reregistering_a_name_replaces_the_sender():
    async def replacement(message: MailMessage) -> None:
        return None

    _register("log", replacement)
    assert transport_for("log") is replacement


def test_register_transport_rejects_bad_input():
    with pytest.raises(ConfigurationError, match="non-empty"):
        register_transport("   ", lambda m: None)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="async callable"):
        register_transport("acme", "not-callable")  # type: ignore[arg-type]


def test_register_transport_rejects_a_sync_callable():
    import functools

    with pytest.raises(ConfigurationError, match="async callable"):

        def send_sync(message: MailMessage) -> None:
            return None

        _register("acme-sync", send_sync)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="async callable"):
        _register("acme-lambda", lambda message: None)  # type: ignore[arg-type]

    # A sync wrapper around an async function is still sync — functools
    # unwrapping must not whitelist it.
    async def send_async(message: MailMessage) -> None:
        return None

    with pytest.raises(ConfigurationError, match="async callable"):
        _register("acme-wrapped", functools.partial(lambda m: None, None))  # type: ignore[arg-type]


def test_register_transport_accepts_partial_wrapped_async():
    import functools

    delivered: list[MailMessage] = []

    async def send_acme(message: MailMessage, label: str) -> None:
        delivered.append(message)

    _register("acme-partial", functools.partial(send_acme, label="x"))
    assert callable(transport_for("acme-partial"))


def test_register_transport_accepts_bound_and_standalone_async():
    class Driver:
        async def send(self, message: MailMessage) -> None:
            return None

    _register("acme-bound", Driver().send)

    async def send_plain(message: MailMessage) -> None:
        return None

    _register("acme-plain", send_plain)
    assert callable(transport_for("acme-bound"))
    assert callable(transport_for("acme-plain"))
