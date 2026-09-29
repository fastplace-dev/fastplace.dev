"""Storage — a driver-agnostic filesystem abstraction behind one async surface.

``disk()`` returns the named disk selected by ``STORAGE_DISK`` (default
``local``). Every driver — today :class:`LocalDisk`, tomorrow an S3 adapter —
implements the same :class:`Disk` interface, so services read and write files
without caring where the bytes physically live. The ABC itself is the
cloud-ready contract: an S3 adapter implements ``Disk`` with a presigned
``temporary_url``; nothing else about the surface changes.

Two decisions are load-bearing:

- **Signed URLs belong to cloud disks.** Presigning requires authority over
  the object store, which a local disk does not have. ``LocalDisk.
  temporary_url`` therefore refuses honestly (:class:`StorageNotSupported`)
  instead of faking a signature — private local files are served through an
  app-level signed route built on :mod:`fastplace.auth.signing` (sign the
  path, verify on a dedicated route, then stream the file).
- **Every path is containment-checked before any filesystem call.** Paths are
  disk-relative, ``/``-separated, and resolved against the disk root; an
  escape ("../", absolute, drive-qualified, backslash, symlinked-out) raises
  :class:`StoragePathError` before a byte is touched.

Blocking local IO runs on the default executor via ``asyncio.to_thread`` —
the public surface stays await-only and large reads never stall the loop.
Test doubles stay trivial: subclass :class:`Disk`, implement the primitives
over a dict, inherit the derived ops (``text``, ``missing``, ``move``,
``temporary_url``) for free.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from fastplace.config import config
from fastplace.errors import ConfigurationError, FastplaceError, NotFoundError

#: Windows drive prefixes ("C:") are never disk-relative — refused up front.
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class StoragePathError(FastplaceError):
    """A storage path escaped its disk root or was not a valid disk-relative path."""

    status_code = 400
    default_message = "Invalid storage path."


class StorageNotSupported(FastplaceError):
    """The storage driver cannot perform the requested operation (e.g. presigning)."""

    status_code = 500
    default_message = "Operation not supported by this storage driver."


def _not_found(path: str) -> NotFoundError:
    return NotFoundError(f"storage file not found: {path!r}")


def _as_bytes(content: bytes | str) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, bytes):
        return content
    raise TypeError(f"storage content must be bytes or str, got {type(content).__name__}")


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class Disk(ABC):
    """The async surface every storage driver implements.

    Primitives (abstract): ``put``, ``get``, ``exists``, ``delete``, ``copy``,
    ``size``, ``last_modified``, ``files``, ``url``. Derived ops have ABC
    defaults so drivers — and test fakes — implement only what they must:
    ``missing`` is ``not exists``, ``text`` is a decoded ``get``, ``move`` is
    ``copy`` then ``delete`` (source removed only after a successful copy),
    and ``temporary_url`` refuses by default (only cloud disks presign).
    ``json()`` is deliberately absent — ``json.loads(await disk.text(p))``
    covers it without growing the contract.
    """

    @abstractmethod
    async def put(self, path: str, content: bytes | str) -> None:
        """Write ``content`` (creating parent directories); overwrite on repeat."""
        ...

    @abstractmethod
    async def get(self, path: str) -> bytes:
        """Read the file's bytes; a missing file raises :class:`NotFoundError`."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Whether ``path`` exists (file or directory)."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Remove the file — deleting an absent file is a no-op, never an error."""
        ...

    @abstractmethod
    async def copy(self, source: str, destination: str) -> None:
        """Duplicate ``source`` to ``destination`` (parent auto-created, overwrite)."""
        ...

    @abstractmethod
    async def size(self, path: str) -> int:
        """File size in bytes; a missing file raises :class:`NotFoundError`."""
        ...

    @abstractmethod
    async def last_modified(self, path: str) -> float | None:
        """Unix mtime seconds, or ``None`` when the file is absent."""
        ...

    @abstractmethod
    async def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        """Disk-relative file paths under ``directory``, sorted (directories excluded)."""
        ...

    @abstractmethod
    async def url(self, path: str) -> str:
        """The public URL for ``path`` — pure path math, no existence check."""
        ...

    async def text(self, path: str) -> str:
        """The file decoded as UTF-8."""
        return (await self.get(path)).decode("utf-8")

    async def missing(self, path: str) -> bool:
        """Negation of :meth:`exists` — reads well in guards."""
        return not await self.exists(path)

    async def move(self, source: str, destination: str) -> None:
        """Relocate a file; the source survives a failed copy and dies after success."""
        await self.copy(source, destination)
        await self.delete(source)

    async def temporary_url(self, path: str, *, expires_in: int) -> str:
        """A time-limited URL — the cloud-disk contract (S3 presigns; local refuses)."""
        raise StorageNotSupported(
            "temporary_url requires a disk with presigning authority "
            "(an S3 adapter); local disks serve private files through an "
            "app-level signed route built on fastplace.auth.signing"
        )


