"""Per-page head surface on render() — titles, description, canonical, OG.

Crawlers and link unfurlers (Facebook, Slack, iMessage, WhatsApp) do not
execute JavaScript, so every head tag must already be present in the
initial HTML document. These tests pin the declarative ``render()``
keyword surface that puts them there, plus the unconditional
``<noscript>`` fallback required by the SPA-only rendering contract.
"""

from __future__ import annotations

from starlette.requests import Request as SRequest

from fastplace.http.render import render
from fastplace.http.request import Request as FpRequest


def _document(**kwargs) -> str:
    session = kwargs.pop("session", None)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    if session is not None:
        scope["session"] = session
    request = FpRequest(SRequest(scope))
    response = render(request, component="Projects/Show", **kwargs)
    return response.body.decode()


# --- Titles ---------------------------------------------------------------


def test_default_title_uses_component_tail():
    doc = _document(props={})
    assert "<title>Show</title>" in doc


def test_camel_case_component_tail_gets_word_boundaries():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    response = render(FpRequest(SRequest(scope)), component="Auth/ForgotPassword", props={})
    assert "<title>Forgot Password</title>" in response.body.decode()


def test_title_argument_overrides_component_default():
    doc = _document(title="Project Alpha — Acme", props={})
    assert "<title>Project Alpha — Acme</title>" in doc


def test_title_is_html_escaped():
    doc = _document(title="R&D <lab>", props={})
    assert "<title>R&amp;D &lt;lab&gt;</title>" in doc
    assert "R&D <lab>" not in doc


# --- Description / robots -------------------------------------------------


def test_description_emitted_as_meta():
    doc = _document(description="All about project Alpha", props={})
    assert '<meta name="description" content="All about project Alpha">' in doc


def test_description_is_escaped():
    doc = _document(description='Say "hi" & <bye>', props={})
    assert '<meta name="description" content="Say &quot;hi&quot; &amp; &lt;bye&gt;">' in doc


def test_robots_emitted_when_given():
    doc = _document(robots="noindex, nofollow", props={})
    assert '<meta name="robots" content="noindex, nofollow">' in doc


def test_no_robots_meta_by_default():
    doc = _document(props={})
    assert 'name="robots"' not in doc


# --- Canonical ------------------------------------------------------------


def test_canonical_absolutized_against_app_url(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.test")
    doc = _document(canonical="/projects/alpha", props={})
    assert '<link rel="canonical" href="https://app.test/projects/alpha">' in doc


def test_absolute_canonical_passes_through(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.test")
    doc = _document(canonical="https://cdn.app.test/weird", props={})
    assert '<link rel="canonical" href="https://cdn.app.test/weird">' in doc


def test_canonical_skipped_when_base_unresolvable(monkeypatch):
    monkeypatch.setenv("APP_URL", "")
    doc = _document(canonical="/projects/alpha", props={})
    assert 'rel="canonical"' not in doc


# --- Open Graph / Twitter -------------------------------------------------


def test_og_defaults_with_description_and_image(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.test")
    monkeypatch.setenv("APP_NAME", "Fastplace Demo")
    doc = _document(
        title="Project Alpha",
        description="All about Alpha",
        image="/covers/alpha.png",
        props={},
    )
    assert '<meta property="og:title" content="Project Alpha">' in doc
    assert '<meta property="og:description" content="All about Alpha">' in doc
    assert '<meta property="og:image" content="https://app.test/covers/alpha.png">' in doc
    assert '<meta property="og:type" content="website">' in doc
    assert '<meta property="og:site_name" content="Fastplace Demo">' in doc
    assert '<meta name="twitter:card" content="summary_large_image">' in doc


def test_twitter_card_summary_without_image():
    doc = _document(description="plain", props={})
    assert '<meta name="twitter:card" content="summary">' in doc
    assert "og:image" not in doc


def test_og_url_matches_canonical(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.test")
    doc = _document(canonical="/projects/alpha", props={})
    assert '<meta property="og:url" content="https://app.test/projects/alpha">' in doc


def test_og_kwargs_override_fallbacks(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.test")
    doc = _document(
        title="Page Title",
        canonical="/p/1",
        og={"title": "Share Card", "type": "article"},
        props={},
    )
    assert '<meta property="og:title" content="Share Card">' in doc
    assert '<meta property="og:type" content="article">' in doc
    # The plain title tag keeps the page title — only og:title is overridden.
    assert "<title>Page Title</title>" in doc


def test_extra_og_keys_pass_through(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.test")
    doc = _document(og={"locale": "en_US"}, props={})
    assert '<meta property="og:locale" content="en_US">' in doc


def test_og_values_are_escaped():
    doc = _document(og={"title": 'A "quoted" <thing>'}, props={})
    assert '<meta property="og:title" content="A &quot;quoted&quot; &lt;thing&gt;">' in doc


# --- Raw head tags --------------------------------------------------------


def test_head_tags_appended_verbatim():
    doc = _document(head_tags=['<link rel="alternate" hreflang="bn" href="/bn">'], props={})
    assert '<link rel="alternate" hreflang="bn" href="/bn">' in doc


def test_head_tags_rendered_before_csrf_meta():
    doc = _document(
        head_tags=["<meta name=x>"],
        props={},
        session={"_token": "tok-1"},
    )
    head = doc.split("</head>")[0]
    assert head.index("<meta name=x>") < head.index('name="csrf-token"')


# --- Bridge mode ----------------------------------------------------------


def test_head_kwargs_ignored_in_bridge_mode():
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [(b"x-fastplace-request", b"true")],
        "query_string": b"",
    }
    response = render(
        FpRequest(SRequest(scope)),
        component="Projects/Show",
        title="SEO Title",
        description="d",
        props={},
    )
    body = response.body.decode()
    assert "SEO Title" not in body
    assert '"component"' in body


# --- noscript fallback ----------------------------------------------------


def test_noscript_fallback_present_with_content():
    doc = _document(title="Project Alpha", props={})
    assert "<noscript>" in doc
    assert "Project Alpha" in doc.split("<noscript>", 1)[1]
    assert "JavaScript" in doc.split("<noscript>", 1)[1]


def test_noscript_precedes_mount_point():
    doc = _document(props={})
    assert doc.index("<noscript>") < doc.index('<div id="fastplace"')


def test_csrf_meta_still_injected_with_head_block():
    doc = _document(description="d", props={}, session={"_token": "tok-9"})
    assert '<meta name="csrf-token" content="tok-9">' in doc


# --- Sample app controllers use the surface -------------------------------


def _sample_app():
    import sys
    from pathlib import Path

    from fastplace.http import get_app

    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from routes.api import router as api_router
    from routes.web import router as web_router

    return get_app(routes=web_router, api_routes=api_router, config={})


async def _html(path: str) -> str:
    import httpx

    transport = httpx.ASGITransport(app=_sample_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200
    return response.text


async def test_login_page_carries_explicit_title_and_noindex():
    doc = await _html("/login")
    assert "<title>Log in</title>" in doc
    assert '<meta name="robots" content="noindex">' in doc


async def test_home_page_carries_seo_head():
    doc = await _html("/")
    assert "<title>Home</title>" in doc
    assert '<meta name="description" content=' in doc
    assert 'rel="canonical"' in doc


async def test_about_page_title():
    doc = await _html("/about")
    assert "<title>About</title>" in doc
