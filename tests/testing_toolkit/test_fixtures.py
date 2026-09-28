"""Plugin fixtures — ``app``, ``client``, ``mail``, ``queue_fake``,
``events_fake``, ``clock`` — exercised through real pytest subprocesses."""

from __future__ import annotations


def test_app_and_client_boot_a_real_application(pytester, mini_app, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_boot="""
        from fastplace.testing import TestResponse


        async def test_app_fixture_boots(app):
            assert app is not None


        async def test_client_returns_fluent_responses(client):
            response = await client.get("/health")
            assert isinstance(response, TestResponse)
            response.assert_ok()
            response.assert_json({"ok": True})
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)


def test_mail_fake_asserts_on_the_memory_outbox(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_mail="""
        from fastplace.mail import Mail, MailMessage


        async def test_assertions(mail):
            await Mail.to("a@example.test").send(
                MailMessage(subject="Invoice paid", text="Thanks for the money", to="ignored")
            )
            mail.assert_sent_to("a@example.test")
            mail.assert_sent(subject_contains="Invoice")
            mail.assert_sent(body_contains="money")
            mail.assert_sent_count(1)
            mail.assert_not_sent(to="other@example.test")
            mail.assert_not_sent(subject_contains="Refund")


        async def test_nothing_sent_yet(mail):
            mail.assert_nothing_sent()
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)


def test_queue_fake_asserts_pushes_and_drains(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_queue="""
        from fastplace.queue import Job


        @Job()
        async def send_welcome(email: str) -> str:
            return email


        @Job()
        async def add_note() -> None:
            from pathlib import Path

            Path("ran.txt").write_text("ran")


        async def test_push_assertions(queue_fake):
            from fastplace.queue import queue

            await queue().dispatch("send_welcome", email="u@example.test")
            queue_fake.assert_pushed("send_welcome")
            queue_fake.assert_pushed("send_welcome", match={"email": "u@example.test"})
            queue_fake.assert_pushed_count(1)
            queue_fake.assert_not_pushed("send_invoice")
            queue_fake.assert_not_pushed("send_welcome", match={"email": "nope@x.test"})


        async def test_drain_runs_the_jobs(queue_fake):
            from fastplace.queue import queue

            await queue().dispatch("add_note")
            assert await queue_fake.run() == 1
            queue_fake.assert_executed("add_note")
            queue_fake.assert_not_executed("send_welcome")


        async def test_nothing_pushed(queue_fake):
            queue_fake.assert_nothing_pushed()
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=3)


def test_events_fake_records_and_suppresses_listeners(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_events="""
        import fastplace.events
        from fastplace.events import DomainEvent, listen


        async def test_records_dispatches(events_fake):
            await fastplace.events.dispatch(DomainEvent("order.shipped", {"id": 5}))
            events_fake.assert_dispatched("order.shipped")
            events_fake.assert_dispatched("order.shipped", payload={"id": 5})
            events_fake.assert_dispatched_count(1)
            events_fake.assert_not_dispatched("order.cancelled")


        async def test_listeners_do_not_run(events_fake):
            ran = []
            listen("side.effect", lambda event: ran.append(event.name))
            await fastplace.events.dispatch(DomainEvent("side.effect"))
            assert ran == []
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)


def test_clock_freezes_travels_and_restores(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_clock="""
        from datetime import UTC, datetime


        async def test_freeze_and_travel(clock):
            clock.freeze("2026-01-15T12:00:00+00:00")
            assert datetime.now(UTC).day == 15
            clock.travel(days=1, hours=2)
            now = datetime.now(UTC)
            assert (now.day, now.hour) == (16, 14)
            clock.move_to("2020-05-05T00:00:00+00:00")
            assert datetime.now(UTC).year == 2020


        async def test_frozen_here_too(clock):
            clock.freeze("1999-01-01T00:00:00+00:00")
            assert datetime.now(UTC).year == 1999


        def test_restored_after_the_frozen_tests():
            # The clock fixture must stop its freezer on teardown; 1999 was
            # only ever a frozen year.
            assert datetime.now(UTC).year != 1999
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=3)


def test_clock_stays_timezone_aware_for_naive_input(pytester, set_fastplace_ini):
    """A date-only or naive freeze (the guide's own example form) must still
    yield aware datetimes — ``clock.now() < datetime.now(UTC)`` is the exact
    comparison app code writes for "is this deadline past?"."""
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_clock_tz="""
        from datetime import UTC, datetime


        async def test_date_only_freeze_stays_comparable(clock):
            moment = clock.freeze("2026-01-01")
            assert moment.tzinfo is not None
            assert clock.now().tzinfo is not None
            # freezegun patches datetime.now too, so both sides are frozen —
            # the contract is the comparison works at all (naive/aware
            # mixing raises TypeError instead).
            assert clock.now() <= datetime.now(UTC)


        async def test_naive_string_freeze_stays_comparable(clock):
            clock.freeze("2026-03-01T12:00:00")
            assert clock.now().tzinfo is not None
            assert clock.now() <= datetime.now(UTC)
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)


def test_clock_travels_cache_ttl_deadlines(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_clock_cache="""
        from fastplace.cache import cache, reset_cache


        async def test_ttl_expiry_under_travel(clock):
            clock.freeze("2026-01-01T00:00:00+00:00")
            reset_cache()
            store = cache()
            await store.put("k", "v", ttl=60)
            assert await store.get("k") == "v"
            clock.travel(minutes=61)
            assert await store.get("k") is None
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
