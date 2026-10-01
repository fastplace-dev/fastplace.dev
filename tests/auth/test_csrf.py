"""T4.4 — CSRF middleware: token issuance + unsafe-method validation."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from fastplace.http.render import render
from tests.auth.conftest import bootstrap_csrf, build_auth_app

SECRET = "test-app-key-not-for-production-use-only"


class TestCsrfIssuance:
    async def test_safe_requests_receive_a_csrf_token_header(self, auth_client):
        response = await auth_client.get("/me")
        token = response.headers.get("X-Fastplace-CSRF-Token")
        assert token and len(token) >= 32

    async def test_the_token_is_stable_within_a_session(self, auth_client):
        first = await auth_client.get("/me")
        second = await auth_client.get("/me")
        assert first.headers["X-Fastplace-CSRF-Token"] == (second.headers["X-Fastplace-CSRF-Token"])

    async def test_first_page_load_embeds_the_meta_tag(self, auth_client):
        # The very first GET of a fresh session must already carry the token
        # in the HTML — the bridge reads <meta name="csrf-token">, and the
        # first POST of a real visit happens right after this load.
        response = await auth_client.get("/page")
        assert response.status_code == 200
        assert '<meta name="csrf-token"' in response.text

    async def test_page_props_carry_the_csrf_token(self, auth_client):
        """No-JS forms cannot read <meta> — render() also injects the token
        into the page props so hidden ``_token`` inputs can use it."""
        import json as json_module

        bridge = await auth_client.get("/page", headers={"X-Fastplace-Request": "true"})
        assert bridge.status_code == 200
        token = bridge.headers["X-Fastplace-CSRF-Token"]
        assert bridge.json()["props"]["csrf_token"] == token

        html = await auth_client.get("/page")
        payload = html.text.split('data-page="', 1)[1].split('"', 1)[0]
        import html as html_module

        props = json_module.loads(html_module.unescape(payload))["props"]
        assert props["csrf_token"] == token


class TestCsrfValidation:
    async def test_unsafe_post_without_a_token_is_rejected(self, auth_client):
        response = await auth_client.post("/submit")
        assert response.status_code == 419
        assert "CSRF" in response.json()["message"]

    async def test_unsafe_post_with_a_valid_header_token_passes(self, auth_client):
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post(
            "/submit", headers={"X-Fastplace-CSRF-Token": token}, data={"payload": 1}
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True, "payload": "1"}

    async def test_mismatched_token_is_rejected(self, auth_client):
        await auth_client.get("/me")  # session now holds the real token
        response = await auth_client.post(
            "/submit", headers={"X-Fastplace-CSRF-Token": "forged-token-value"}
        )
        assert response.status_code == 419

    async def test_json_body_token_is_accepted(self, auth_client):
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post("/submit", json={"_token": token, "payload": 1})
        assert response.status_code == 200

    async def test_form_encoded_body_token_is_accepted(self, auth_client):
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post("/submit", data={"_token": token, "payload": 1})
        assert response.status_code == 200

    async def test_csrf_check_does_not_drain_the_body(self, auth_client):
        """A form POST with a valid _token must still reach the controller."""
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post("/submit", data={"_token": token, "payload": 1})
        assert response.status_code == 200
        # The controller read the same form after the middleware parsed it.
        assert response.json()["payload"] == "1"

    async def test_valid_bearer_token_bypasses_csrf(
        self, auth_client, registered_user, monkeypatch
    ):
        # Only a genuinely token-authenticated request (stateless edge) is
        # exempt — mint a real token for the registered user.
        monkeypatch.setenv("APP_KEY", SECRET)
        from fastplace.auth.guards import TokenGuard
        from fastplace.auth.providers import dict_provider

        guard = TokenGuard(dict_provider, secret=SECRET)
        token = guard.issue_for(registered_user)
        response = await auth_client.post("/submit", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    async def test_authorization_header_alone_does_not_bypass_csrf(self, auth_client):
        # A session-authenticated request carrying any Authorization value
        # (Basic junk, gateway-injected header) must still pass the CSRF
        # check — exemption is tied to token authentication, not header
        # presence (OWASP CSRF: fail closed on mixed credentials).
        await auth_client.get("/me")  # establish the session + token
        response = await auth_client.post(
            "/submit", headers={"Authorization": "Basic dXNlcjpwd2Q="}
        )
        assert response.status_code == 419

    async def test_invalid_bearer_without_session_is_rejected_as_unauthorized(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setenv("APP_KEY", SECRET)
        response = await auth_client.post("/submit", headers={"Authorization": "Bearer not.a.jwt"})
        assert response.status_code == 401

    async def test_except_paths_are_exempted(self, auth_client, monkeypatch):
        monkeypatch.setenv("CSRF_EXCEPT", "/webhook/*")
        response = await auth_client.post("/webhook/order")
        assert response.status_code == 200  # the exempt route itself passes

    async def test_except_glob_does_not_leak_past_the_prefix(self, auth_client, monkeypatch):
        monkeypatch.setenv("CSRF_EXCEPT", "/webhook/*")
        response = await auth_client.post("/webhookify")
        assert response.status_code == 419  # /webhookify is a different path

    async def test_non_exempt_paths_still_protected_under_csrf_except(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setenv("CSRF_EXCEPT", "/webhook/*")
        response = await auth_client.post("/submit")
        assert response.status_code == 419

    async def test_non_ascii_header_token_is_rejected_not_a_500(self):
        # Starlette decodes header bytes as latin-1: byte 0xFC arrives as the
        # str "ü", and hmac.compare_digest raises TypeError on non-ASCII strs
        # (signing.py's encoded-bytes precedent). A forged token must
        # mismatch with 419, never crash the request into a 500.
        transport = httpx.ASGITransport(app=build_auth_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await bootstrap_csrf(client)
            response = await client.post("/submit", headers={b"X-Fastplace-CSRF-Token": b"\xfc"})
        assert response.status_code == 419

    async def test_non_ascii_form_token_is_rejected_not_a_500(self, auth_client):
        # Same bug class through the form field: a URL-decoded "%C3%BC"
        # yields a non-ASCII str that must mismatch, not raise TypeError.
        await bootstrap_csrf(auth_client)
        response = await auth_client.post("/submit", data={"_token": "ü", "payload": 1})
        assert response.status_code == 419

    async def test_419_envelope_advertises_a_token_the_client_can_adopt(self, auth_client):
        # adoptCsrfToken recovers from error responses: a mismatch must
        # yield a 419 carrying a usable X-Fastplace-CSRF-Token, and the
        # immediate retry with it succeeds — no manual page reload.
        await bootstrap_csrf(auth_client)
        rejected = await auth_client.post(
            "/submit", headers={"X-Fastplace-CSRF-Token": "stale-token"}
        )
        assert rejected.status_code == 419
        fresh = rejected.headers.get("X-Fastplace-CSRF-Token")
        assert fresh and len(fresh) >= 32
        retried = await auth_client.post(
            "/submit", headers={"X-Fastplace-CSRF-Token": fresh}, data={"payload": 1}
        )
        assert retried.status_code == 200

    async def test_delete_and_patch_are_also_guarded(self, auth_client):
        for method in ("delete", "patch", "put"):
            response = await auth_client.request(method.upper(), "/submit")
            assert response.status_code == 419, method


class TestCsrfRedirectBack:
    """A browser form post with a stale token redirects back with flashed
    errors — the same no-JS contract validation failures already honor —
    while bridge and API clients keep the 419 JSON envelope."""

    BROWSER_HEADERS = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml",
    }

    async def test_browser_post_with_a_stale_token_redirects_back(self, auth_client):
        await auth_client.get("/page")  # session + token the form came from
        response = await auth_client.post(
            "/submit",
            data={"_token": "expired-token-value"},
            headers={**self.BROWSER_HEADERS, "Referer": "http://test/page"},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "http://test/page"

    async def test_the_redirect_back_flashes_the_token_error_once(self, auth_client):
        import html as html_module
        import json as json_module

        await auth_client.get("/page")
        await auth_client.post(
            "/submit",
            data={"_token": "expired-token-value"},
            headers=self.BROWSER_HEADERS,
        )
        page = await auth_client.get("/page", headers={"Accept": "text/html"})
        payload = page.text.split('data-page="', 1)[1].split('"', 1)[0]
        props = json_module.loads(html_module.unescape(payload))["props"]
        assert props["errors"] == {"_token": ["The page has expired. Please try again."]}
        again = await auth_client.get("/page", headers={"Accept": "text/html"})
        assert "errors" not in again.text.split('data-page="', 1)[1].split('"', 1)[0]

    async def test_bridge_post_keeps_the_419_envelope(self, auth_client):
        await auth_client.get("/page")
        response = await auth_client.post(
            "/submit",
            data={"_token": "expired-token-value"},
            headers={"X-Fastplace-Request": "true"},
        )
        assert response.status_code == 419
        assert "CSRF" in response.json()["message"]

    async def test_json_client_keeps_the_419_envelope(self, auth_client):
        await auth_client.get("/page")
        response = await auth_client.post(
            "/submit",
            json={"_token": "expired-token-value"},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 419


class TestCsrfRenderIntegration:
    def _request(self, session: dict) -> SimpleNamespace:
        return SimpleNamespace(
            session=session,
            full_path="/dashboard",
            is_bridge=False,
            starlette=SimpleNamespace(scope={}),
        )

    def test_render_embeds_the_csrf_meta_tag_when_a_token_exists(self):
        token = "meta-tag-token-1234567890abcdef"
        response = render(self._request({"_token": token}), component="Dashboard/Index", props={})
        assert f'<meta name="csrf-token" content="{token}">' in response.body.decode()

    def test_render_omits_the_meta_tag_without_a_session_token(self):
        response = render(self._request({}), component="Dashboard/Index", props={})
        assert 'name="csrf-token"' not in response.body.decode()
