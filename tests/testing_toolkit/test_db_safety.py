"""supp-1-G3 — no test can ever write the developer's real database.

The plugin (``fastplace.testing.plugin``, loaded via the pytest11 entry
point) binds ``DATABASE_URL`` to a throwaway sqlite file for the whole
session. These tests run real pytest subprocesses over minimal projects
whose ``.env`` points at a "production" sqlite file and assert that file is
never touched — the exact hazard an app developer hits when a test imports
a repository or model without the ``app`` fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_plugin_pins_a_throwaway_database_by_default(
    pytester: pytest.Pytester, mini_app, set_fastplace_ini
):
    set_fastplace_ini(None)
    real = pytester.path / "production.sqlite3"
    (pytester.path / ".env").write_text(f"DATABASE_URL=sqlite+aiosqlite:///{real}\n")
    pytester.makepyfile(
        test_hazard="""
        async def test_writes_without_the_app_fixture():
            # No `app` fixture — this is the repository/service/model path
            # that read the .env URL before the plugin existed.
            from pathlib import Path

            from fastplace.db import db

            Path("pinned-url.txt").write_text(__import__("os").environ["DATABASE_URL"])
            await db.raw("CREATE TABLE IF NOT EXISTS hazard_probe (id INTEGER)")
            await db.raw("INSERT INTO hazard_probe VALUES (1)")
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
    pinned = Path(pytester.path, "pinned-url.txt").read_text()
    assert "fastplace-test-db" in pinned
    # The database the .env pointed at was never created, let alone written.
    assert not real.exists()


def test_the_pin_covers_the_whole_session(pytester: pytest.Pytester, mini_app, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_pin="""
        import os
        from pathlib import Path


        def _record(tag):
            with Path("urls.txt").open("a") as fh:
                fh.write(f"{tag}={os.environ['DATABASE_URL']}\\n")


        def test_first():
            _record("a")


        def test_second():
            _record("b")
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)
    lines = Path(pytester.path, "urls.txt").read_text().splitlines()
    assert len(lines) == 2
    assert lines[0].split("=", 1)[1] == lines[1].split("=", 1)[1]
    assert "fastplace-test-db" in lines[0]


def test_ini_off_disables_the_plugin_entirely(
    pytester: pytest.Pytester, set_fastplace_ini, monkeypatch
):
    set_fastplace_ini("off")
    monkeypatch.delenv("FASTPLACE_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///sentinel.sqlite3")
    pytester.makepyfile(
        test_off="""
        import os


        def test_environment_untouched():
            assert os.environ["DATABASE_URL"] == "sqlite+aiosqlite:///sentinel.sqlite3"
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_env_var_overrides_the_auto_pin(
    pytester: pytest.Pytester, mini_app, set_fastplace_ini, monkeypatch
):
    set_fastplace_ini(None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    chosen = pytester.path / "chosen.sqlite3"
    monkeypatch.setenv("FASTPLACE_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{chosen}")
    pytester.makepyfile(
        test_env="""
        import os
        from pathlib import Path


        def test_chosen_url_wins():
            Path("pinned-url.txt").write_text(os.environ["DATABASE_URL"])
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
    pinned = Path(pytester.path, "pinned-url.txt").read_text()
    assert pinned == f"sqlite+aiosqlite:///{chosen}"


def test_ini_url_pins_that_exact_database(pytester: pytest.Pytester, mini_app, set_fastplace_ini):
    chosen = pytester.path / "pick.sqlite3"
    set_fastplace_ini(f"sqlite+aiosqlite:///{chosen}")
    pytester.makepyfile(
        test_ini="""
        import os
        from pathlib import Path


        def test_ini_url_wins():
            Path("pinned-url.txt").write_text(os.environ["DATABASE_URL"])
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
    pinned = Path(pytester.path, "pinned-url.txt").read_text()
    assert pinned == f"sqlite+aiosqlite:///{chosen}"


def test_missing_directory_url_still_resolves(pytester, set_fastplace_ini):
    """An ini URL whose directory does not exist is used verbatim — sqlite
    only complains at connect time, never at the pin. (The earlier version
    of this test never actually pinned a missing-directory URL.)"""
    set_fastplace_ini(f"sqlite+aiosqlite:///{pytester.path}/no/such/dir/test.sqlite3")
    pytester.makepyfile(
        test_missing="""
        import os


        def test_ini_url_is_used_verbatim():
            assert os.environ["DATABASE_URL"].endswith("/no/such/dir/test.sqlite3")
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
