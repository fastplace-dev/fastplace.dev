"""S3Disk — the cloud storage driver over aioboto3, behind the same Disk ABC.

Every local-disk guarantee carries over; only the bytes move:

- **Containment before S3.** ``_key`` refuses the same hostile shapes
  ``LocalDisk._resolve`` refuses (non-string, empty, backslash, absolute,
  drive-qualified, NUL) and normalizes ``..`` segments on a stack — a path
  that would escape the configured key prefix dies as
  :class:`~fastplace.storage.StoragePathError` before any S3 call runs.
- **The dependency is optional.** aioboto3 ships as the ``s3`` extra and is
  imported lazily; without it, constructing the driver fails loud with the
  install hint. Injecting ``client_factory`` (tests, alternative clients)
  bypasses the guard — the dependency is the default factory's problem.
- **Clients live inside their context.** aioboto3 clients are only usable
  within ``async with``, so every primitive opens one from the factory,
  does its single call, and closes it.

Keys carry the configured ``prefix`` (e.g. ``"tenants/7"``) so many disks can
share one bucket without seeing each other's objects; listings are returned
disk-relative, so callers never see the prefix.
"""

from __future__ import annotations

import importlib.util
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastplace.errors import ConfigurationError, NotFoundError
from fastplace.storage import _DRIVE_PREFIX, Disk, StorageNotSupported, StoragePathError, _as_bytes

#: botocore error codes that mean "no such object" — checked duck-typed
#: (``exc.response["Error"]["Code"]``) so the driver never imports botocore.
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})

ClientFactory = Callable[[], Any]


def _require_aioboto3() -> None:
    """Fail loud with the extra's name when aioboto3 is not installed."""
    if importlib.util.find_spec("aioboto3") is None:
        raise ConfigurationError(
            "STORAGE_DISKS driver 's3' requires aioboto3 — pip install 'fastplace[s3]'"
        )


def _default_client_factory(
    bucket: str,
    region: str | None,
    endpoint_url: str | None,
) -> ClientFactory:
    """The real factory: aioboto3 session client, imported on first use."""

    def _open() -> Any:
        import aioboto3

        return aioboto3.Session().client("s3", region_name=region, endpoint_url=endpoint_url)

    return _open


def _is_s3_not_found(exc: BaseException) -> bool:
    """Duck-typed botocore ClientError not-found check — no botocore import."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if not isinstance(error, dict):
        return False
    return error.get("Code") in _NOT_FOUND_CODES


def _mtime_or_none(modified: Any) -> float | None:
    """head_object's LastModified as Unix seconds — naive stamps read as UTC."""
    if modified is None:
        return None
    stamp = modified if isinstance(modified, datetime) else None
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.timestamp()


