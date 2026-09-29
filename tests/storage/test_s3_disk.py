"""S3Disk — the cloud storage driver, against a fake S3 client.

No network, ever: the client is an in-process fake injected through the
``client_factory`` seam (the same seam a real deployment leaves to the lazy
aioboto3 import). The key policy mirrors LocalDisk containment — a hostile
key ("../x", absolute, backslash, drive-qualified) must die as StoragePathError
before any S3 call runs, so a hostile key can never address objects outside
the disk prefix.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from fastplace.errors import ConfigurationError, NotFoundError
from fastplace.storage import Storage, StorageNotSupported, StoragePathError, reset_storage
from fastplace.storage_s3 import S3Disk

STAMP = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


class FakeS3Body:
    """The read-once body shape aiobotocore hands back from get_object."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeS3Error(Exception):
    """Duck-typed botocore ClientError — the driver checks .response, no import."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response: dict[str, Any] = {"Error": {"Code": code}}


class FakeS3Client:
    """Dict-backed S3 client recording every call for assertion."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "FakeS3Client":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def _record(self, op: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((op, dict(kwargs)))

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self._record("put_object", {"Bucket": Bucket, "Key": Key, "Body": Body})
        self.objects[Key] = Body

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise FakeS3Error("NoSuchKey")
        self._record("get_object", {"Bucket": Bucket, "Key": Key})
        return {"Body": FakeS3Body(self.objects[Key])}

    async def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            raise FakeS3Error("404")
        self._record("head_object", {"Bucket": Bucket, "Key": Key})
        return {"ContentLength": len(self.objects[Key]), "LastModified": STAMP}

    async def delete_object(self, *, Bucket: str, Key: str) -> None:
        self._record("delete_object", {"Bucket": Bucket, "Key": Key})
        self.objects.pop(Key, None)

    async def copy_object(self, *, Bucket: str, Key: str, CopySource: dict[str, Any]) -> None:
        self._record("copy_object", {"Bucket": Bucket, "Key": Key, "CopySource": CopySource})
        source = CopySource["Key"]
        if source not in self.objects:
            raise FakeS3Error("404")
        self.objects[Key] = self.objects[source]

    async def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str,
        Delimiter: str | None = None,
        MaxKeys: int | None = None,
        ContinuationToken: str | None = None,
    ) -> dict[str, Any]:
        self._record(
            "list_objects_v2",
            {"Bucket": Bucket, "Prefix": Prefix, "Delimiter": Delimiter},
        )
        keys = sorted(k for k in self.objects if k.startswith(Prefix) and k != Prefix)
        contents = [{"Key": k} for k in keys]
        common: list[str] = []
        if Delimiter:
            trimmed: list[str] = []
            for key in keys:
                rest = key[len(Prefix):]
                if Delimiter in rest:
                    head = Prefix + rest.split(Delimiter, 1)[0] + Delimiter
                    if head not in common:
                        common.append(head)
                else:
                    trimmed.append(key)
            contents = [{"Key": k} for k in trimmed]
        return {"Contents": contents, "CommonPrefixes": [{"Prefix": p} for p in common]}

    async def generate_presigned_url(
        self, *, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        # aiobotocore's shape is a coroutine — the driver must await it.
        self._record(
            "generate_presigned_url",
            {"ClientMethod": ClientMethod, "Params": Params, "ExpiresIn": ExpiresIn},
        )
        return f"https://s3.example.com/{Params['Key']}?exp={ExpiresIn}"


class PagedFakeS3Client(FakeS3Client):
    """Serves list_objects_v2 one key per page — the pagination loop's proof.

    The continuation token is the next index, so the driver must actually
    hand it back to advance pages (a constant token would loop forever).
    """

    async def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        page = await super().list_objects_v2(**kwargs)
        contents = page["Contents"]
        if len(contents) <= 1:
            return page
        token = kwargs.get("ContinuationToken")
        start = int(token) if token is not None else 0
        batch = contents[start : start + 1]
        nxt = start + 1
        if nxt < len(contents):
            return {
                "Contents": batch,
                "CommonPrefixes": [],
                "IsTruncated": True,
                "NextContinuationToken": str(nxt),
            }
        return {"Contents": batch, "CommonPrefixes": []}


def make_disk(client: FakeS3Client, **kwargs: Any) -> S3Disk:
    return S3Disk("app-bucket", client_factory=lambda: client, **kwargs)


@pytest.fixture(autouse=True)
def _fresh_storage():
    reset_storage()
    yield
    reset_storage()


@pytest.fixture
def fake() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def s3(fake: FakeS3Client) -> S3Disk:
    return make_disk(fake)


# -- primitives ---------------------------------------------------------------


async def test_s3_disk_roundtrips_text_and_bytes(fake: FakeS3Client, s3: S3Disk) -> None:
    await s3.put("notes/hello.txt", "hello")
    assert await s3.get("notes/hello.txt") == b"hello"
    await s3.put("blob.bin", b"\x00\x01\xff")
    assert await s3.get("blob.bin") == b"\x00\x01\xff"
    assert fake.objects["notes/hello.txt"] == b"hello"
    assert fake.calls[0][1]["Bucket"] == "app-bucket"


async def test_s3_disk_put_rejects_non_string_non_bytes_content(s3: S3Disk) -> None:
    with pytest.raises(TypeError, match="bytes or str"):
        await s3.put("x.txt", 42)  # type: ignore[arg-type]


async def test_s3_disk_missing_read_raises_not_found_error(s3: S3Disk) -> None:
    with pytest.raises(NotFoundError, match="gone.txt"):
        await s3.get("gone.txt")


async def test_s3_disk_missing_size_raises_not_found(s3: S3Disk) -> None:
    with pytest.raises(NotFoundError, match="gone.txt"):
        await s3.size("gone.txt")


async def test_s3_disk_last_modified_returns_none_when_missing(s3: S3Disk) -> None:
    assert await s3.last_modified("gone.txt") is None


async def test_s3_disk_exists_covers_objects_and_directories(fake: FakeS3Client, s3: S3Disk) -> None:
    await s3.put("dir/a.txt", "a")
    assert await s3.exists("dir/a.txt") is True
    assert await s3.exists("dir") is True  # prefix with objects underneath
    assert await s3.exists("missing.txt") is False
    assert await s3.exists("missing-dir") is False


async def test_s3_disk_delete_is_lenient_when_absent(fake: FakeS3Client, s3: S3Disk) -> None:
    await s3.delete("never-there.txt")  # no raise
    await s3.put("here.txt", "x")
    await s3.delete("here.txt")
    assert "here.txt" not in fake.objects


async def test_s3_disk_size_returns_byte_count(s3: S3Disk) -> None:
    await s3.put("sized.bin", b"12345")
    assert await s3.size("sized.bin") == 5


async def test_s3_disk_last_modified_parses_head_object_timestamp(s3: S3Disk) -> None:
    await s3.put("stamped.txt", "x")
    assert await s3.last_modified("stamped.txt") == STAMP.timestamp()


async def test_s3_disk_copy_uses_server_side_copy_and_keeps_source(fake: FakeS3Client, s3: S3Disk) -> None:
    await s3.put("src.txt", "data")
    await s3.copy("src.txt", "dst.txt")
    assert fake.objects["src.txt"] == b"data"  # server-side copy keeps the source
    assert await s3.get("dst.txt") == b"data"
    copy_calls = [c for c in fake.calls if c[0] == "copy_object"]
    assert copy_calls[0][1]["CopySource"] == {"Bucket": "app-bucket", "Key": "src.txt"}


async def test_s3_disk_copy_of_missing_source_raises_not_found(s3: S3Disk) -> None:
    with pytest.raises(NotFoundError, match="src.txt"):
        await s3.copy("src.txt", "dst.txt")


async def test_s3_disk_files_lists_immediate_keys_sorted_excluding_children(
    fake: FakeS3Client, s3: S3Disk
) -> None:
    for key in ("docs/b.txt", "docs/sub/c.txt", "docs/a.txt", "other/x.txt"):
        await s3.put(key, "v")
    assert await s3.files("docs") == ["docs/a.txt", "docs/b.txt"]  # sub/ rolled up


async def test_s3_disk_files_recursive_lists_every_key_sorted_paginated() -> None:
    paged = PagedFakeS3Client()
    s3 = make_disk(paged)
    for key in ("docs/a.txt", "docs/b.txt", "docs/sub/c.txt"):
        await s3.put(key, "v")
    assert await s3.files("docs", recursive=True) == [
        "docs/a.txt",
        "docs/b.txt",
        "docs/sub/c.txt",
    ]


async def test_s3_disk_files_on_missing_prefix_raises_not_found(s3: S3Disk) -> None:
    with pytest.raises(NotFoundError, match="no-such-dir"):
        await s3.files("no-such-dir")


async def test_s3_disk_root_listing_with_dot(fake: FakeS3Client, s3: S3Disk) -> None:
    await s3.put("a.txt", "1")
    await s3.put("b/c.txt", "2")
    assert await s3.files(".") == ["a.txt"]  # LocalDisk parity: "." lists the root


# -- key containment policy ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [None, "", "   ", "../escape.txt", "/abs.txt", "C:\\x.txt", "a\\b.txt", "a/\x00b", ".."],
)
async def test_s3_disk_key_policy_mirrors_local_containment_refusals(s3: S3Disk, path: str) -> None:
    with pytest.raises(StoragePathError):
        await s3.put(path, "x")  # type: ignore[arg-type]


