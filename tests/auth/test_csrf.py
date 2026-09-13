"""T4.4 — CSRF middleware: token issuance + unsafe-method validation."""

from __future__ import annotations

from types import SimpleNamespace

from fastplace.http.render import render

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


class TestCsrfValidation:
    async def test_unsafe_post_without_a_token_is_rejected(self, auth_client):
        response = await auth_client.post("/submit")
        assert response.status_code == 403
        assert "CSRF" in response.json()["error"]

    async def test_unsafe_post_with_a_valid_header_token_passes(self, auth_client):
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post("/submit", headers={"X-Fastplace-CSRF-Token": token})
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    async def test_mismatched_token_is_rejected(self, auth_client):
        await auth_client.get("/me")  # session now holds the real token
        response = await auth_client.post(
            "/submit", headers={"X-Fastplace-CSRF-Token": "forged-token-value"}
        )
        assert response.status_code == 403

    async def test_json_body_token_is_accepted(self, auth_client):
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post("/submit", json={"_token": token, "payload": 1})
        assert response.status_code == 200

    async def test_form_encoded_body_token_is_accepted(self, auth_client):
        page = await auth_client.get("/me")
        token = page.headers["X-Fastplace-CSRF-Token"]
        response = await auth_client.post(
            "/submit", data={"_token": token, "payload": 1}
        )
        assert response.status_code == 200

    async def test_valid_bearer_token_bypasses_csrf(self, auth_client, registered_user, monkeypatch):
        # Only a genuinely token-authenticated request (stateless edge) is
        # exempt — mint a real token for the registered user.
        monkeypatch.setenv("APP_KEY", SECRET)
        from fastplace.auth.guards import TokenGuard
        from fastplace.auth.providers import dict_provider

        guard = TokenGuard(dict_provider, secret=SECRET)
        token = guard.issue_for(registered_user)
        response = await auth_client.post(
            "/submit", headers={"Authorization": f"Bearer {token}"}
        )
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
        assert response.status_code == 403

    async def test_invalid_bearer_without_session_is_rejected_as_unauthorized(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setenv("APP_KEY", SECRET)
        response = await auth_client.post(
            "/submit", headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401

    async def test_except_paths_are_exempted(self, auth_client, monkeypatch):
        monkeypatch.setenv("CSRF_EXCEPT", "/webhook/*")
        response = await auth_client.post("/webhook/order")
        assert response.status_code == 200  # the exempt route itself passes

    async def test_except_glob_does_not_leak_past_the_prefix(self, auth_client, monkeypatch):
        monkeypatch.setenv("CSRF_EXCEPT", "/webhook/*")
        response = await auth_client.post("/webhookify")
        assert response.status_code == 403  # /webhookify is a different path

    async def test_non_exempt_paths_still_protected_under_csrf_except(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setenv("CSRF_EXCEPT", "/webhook/*")
        response = await auth_client.post("/submit")
        assert response.status_code == 403

    async def test_delete_and_patch_are_also_guarded(self, auth_client):
        for method in ("delete", "patch", "put"):
            response = await auth_client.request(method.upper(), "/submit")
            assert response.status_code == 403, method


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