class S3Disk(Disk):
    """S3-backed disk: one bucket, one optional key prefix, one URL policy.

    ``public_base`` makes :meth:`url` pure path math against a CDN/app base;
    ``public=True`` alone uses the bucket's vhost URL. Neither set means the
    disk holds private objects — :meth:`url` refuses and points at
    :meth:`temporary_url`, which presigns (commit pairing with the local
    disk's refusal to fake signatures).
    """

    def __init__(
        self,
        bucket: str,
        *,
        region: str | None = None,
        endpoint_url: str | None = None,
        prefix: str = "",
        public_base: str | None = None,
        public: bool = False,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not bucket:
            raise ConfigurationError("an S3 disk needs a bucket name")
        self._bucket = bucket
        self._region = region
        self._endpoint_url = endpoint_url
        self._prefix = prefix.strip("/")
        self._public_base = public_base.rstrip("/") if public_base else None
        self._public = public
        if client_factory is None:
            _require_aioboto3()
            client_factory = _default_client_factory(bucket, region, endpoint_url)
        self._client_factory = client_factory

    @property
    def bucket(self) -> str:
        """The bucket every operation targets."""
        return self._bucket

    # -- key guard ----------------------------------------------------------

    def _segments(self, path: str) -> list[str]:
        """Containment-checked normalized path segments.

        Mirrors LocalDisk's refusal order, then resolves ``.``/``..`` on a
        stack — popping an empty stack escapes the disk and dies here, so no
        S3 call can ever address a key outside the configured prefix.
        """
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
        segments: list[str] = []
        for part in cleaned.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not segments:
                    raise StoragePathError(f"path escapes the disk root: {path!r}")
                segments.pop()
            else:
                segments.append(part)
        return segments

    def _key(self, path: str) -> str:
        """The full S3 key for a file path — never empty, always under the prefix."""
        segments = self._segments(path)
        if not segments:
            raise StoragePathError(f"storage path must name an object, not the disk root: {path!r}")
        parts = [self._prefix] if self._prefix else []
        return "/".join([*parts, *segments])

    def _dir_prefix(self, directory: str) -> str:
        """The listing prefix for a directory — the disk root lists everything."""
        segments = self._segments(directory)
        parts = [self._prefix] if self._prefix else []
        parts.extend(segments)
        base = "/".join(parts)
        return f"{base}/" if base else ""

    def _relative(self, key: str) -> str:
        """Disk-relative form of a listed key (the prefix never leaks)."""
        if self._prefix and key.startswith(f"{self._prefix}/"):
            return key[len(self._prefix) + 1 :]
        return key

    # -- primitives ---------------------------------------------------------

    async def put(self, path: str, content: bytes | str) -> None:
        async with self._client_factory() as client:
            await client.put_object(
                Bucket=self._bucket, Key=self._key(path), Body=_as_bytes(content)
            )

    async def get(self, path: str) -> bytes:
        key = self._key(path)
        async with self._client_factory() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _is_s3_not_found(exc):
                    raise NotFoundError(f"storage file not found: {path!r}") from None
                raise
            return await response["Body"].read()

    async def exists(self, path: str) -> bool:
        segments = self._segments(path)
        key = (
            "/".join([self._prefix, *segments])
            if (self._prefix and segments)
            else "/".join(segments)
        )
        async with self._client_factory() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception as exc:
                if not _is_s3_not_found(exc):
                    raise
            # No such object — the path may still be a "directory": a prefix
            # with objects underneath (S3 has no real folders).
            base = f"{key}/" if key else ""
            if not key:
                return True  # the disk root always lists
            response = await client.list_objects_v2(Bucket=self._bucket, Prefix=base, MaxKeys=1)
            contents = response.get("Contents") or []
            common = response.get("CommonPrefixes") or []
            return bool(contents or common)

    async def delete(self, path: str) -> None:
        async with self._client_factory() as client:
            await client.delete_object(Bucket=self._bucket, Key=self._key(path))

    async def copy(self, source: str, destination: str) -> None:
        src_key = self._key(source)
        async with self._client_factory() as client:
            try:
                await client.copy_object(
                    Bucket=self._bucket,
                    Key=self._key(destination),
                    CopySource={"Bucket": self._bucket, "Key": src_key},
                )
            except Exception as exc:
                if _is_s3_not_found(exc):
                    raise NotFoundError(f"storage file not found: {source!r}") from None
                raise

    async def size(self, path: str) -> int:
        key = self._key(path)
        async with self._client_factory() as client:
            try:
                response = await client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _is_s3_not_found(exc):
                    raise NotFoundError(f"storage file not found: {path!r}") from None
                raise
            return int(response["ContentLength"])

    async def last_modified(self, path: str) -> float | None:
        key = self._key(path)
        async with self._client_factory() as client:
            try:
                response = await client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _is_s3_not_found(exc):
                    return None
                raise
            return _mtime_or_none(response.get("LastModified"))

    async def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        base = self._dir_prefix(directory)
        delimiter = None if recursive else "/"
        keys: list[str] = []
        saw_child_prefix = False
        token: str | None = None
        async with self._client_factory() as client:
            while True:
                kwargs: dict[str, Any] = {
                    "Bucket": self._bucket,
                    "Prefix": base,
                    "Delimiter": delimiter,
                }
                if token is not None:
                    kwargs["ContinuationToken"] = token
                response = await client.list_objects_v2(**kwargs)
                for entry in response.get("Contents") or []:
                    key = entry["Key"]
                    if key != base:
                        keys.append(self._relative(key))
                if response.get("CommonPrefixes"):
                    saw_child_prefix = True
                if not response.get("IsTruncated"):
                    break
                token = response.get("NextContinuationToken")
                if not token:
                    break
        if not keys and not saw_child_prefix:
            raise NotFoundError(f"storage directory not found: {directory!r}")
        return sorted(set(keys))

    async def url(self, path: str) -> str:
        key = self._key(path)
        if self._public_base:
            return f"{self._public_base}/{key}"
        if self._public:
            region = self._region or "us-east-1"
            return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"
        raise StorageNotSupported(
            "this S3 disk serves private objects — configure public_base (or "
            "public=True) for stable URLs, or use temporary_url which presigns"
        )

    async def temporary_url(self, path: str, *, expires_in: int) -> str:
        """A real presigned GET — the authority local disks refuse to fake.

        aiobotocore's ``generate_presigned_url`` is a coroutine (boto3's is
        not), so the awaitable is detected and awaited — the injected fake
        mirrors the coroutine shape.
        """
        key = self._key(path)
        async with self._client_factory() as client:
            result = client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            if inspect.isawaitable(result):
                result = await result
            return result
