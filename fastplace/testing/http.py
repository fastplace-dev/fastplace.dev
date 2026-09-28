"""HTTP client fake — stub responses, record requests, assert on both.

The framework ships no outbound HTTP client (apps bring their own — typically
``httpx``). This fake is the injectable stand-in for whatever client a service
accepts: construct it, stub what the test's URLs should answer with
``respond()``, hand it to the code under test, and assert on what that code
asked for. For intercepting a real ``httpx`` client at transport level, respx
remains the canonical tool.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any


class FakeResponse:
    """A canned response: status, body, headers — nothing else exists."""

    def __init__(
        self,
        status: int = 200,
        *,
        json: Any | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._json = json
        self.text = text
        self.headers: dict[str, str] = headers or {}

    @property
    def status_code(self) -> int:
        return self.status

    async def json(self) -> Any:
        return self._json


@dataclass
class RecordedRequest:
    """One request the code under test made through the fake."""

    method: str
    url: str
    params: dict[str, Any] | None = None
    json: Any | None = None
    data: Any | None = None
    headers: dict[str, str] = field(default_factory=dict)


class FakeHttp:
    """A duck-typed async HTTP client with a recording deck.

    ``respond(method, url_pattern, response)`` stacks stubs — later stubs win,
    so a broad ``"https://api.test/*"`` baseline can be overridden per test.
    Patterns are :mod:`fnmatch` globs matched case-sensitively against the
    full URL. A request that matches no stub is a loud ``AssertionError``:
    silent 404s from a missing stub are exactly what a test fake must not
    produce.
    """

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._stubs: list[tuple[str, str, FakeResponse]] = []

    # -- stubbing ----------------------------------------------------------------

    def respond(self, method: str, url_pattern: str, response: FakeResponse | None = None) -> None:
        """Stack a stub; ``response`` defaults to an empty 200."""
        self._stubs.append((method.upper(), url_pattern, response or FakeResponse(200)))

    # -- the client surface --------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        data: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.requests.append(
            RecordedRequest(
                method=method.upper(),
                url=url,
                params=params,
                json=json,
                data=data,
                headers=headers or {},
            )
        )
        for stub_method, pattern, response in reversed(self._stubs):
            if stub_method == method.upper() and fnmatch.fnmatchcase(url, pattern):
                return response
        raise AssertionError(
            f"FakeHttp has no stub for {method.upper()} {url} — add one with "
            f"fake.respond({method.upper()!r}, {url!r}, FakeResponse(...))"
        )

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("PUT", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("DELETE", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("HEAD", url, **kwargs)

    async def options(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._request("OPTIONS", url, **kwargs)

    # -- assertions -----------------------------------------------------------------

    def _filter(
        self,
        method: str | None,
        url: str | None,
        match: dict[str, Any] | None,
    ) -> list[RecordedRequest]:
        return [
            request
            for request in self.requests
            if (method is None or request.method == method.upper())
            and (url is None or fnmatch.fnmatchcase(request.url, url))
            and (
                match is None
                or (
                    isinstance(request.json, dict)
                    and all(request.json.get(key) == value for key, value in match.items())
                )
            )
        ]

    def assert_requested(
        self,
        method: str | None = None,
        url: str | None = None,
        *,
        match: dict[str, Any] | None = None,
        times: int | None = None,
    ) -> list[RecordedRequest]:
        matches = self._filter(method, url, match)
        if times is not None:
            if len(matches) != times:
                raise AssertionError(
                    f"expected {times} matching request(s), got {len(matches)}; "
                    f"requests: {self._describe()}"
                )
        elif not matches:
            raise AssertionError(
                f"expected at least one request matching ({method!r}, {url!r}, {match!r}); "
                f"requests: {self._describe()}"
            )
        return matches

    def assert_not_requested(
        self,
        method: str | None = None,
        url: str | None = None,
        *,
        match: dict[str, Any] | None = None,
    ) -> None:
        matches = self._filter(method, url, match)
        if matches:
            raise AssertionError(
                f"expected no matching request, found {len(matches)}: {self._describe()}"
            )

    def assert_request_count(self, count: int) -> None:
        if len(self.requests) != count:
            raise AssertionError(
                f"expected {count} request(s), got {len(self.requests)}: {self._describe()}"
            )

    def _describe(self) -> str:
        if not self.requests:
            return "<nothing>"
        return "\n".join(f"  - {r.method} {r.url}" for r in self.requests)


__all__ = ["FakeHttp", "FakeResponse", "RecordedRequest"]