async def test_s3_disk_key_escaping_prefix_is_refused(fake: FakeS3Client) -> None:
    s3 = make_disk(fake, prefix="tenants/7")
    with pytest.raises(StoragePathError, match="escapes"):
        await s3.put("../../other/x.txt", "x")


async def test_s3_disk_interior_dotdot_that_stays_inside_is_allowed(fake: FakeS3Client) -> None:
    s3 = make_disk(fake, prefix="tenants/7")
    await s3.put("docs/../final.txt", "v")  # normalizes to tenants/7/final.txt
    assert "tenants/7/final.txt" in fake.objects
    assert await s3.get("final.txt") == b"v"


async def test_s3_disk_operations_stay_under_the_prefix(fake: FakeS3Client) -> None:
    s3 = make_disk(fake, prefix="tenants/7")
    await s3.put("a.txt", "v")
    assert list(fake.objects) == ["tenants/7/a.txt"]
    assert await s3.files(".") == ["a.txt"]  # listings are prefix-relative


# -- factory ------------------------------------------------------------------


def test_disk_factory_builds_s3_disk_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "aioboto3" else None)
    storage = Storage(
        disks={
            "s3": {
                "driver": "s3",
                "bucket": "app-bucket",
                "region": "us-east-1",
                "prefix": "app",
            }
        },
        default="s3",
    )
    built = storage.disk()
    assert isinstance(built, S3Disk)


