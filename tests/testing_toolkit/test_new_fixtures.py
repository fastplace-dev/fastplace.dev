"""The supp-1-G8 fixtures — notifications, storage_fake, http_fake — through
real pytest subprocesses, like their W6 siblings in test_fixtures.py."""

from __future__ import annotations


def test_storage_fake_redirects_the_process_disk(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_storage_fake="""
        from pathlib import Path

        from fastplace.storage import disk


        async def test_writes_land_in_the_fake_not_the_filesystem(storage_fake):
            process_disk = disk()
            assert process_disk is storage_fake
            await process_disk.put("reports/q1.txt", "hello")
            storage_fake.assert_stored("reports/q1.txt", content="hello")
            # Nothing touched the real tree.
            assert not Path("storage/app/reports/q1.txt").exists()
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_notifications_fixture_records_without_delivery(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_notifications="""
        from fastplace.mail.message import MailMessage
        from fastplace.mail.transports import mail_outbox
        from fastplace.notifications import Notification, send
        from fastplace.testing import FakeNotifications


        class Paid(Notification):
            def via(self, notifiable):
                return ["mail", "database"]

            def to_mail(self, notifiable):
                return MailMessage(subject="paid", text="t", to=notifiable.email)

            def to_database(self, notifiable):
                return {"kind": "invoice"}


        class User:
            id = 3
            email = "a@example.test"


        async def test_fanout_recorded(notifications):
            assert isinstance(notifications, FakeNotifications)
            results = await send(User(), Paid())
            assert len(results) == 2
            assert mail_outbox() == []
            notifications.assert_sent(Paid, channel="mail", times=1)
            notifications.assert_sent(Paid, channel="database", match={"kind": "invoice"})
            notifications.assert_sent_count(2)
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_http_fake_fixture_stubs_and_asserts(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_http_fake="""
        from fastplace.testing import FakeHttp, FakeResponse


        async def test_stubbed_roundtrip(http_fake):
            assert isinstance(http_fake, FakeHttp)
            http_fake.respond("GET", "https://api.test/*", FakeResponse(200, json={"ok": 1}))
            response = await http_fake.get("https://api.test/thing")
            assert await response.json() == {"ok": 1}
            http_fake.assert_requested("GET", "https://api.test/thing")
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