# ---------------------------------------------------------------------------
# Local driver
# ---------------------------------------------------------------------------


class LocalDisk(Disk):
    """Filesystem disk rooted at one directory — the default driver.

    The root is anchored (resolved) at construction, so a relative config root
    like ``storage/app`` binds to the process CWD once and containment checks
    never drift mid-process. Serving ``url()`` paths is the app's choice: the
    conventional ``/storage`` prefix matches a StaticFiles mount over the same
    root — the framework computes URLs, the app decides what it exposes.
    """

    def __init__(self, root: str | Path, *, url_base: str = "/storage") -> None:
        self._root = Path(root).expanduser().resolve()
        self._url_base = url_base.rstrip("/")

    @property
    def root(self) -> Path:
        """The absolute anchor every operation is contained within."""
        return self._root

    # -- path guard ---------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Containment-checked absolute target — every operation funnels through here."""
        if not isinstance(path, str):
            raise StoragePathError(f"storage path must be a string, got {type(path).__name__}")
        cleaned = path.strip()
        if not cleaned:
            raise StoragePathError("storage path must not be empty")
        if "\\" in cleaned:
            raise StoragePathError(f"storage paths use '/' separators: {path!r}")
        if cleaned.startswith("/"):
            raise StoragePathError(f"storage paths are disk-relative: {path!r}")
        if _DRIVE_PREFIX.match(cleaned):
            raise StoragePathError(f"drive-qualified paths are not disk-relative: {path!r}")
        if "\x00" in cleaned:
            raise StoragePathError(f"storage path must not contain NUL: {path!r}")
        candidate = (self._root / cleaned).resolve()
        # resolve() follows symlinks, so a link out of the root resolves
        # outside and dies here — the escape check covers real paths only.
        if candidate != self._root and not candidate.is_relative_to(self._root):
            raise StoragePathError(f"path escapes the disk root: {path!r}")
        return candidate

    def _file(self, path: str) -> Path:
        """A path that must name a file — the disk root itself is never an object."""
        target = self._resolve(path)
        if target == self._root:
            raise StoragePathError(f"storage path must name a file, not the disk root: {path!r}")
        return target

    # -- primitives ---------------------------------------------------------

    async def put(self, path: str, content: bytes | str) -> None:
        target = self._file(path)
        payload = _as_bytes(content)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

        await asyncio.to_thread(_write)

    async def get(self, path: str) -> bytes:
        target = self._file(path)
        try:
            return await asyncio.to_thread(target.read_bytes)
        except FileNotFoundError:
            raise _not_found(path) from None

    async def exists(self, path: str) -> bool:
        return await asyncio.to_thread(self._resolve(path).exists)

    async def delete(self, path: str) -> None:
        target = self._file(path)
        await asyncio.to_thread(lambda: target.unlink(missing_ok=True))

    async def copy(self, source: str, destination: str) -> None:
        src = self._file(source)
        dst = self._file(destination)

        def _copy() -> None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

        try:
            await asyncio.to_thread(_copy)
        except FileNotFoundError:
            raise _not_found(source) from None

    async def size(self, path: str) -> int:
        target = self._file(path)
        try:
            return (await asyncio.to_thread(target.stat)).st_size
        except FileNotFoundError:
            raise _not_found(path) from None

    async def last_modified(self, path: str) -> float | None:
        target = self._resolve(path)

        def _mtime() -> float | None:
            try:
                return target.stat().st_mtime
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_mtime)

    async def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        base = self._resolve(directory)

        def _list() -> list[str]:
            if not base.is_dir():
                raise NotFoundError(f"storage directory not found: {directory!r}")
            pattern = "**/*" if recursive else "*"
            return sorted(
                found.relative_to(self._root).as_posix()
                for found in base.glob(pattern)
                if found.is_file()
            )

        return await asyncio.to_thread(_list)

    async def url(self, path: str) -> str:
        target = self._file(path)
        return f"{self._url_base}/{target.relative_to(self._root).as_posix()}"

    async def temporary_url(self, path: str, *, expires_in: int) -> str:
        raise StorageNotSupported(
            "local disks have no presigning authority — serve private files "
            "through an app-level signed route built on fastplace.auth.signing "
            "(sign the path, verify on a dedicated route, stream the file), or "
            "configure a cloud disk whose temporary_url presigns"
        )


