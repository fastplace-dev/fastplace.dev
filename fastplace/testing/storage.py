"""Storage fake — the Disk contract over a dict, plus assertions.

The blueprint's prescribed test double: subclass :class:`~fastplace.storage.Disk`,
implement the primitives over a dict, inherit the derived ops (``text``,
``missing``, ``move``, ``temporary_url``) for free. Nothing ever touches the
filesystem, so a test can exercise upload/report/export code without a
sandbox — and the ``storage_fake`` fixture points the process-wide
``disk()`` at one.
"""

from __future__ import annotations

import time

from fastplace.errors import NotFoundError
from fastplace.storage import Disk


class FakeStorage(Disk):
    """An in-memory disk: the real surface, zero filesystem."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._mtimes: dict[str, float] = {}

    # -- primitives -----------------------------------------------------------

    async def put(self, path: str, content: bytes | str) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        self._files[path] = data
        self._mtimes[path] = time.time()

    async def get(self, path: str) -> bytes:
        if path not in self._files:
            raise NotFoundError(f"storage file not found: {path!r}")
        return self._files[path]

    async def exists(self, path: str) -> bool:
        if path in self._files:
            return True
        # Directory semantics: "reports" exists when any file lives under it.
        prefix = path.rstrip("/") + "/"
        return any(key.startswith(prefix) for key in self._files)

    async def delete(self, path: str) -> None:
        self._files.pop(path, None)
        self._mtimes.pop(path, None)

    async def copy(self, source: str, destination: str) -> None:
        if source not in self._files:
            raise NotFoundError(f"storage file not found: {source!r}")
        self._files[destination] = self._files[source]
        self._mtimes[destination] = time.time()

    async def size(self, path: str) -> int:
        if path not in self._files:
            raise NotFoundError(f"storage file not found: {path!r}")
        return len(self._files[path])

    async def last_modified(self, path: str) -> float | None:
        return self._mtimes.get(path)

    async def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        prefix = directory.rstrip("/") + "/"
        if recursive:
            return sorted(key for key in self._files if key.startswith(prefix))
        depth = prefix.count("/")
        return sorted(
            key for key in self._files if key.startswith(prefix) and key.count("/") == depth
        )

    async def url(self, path: str) -> str:
        return f"/storage/{path}"

    # -- assertions -----------------------------------------------------------

    def stored(self) -> list[str]:
        """Every stored path, sorted."""
        return sorted(self._files)

    def assert_stored(self, path: str, *, content: bytes | str | None = None) -> None:
        if path not in self._files:
            raise AssertionError(
                f"expected {path!r} to be stored; stored: {self.stored() or '<nothing>'}"
            )
        if content is not None:
            actual = self._files[path]
            expected = content.encode("utf-8") if isinstance(content, str) else content
            if actual != expected:
                raise AssertionError(f"expected {path!r} to contain {expected!r}, found {actual!r}")

    def assert_missing(self, path: str) -> None:
        if path in self._files:
            raise AssertionError(f"expected {path!r} to be missing, but it is stored")

    def assert_stored_count(self, count: int) -> None:
        if len(self._files) != count:
            raise AssertionError(
                f"expected {count} stored file(s), found {len(self._files)}: {self.stored()}"
            )

    def assert_nothing_stored(self) -> None:
        self.assert_stored_count(0)


__all__ = ["FakeStorage"]