def test_disk_factory_s3_without_bucket_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "aioboto3" else None)
    storage = Storage(disks={"s3": {"driver": "s3"}}, default="s3")
    with pytest.raises(ConfigurationError, match="bucket"):
        storage.disk()


def test_disk_factory_parses_truthy_string_public_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # config() hands back raw strings for keys no config module registers:
    # the commented-out s3 entry in config/storage.py reads
    # config("S3_PUBLIC", default=False), so an env S3_PUBLIC=false arrives
    # here as 'false' — and bool('false') is True. The factory must parse
    # the flag, not cast it, or "disable public URLs" silently enables them.
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "aioboto3" else None)
    storage = Storage(
        disks={
            "off": {"driver": "s3", "bucket": "b", "public": "false"},
            "on": {"driver": "s3", "bucket": "b", "public": "true"},
            "bool": {"driver": "s3", "bucket": "b", "public": True},
        },
        default="off",
    )
    assert storage.disk("off")._public is False
    assert storage.disk("on")._public is True
    assert storage.disk("bool")._public is True


# -- presigned URLs & public URL policy ----------------------------------------


async def test_s3_disk_temporary_url_presigns_a_scoped_get(fake: FakeS3Client) -> None:
    s3 = make_disk(fake, prefix="tenants/7")
    url = await s3.temporary_url("invoices/9.pdf", expires_in=300)
    assert url == "https://s3.example.com/tenants/7/invoices/9.pdf?exp=300"
    presign = [c for c in fake.calls if c[0] == "generate_presigned_url"]
    assert presign[0][1] == {
        "ClientMethod": "get_object",
        "Params": {"Bucket": "app-bucket", "Key": "tenants/7/invoices/9.pdf"},
        "ExpiresIn": 300,
    }


async def test_s3_disk_url_honors_public_base(fake: FakeS3Client) -> None:
    s3 = make_disk(fake, prefix="app", public_base="https://cdn.example.com/assets/")
    assert await s3.url("img/logo.png") == "https://cdn.example.com/assets/app/img/logo.png"


async def test_s3_disk_url_public_bucket_uses_vhost_style(fake: FakeS3Client) -> None:
    s3 = make_disk(fake, region="eu-west-1", public=True)
    assert await s3.url("img/logo.png") == (
        "https://app-bucket.s3.eu-west-1.amazonaws.com/img/logo.png"
    )


async def test_s3_disk_url_without_public_surface_refuses_with_hint(s3: S3Disk) -> None:
    with pytest.raises(StorageNotSupported, match="temporary_url"):
        await s3.url("img/logo.png")


# -- optional dependency guard ---------------------------------------------------


def test_s3_disk_without_aioboto3_fails_loud_with_extra_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ConfigurationError, match=r"pip install 'fastplace\[s3\]'"):
        S3Disk("app-bucket")  # no client_factory — the default path real apps take


def test_disk_factory_s3_without_aioboto3_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    reset_storage()
    storage = Storage(disks={"s3": {"driver": "s3", "bucket": "app-bucket"}}, default="s3")
    with pytest.raises(ConfigurationError, match=r"fastplace\[s3\]"):
        storage.disk()


def test_s3_disk_injected_factory_never_checks_the_dependency(
    monkeypatch: pytest.MonkeyPatch, fake: FakeS3Client
) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert S3Disk("app-bucket", client_factory=lambda: fake) is not None  # no raise — DI bypasses
