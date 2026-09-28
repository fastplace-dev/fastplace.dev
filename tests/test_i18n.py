"""i18n — translation helper, pluralization, request-scoped locale (sweep-G10)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def lang_dir(tmp_path, monkeypatch):
    """A project root with ``lang/en.json`` + ``lang/bn.json``; cwd there."""
    lang = tmp_path / "lang"
    lang.mkdir()
    (lang / "en.json").write_text(
        json.dumps(
            {
                "messages": {
                    "welcome": "Welcome aboard",
                    "greet": "Hello :name",
                    "apples": "one apple|:count apples",
                    "inbox": {
                        "zero": "empty inbox",
                        "one": ":count message",
                        "other": ":count messages",
                    },
                },
                "only_en": "english only",
            }
        )
    )
    (lang / "bn.json").write_text(json.dumps({"messages": {"welcome": "স্বাগতম"}}))
    monkeypatch.chdir(tmp_path)
    return lang


@pytest.fixture(autouse=True)
def _reset_locale():
    from fastplace import i18n

    i18n.reset()
    yield
    i18n.reset()


def test_trans_resolves_a_dot_key_from_lang_json(lang_dir):
    from fastplace.i18n import trans

    assert trans("messages.welcome") == "Welcome aboard"


def test_trans_falls_back_to_the_default_locale(lang_dir):
    from fastplace.i18n import set_locale, trans

    set_locale("bn")
    # bn has the key; en fallback is never reached
    assert trans("messages.welcome") == "স্বাগতম"
    # missing in bn — the default locale answers
    assert trans("only_en") == "english only"


def test_trans_missing_key_returns_default_then_the_key(lang_dir):
    from fastplace.i18n import trans

    assert trans("messages.nope", default="fallback text") == "fallback text"
    assert trans("messages.nope") == "messages.nope"


def test_trans_replaces_placeholders(lang_dir):
    from fastplace.i18n import trans

    assert trans("messages.greet", name="Lin") == "Hello Lin"


def test_trans_choice_pipe_forms(lang_dir):
    from fastplace.i18n import trans_choice

    assert trans_choice("messages.apples", 1) == "one apple"
    assert trans_choice("messages.apples", 5) == "5 apples"


def test_trans_choice_named_forms(lang_dir):
    from fastplace.i18n import trans_choice

    assert trans_choice("messages.inbox", 0) == "empty inbox"
    assert trans_choice("messages.inbox", 1) == "1 message"
    assert trans_choice("messages.inbox", 12) == "12 messages"


def test_trans_choice_accepts_extra_replacements(lang_dir):
    from fastplace.i18n import trans_choice

    # Extra replacements are harmless; :count is still substituted.
    assert trans_choice("messages.apples", 3, name="Lin") == "3 apples"


def test_set_locale_scopes_per_async_task(lang_dir):
    """Each request/task sees its own locale — concurrent scopes never bleed."""
    import asyncio

    from fastplace.i18n import get_locale, set_locale, trans

    async def worker(locale: str) -> str:
        set_locale(locale)
        await asyncio.sleep(0.01)
        return trans("messages.welcome")

    async def driver():
        results = await asyncio.gather(worker("en"), worker("bn"), worker("en"))
        assert get_locale() == "en"  # driver task untouched
        return results

    assert asyncio.run(driver()) == ["Welcome aboard", "স্বাগতম", "Welcome aboard"]


def test_unsupported_locale_falls_back_to_config_default(lang_dir, monkeypatch):
    from fastplace.i18n import set_locale, trans

    monkeypatch.setenv("LOCALES", "en,bn")
    set_locale("xx")  # not in the supported set
    assert trans("messages.welcome") == "Welcome aboard"


async def test_locale_middleware_resolves_query_header_then_default(lang_dir, monkeypatch):
    from fastplace.http.kernel import get_app
    from fastplace.http.router import Router
    from fastplace.i18n import trans

    monkeypatch.setenv("LOCALES", "en,bn")

    async def handler(request):
        from fastplace.http.response import Json

        return Json({"hello": trans("messages.welcome")})

    router = Router()
    router.get("/hello", handler)
    app = get_app(routes=router, config={"APP_ENV": "local", "APP_KEY": "k" * 64})

    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        by_query = await client.get("/hello", params={"locale": "bn"})
        by_header = await client.get("/hello", headers={"Accept-Language": "bn,en;q=0.8"})
        unsupported = await client.get("/hello", params={"locale": "de"})
        default = await client.get("/hello")
    assert by_query.json() == {"hello": "স্বাগতম"}
    assert by_header.json() == {"hello": "স্বাগতম"}
    assert unsupported.json() == {"hello": "Welcome aboard"}
    assert default.json() == {"hello": "Welcome aboard"}