# ---------------------------------------------------------------------------
# Factory — mirrors the cache() singleton style
# ---------------------------------------------------------------------------


def _build_disk(name: str, entry: dict[str, Any] | None) -> Disk:
    if not isinstance(entry, dict):
        raise ConfigurationError(f"unknown storage disk '{name}' — add it to STORAGE_DISKS")
    driver = entry.get("driver")
    if driver == "local":
        root = entry.get("root")
        if not root:
            raise ConfigurationError(f"storage disk '{name}' needs a 'root' directory")
        return LocalDisk(root)
    if driver == "s3":
        # Lazy import keeps aioboto3 an optional extra (fastplace[s3]).
        from fastplace.storage_s3 import S3Disk, _require_aioboto3

        _require_aioboto3()
        bucket = entry.get("bucket")
        if not bucket:
            raise ConfigurationError(f"storage disk '{name}' needs a 'bucket'")
        return S3Disk(
            bucket,
            region=entry.get("region"),
            endpoint_url=entry.get("endpoint_url"),
            prefix=entry.get("prefix", ""),
            public_base=entry.get("public_base"),
            public=bool(entry.get("public", False)),
        )
    raise ConfigurationError(
        f"unknown storage driver '{driver}' on disk '{name}' — 'local' and 's3' are the shipped drivers"
    )


class Storage:
    """Named-disk registry; ``disk()`` resolves and caches one Disk per name.

    Construct directly with ``disks`` to inject a registry without touching
    config — the shape test doubles and multi-disk apps use.
    """

    def __init__(
        self,
        disks: dict[str, dict[str, Any]] | None = None,
        default: str | None = None,
    ) -> None:
        if disks is None:
            configured = config("STORAGE_DISKS", default=None)
            disks = (
                configured
                if isinstance(configured, dict)
                else {"local": {"driver": "local", "root": "storage/app"}}
            )
        self._disks = disks
        self._default = default or str(config("STORAGE_DISK", default="local"))
        self._built: dict[str, Disk] = {}

    def disk(self, name: str | None = None) -> Disk:
        """The named disk (``STORAGE_DISK`` by default), built once and cached."""
        key = name or self._default
        if key not in self._built:
            self._built[key] = _build_disk(key, self._disks.get(key))
        return self._built[key]


_default_storage: Storage | None = None


def disk(name: str | None = None) -> Disk:
    """The process-wide storage disk (``STORAGE_DISK``, default ``local``)."""
    global _default_storage
    if _default_storage is None:
        _default_storage = Storage()
    return _default_storage.disk(name)


def reset_storage() -> None:
    """Drop the singleton — tests and config reloads."""
    global _default_storage
    _default_storage = None
