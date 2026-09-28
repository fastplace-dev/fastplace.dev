"""Fluent HTTP test surface — ``TestResponse`` and ``TestClient``.

``TestClient`` is a drop-in ``httpx.AsyncClient`` whose responses come back
as :class:`TestResponse`: the same object, plus assertion helpers that read
like the test's intent instead of status-code arithmetic.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

_MISSING: Any = object()


class TestResponse(httpx.Response):
    """An ``httpx.Response`` with assertion helpers for app test suites."""

    # The Test prefix makes pytest try to collect this as a test class.
    __test__ = False

    @classmethod
    def wrap(cls, response: httpx.Response) -> TestResponse:
        """Rebuild a consumed ``httpx.Response`` as a ``TestResponse``.

        The transport has already materialized status, headers, and body, so
        the copy is cheap and safe to assert on after the fact. The body is
        passed decoded, so transport framing headers must not travel with it:
        a kept ``Content-Encoding`` would make httpx decode plain bytes again
        (any page over the compression threshold crashes with ``zlib.error``)
        and a kept ``Content-Length`` would disagree with the decoded size.
        """
        headers = httpx.Headers(response.headers)
        headers.pop("Content-Encoding", None)
        headers.pop("Content-Length", None)
        return cls(
            response.status_code,
            request=response.request,
            headers=headers,
            content=response.content,
            history=response.history,
            extensions=response.extensions,
        )

    # -- status -----------------------------------------------------------

    def assert_ok(self) -> TestResponse:
        """Assert a 2xx status."""
        if not (200 <= self.status_code < 300):
            raise AssertionError(
                f"expected a 2xx response, got {self.status_code}\n{self.text[:500]}"
            )
        return self

    def assert_status(self, status: int) -> TestResponse:
        if self.status_code != status:
            raise AssertionError(
                f"expected status {status}, got {self.status_code}\n{self.text[:500]}"
            )
        return self

    def assert_forbidden(self) -> TestResponse:
        return self.assert_status(403)

    def assert_not_found(self) -> TestResponse:
        return self.assert_status(404)

    def assert_unauthorized(self) -> TestResponse:
        return self.assert_status(401)

    def assert_redirect_to(self, expected: str) -> TestResponse:
        """Assert a 3xx redirect whose Location matches ``expected``.

        A bare path (no ``?``) matches the Location's path only — query
        strings are usually incidental. A path+query matches both.
        """
        if not 300 <= self.status_code < 400:
            raise AssertionError(f"expected a redirect, got status {self.status_code}")
        location = self.headers.get("location", "")
        expected_parts = urlsplit(expected)
        location_parts = urlsplit(location)
        if expected_parts.path != location_parts.path or (
            expected_parts.query and expected_parts.query != location_parts.query
        ):
            raise AssertionError(f"expected redirect to {expected!r}, got {location!r}")
        return self

    def assert_validated(self, key: str | None = None) -> TestResponse:
        """Assert the framework's 422 envelope (``message`` + ``errors``).

        With ``key``, additionally assert at least one error names it.
        """
        self.assert_status(422)
        errors = self.json().get("errors")
        if not isinstance(errors, dict):
            raise AssertionError(f"expected a 422 errors envelope, got: {self.text[:500]}")
        if key is not None and key not in errors:
            raise AssertionError(
                f"expected a validation error on {key!r}, got keys: {sorted(errors)}"
            )
        return self

    # -- body -------------------------------------------------------------

    def json_path(self, path: str) -> Any:
        """Read a dotted JSON path; ``items.0.name`` indexes lists too."""
        value: Any = self.json()
        walked: list[str] = []
        for part in path.split("."):
            walked.append(part)
            try:
                if isinstance(value, list):
                    value = value[int(part)]
                else:
                    value = value[part]
            except (KeyError, IndexError, TypeError, ValueError):
                raise AssertionError(f"path {'.'.join(walked)!r} not found in JSON body") from None
        return value

    def assert_json_path(self, path: str, expected: Any = _MISSING) -> Any:
        """Assert a dotted path resolves (and equals ``expected`` when given).

        Returns the resolved value so a test can drill deeper without
        re-walking the path.
        """
        value = self.json_path(path)
        if expected is not _MISSING and value != expected:
            raise AssertionError(f"{path} = {value!r}, expected {expected!r}")
        return value

    def assert_json(self, subset: dict[str, Any]) -> TestResponse:
        """Assert the JSON body contains ``subset`` (top-level keys)."""
        body = self.json()
        for key, expected in subset.items():
            if key not in body or body[key] != expected:
                raise AssertionError(
                    f"JSON body does not match subset at {key!r}: "
                    f"expected {expected!r}, got {body.get(key, '<missing>')!r}"
                )
        return self

    def assert_see(self, needle: str) -> TestResponse:
        if needle not in self.text:
            raise AssertionError(f"body does not contain {needle!r}:\n{self.text[:500]}")
        return self

    def assert_dont_see(self, needle: str) -> TestResponse:
        if needle in self.text:
            raise AssertionError(f"body unexpectedly contains {needle!r}")
        return self

    def assert_header(self, name: str, value: str) -> TestResponse:
        actual = self.headers.get(name)
        if actual != value:
            raise AssertionError(f"expected header {name}={value!r}, got {actual!r}")
        return self


class TestClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` whose responses are ``TestResponse`` objects."""

    async def request(self, method: str, url: str | httpx.URL, **kwargs: Any) -> TestResponse:  # type: ignore[override]
        return TestResponse.wrap(await super().request(method, url, **kwargs))
