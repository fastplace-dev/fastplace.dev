"""Storage abstraction — LocalDisk surface, traversal guard, factory, fake shape.

The traversal guard is the security core: every path is resolved and verified
to stay inside the disk root before any filesystem call runs, so a hostile
path ("../../etc/passwd", absolute paths, symlinked escapes) is a loud
StoragePathError, never a write outside the root. The DictDisk at the bottom
is the shape wave-6 test fakes will copy: dict-backed, primitives only.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from fastplace.errors import ConfigurationError, FastplaceError, NotFoundError
from fastplace.storage import (
    Disk,
    LocalDisk,
    Storage,
    StorageNotSupported,
    StoragePathError,
    disk,
    reset_storage,
)


@pytest.fixture(autouse=True)
def _fresh_storage():
    """No singleton leakage between cases — every test resolves its own disks."""
    reset_storage()
    yield
    reset_storage()


@pytest.fixture
def disk_root(tmp_path: Path) -> Path:
    return tmp_path / "root"


@pytest.fixture
def local(disk_root: Path) -> LocalDisk:
    return LocalDisk(disk_root)


# ---------------------------------------------------------------------------
# Round-trips: put/get/text, binary fidelity, directories auto-created
# ---------------------------------------------------------------------------


async def test_put_get_roundtrip_text_and_bytes(local: LocalDisk):
    await local.put("greeting.txt", "hello")
    await local.put("blob.bin", b"\x00\x01")
    assert await local.text("greeting.txt") == "hello"
    assert await local.get("blob.bin") == b"\x00\x01"


async def test_binary_roundtrip_survives_every_byte_value(local: LocalDisk):
    payload = bytes(range(256)) + b"\x00\xffbinary\xff\x00"
    await local.put("raw.bin", payload)
    assert await local.get("raw.bin") == payload


async def test_put_encodes_str_as_utf8(local: LocalDisk):
    await local.put("unicode.txt", "héllo — naïve")
    assert await local.get("unicode.txt") == "héllo — naïve".encode()


async def test_put_creates_missing_parent_directories(local: LocalDisk):
    await local.put("deeply/nested/dir/file.txt", "x")
    assert await local.exists("deeply/nested/dir/file.txt")


async def test_put_overwrites_existing_file(local: LocalDisk):
    await local.put("file.txt", "first")
    await local.put("file.txt", "second")
    assert await local.text("file.txt") == "second"


async def test_put_rejects_non_string_non_bytes_content(local: LocalDisk):
    with pytest.raises(TypeError):
        await local.put("file.txt", 123)  # type: ignore[arg-type]


async def test_unicode_filenames_roundtrip(local: LocalDisk):
    await local.put("résumés/naïve.txt", "ok")
    assert await local.text("résumés/naïve.txt") == "ok"


async def test_text_decodes_stored_bytes(local: LocalDisk):
    await local.put("file.txt", "héllo".encode())
    assert await local.text("file.txt") == "héllo"


# ---------------------------------------------------------------------------
# Existence, deletion, size, timestamps
# ---------------------------------------------------------------------------


async def test_exists_and_missing_are_negations(local: LocalDisk):
    await local.put("present.txt", "x")
    assert await local.exists("present.txt")
    assert not await local.missing("present.txt")
    assert await local.missing("absent.txt")
    assert not await local.exists("absent.txt")


async def test_delete_removes_file_and_is_lenient_when_absent(local: LocalDisk):
    await local.put("file.txt", "x")
    await local.delete("file.txt")
    assert await local.missing("file.txt")
    await local.delete("file.txt")  # idempotent — deleting absence is a no-op


async def test_size_returns_byte_count(local: LocalDisk):
    await local.put("file.txt", "12345")
    assert await local.size("file.txt") == 5
    await local.put("empty.txt", "")
    assert await local.size("empty.txt") == 0


async def test_last_modified_is_set_after_put_and_none_when_missing(local: LocalDisk):
    await local.put("file.txt", "x")
    stamp = await local.last_modified("file.txt")
    assert stamp is not None and stamp > 0.0
    assert await local.last_modified("absent.txt") is None


# ---------------------------------------------------------------------------
# Missing-file errors are distinct from path-refusal errors
# ---------------------------------------------------------------------------


async def test_missing_file_raises_not_found_error(local: LocalDisk):
    for probe in (local.get, local.text, local.size):
        with pytest.raises(NotFoundError):
            await probe("absent.txt")


async def test_missing_copy_source_raises_not_found(local: LocalDisk):
    with pytest.raises(NotFoundError):
        await local.copy("absent.txt", "dest.txt")


async def test_storage_errors_are_framework_errors_with_http_statuses():
    assert issubclass(StoragePathError, FastplaceError)
    assert issubclass(StorageNotSupported, FastplaceError)
    assert StoragePathError.status_code == 400
    assert StorageNotSupported.status_code == 500


# ---------------------------------------------------------------------------
# copy / move
# ---------------------------------------------------------------------------


async def test_copy_duplicates_content_and_keeps_source(local: LocalDisk):
    await local.put("a.txt", "content")
    await local.copy("a.txt", "copies/a.txt")
    assert await local.text("a.txt") == "content"
    assert await local.text("copies/a.txt") == "content"


async def test_copy_overwrites_existing_destination(local: LocalDisk):
    await local.put("src.txt", "new")
    await local.put("dest.txt", "old")
    await local.copy("src.txt", "dest.txt")
    assert await local.text("dest.txt") == "new"


async def test_move_relocates_and_removes_source(local: LocalDisk):
    await local.put("inbox/file.txt", "payload")
    await local.move("inbox/file.txt", "archive/file.txt")
    assert await local.missing("inbox/file.txt")
    assert await local.text("archive/file.txt") == "payload"


async def test_move_overwrites_existing_destination(local: LocalDisk):
    await local.put("src.txt", "new")
    await local.put("dest.txt", "old")
    await local.move("src.txt", "dest.txt")
    assert await local.text("dest.txt") == "new"
    assert await local.missing("src.txt")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@pytest.fixture
def listing_disk(local: LocalDisk) -> LocalDisk:
    return local


async def test_files_lists_immediate_files_sorted_excluding_directories(
    listing_disk: LocalDisk,
):
    await listing_disk.put("docs/a.txt", "x")
    await listing_disk.put("docs/deep/b.txt", "x")
    await listing_disk.put("notes.md", "x")
    assert await listing_disk.files(".") == ["notes.md"]  # nested dirs excluded
    assert await listing_disk.files("docs") == ["docs/a.txt"]


async def test_files_recursive_lists_every_file_sorted(listing_disk: LocalDisk):
    await listing_disk.put("docs/a.txt", "x")
    await listing_disk.put("docs/deep/b.txt", "x")
    await listing_disk.put("notes.md", "x")
    assert await listing_disk.files(".", recursive=True) == [
        "docs/a.txt",
        "docs/deep/b.txt",
        "notes.md",
    ]


async def test_files_on_missing_directory_raises_not_found(listing_disk: LocalDisk):
    with pytest.raises(NotFoundError):
        await listing_disk.files("absent-dir")


# ---------------------------------------------------------------------------
# The traversal guard — every escape attempt is a loud StoragePathError
# ---------------------------------------------------------------------------


async def test_parent_escape_is_refused_and_writes_nothing(local: LocalDisk, disk_root: Path):
    with pytest.raises(StoragePathError):
        await local.put("../../escape.txt", "x")
    with pytest.raises(StoragePathError):
        await local.put("a/b/../../../../escape.txt", "x")
    # Refusal happens before any mkdir — not even the leading dirs exist.
    assert not (disk_root / "a").exists()
    assert not (disk_root.parent / "escape.txt").exists()


async def test_absolute_path_is_refused(local: LocalDisk):
    with pytest.raises(StoragePathError):
        await local.get("/etc/passwd")
    with pytest.raises(StoragePathError):
        await local.put("/tmp/absolute-escape.txt", "x")


async def test_drive_qualified_path_is_refused(local: LocalDisk):
    with pytest.raises(StoragePathError):
        await local.put("C:/temp/file.txt", "x")


async def test_backslash_separator_is_refused(local: LocalDisk):
    with pytest.raises(StoragePathError):
        await local.put("a\\b.txt", "x")


async def test_empty_and_null_paths_are_refused(local: LocalDisk):
    for bad in ("", "   ", "a\x00b"):
        with pytest.raises(StoragePathError):
            await local.put(bad, "x")


async def test_non_string_path_is_refused(local: LocalDisk):
    with pytest.raises(StoragePathError):
        await local.get(123)  # type: ignore[arg-type]


async def test_interior_dotdot_that_stays_inside_is_allowed(local: LocalDisk):
    await local.put("a/b/file.txt", "x")
    assert await local.text("a/sibling/../b/file.txt") == "x"


async def test_url_refuses_escaped_path(local: LocalDisk):
    with pytest.raises(StoragePathError):
        await local.url("../escape.txt")


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics are POSIX-only here")
async def test_symlink_escape_is_refused(local: LocalDisk, disk_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    disk_root.mkdir(parents=True, exist_ok=True)
    (disk_root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StoragePathError):
        await local.get("link/secret.txt")
    with pytest.raises(StoragePathError):
        await local.files("link")


# ---------------------------------------------------------------------------
# URLs: the conventional public prefix; presigning refused honestly
# ---------------------------------------------------------------------------


async def test_url_uses_the_conventional_storage_prefix(local: LocalDisk):
    await local.put("a/b.txt", "x")
    assert await local.url("a/b.txt") == "/storage/a/b.txt"


async def test_url_is_pure_path_math_and_does_not_touch_the_disk(local: LocalDisk):
    assert await local.url("never-written.txt") == "/storage/never-written.txt"


async def test_url_honors_custom_base(local: LocalDisk, disk_root: Path):
    media = LocalDisk(disk_root, url_base="/media")
    assert await media.url("a.txt") == "/media/a.txt"


async def test_temporary_url_is_refused_on_local_disks(local: LocalDisk):
    with pytest.raises(StorageNotSupported):
        await local.temporary_url("private/file.txt", expires_in=300)


async def test_temporary_url_refusal_points_to_the_signed_route_recipe():
    with pytest.raises(StorageNotSupported, match="signing"):
        await LocalDisk(Path("/tmp")).temporary_url("f.txt", expires_in=60)


# ---------------------------------------------------------------------------
# Disk ABC defaults — the derived ops every driver (and test fake) inherits
# ---------------------------------------------------------------------------


class DictDisk(Disk):
    """The wave-6 fake shape: dict-backed, primitives only, derived ops inherited."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    async def put(self, path: str, content: bytes | str) -> None:
        self._files[path] = content.encode("utf-8") if isinstance(content, str) else content

    async def get(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError:
            raise NotFoundError(f"storage file not found: {path!r}") from None

    async def exists(self, path: str) -> bool:
        return path in self._files

    async def delete(self, path: str) -> None:
        self._files.pop(path, None)

    async def copy(self, source: str, destination: str) -> None:
        self._files[destination] = await self.get(source)

    async def size(self, path: str) -> int:
        return len(await self.get(path))

    async def last_modified(self, path: str) -> float | None:
        return None if path not in self._files else 0.0

    async def files(self, directory: str, *, recursive: bool = False) -> list[str]:
        prefix = "" if directory in (".", "") else directory.rstrip("/") + "/"
        matches = [p for p in self._files if p.startswith(prefix)]
        if not recursive:
            matches = [p for p in matches if "/" not in p[len(prefix) :]]
        return sorted(matches)

    async def url(self, path: str) -> str:
        return f"/fake/{path}"


async def test_fake_disk_gets_text_missing_move_url_from_the_abc():
    fake = DictDisk()
    await fake.put("a.txt", "one")
    await fake.put("b.txt", "two")
    assert await fake.text("a.txt") == "one"
    assert not await fake.missing("b.txt")
    await fake.move("a.txt", "c.txt")
    assert await fake.missing("a.txt")
    assert await fake.text("c.txt") == "one"
    assert await fake.url("c.txt") == "/fake/c.txt"
    assert await fake.files(".") == ["b.txt", "c.txt"]


async def test_abc_default_temporary_url_is_an_honest_refusal():
    with pytest.raises(StorageNotSupported):
        await DictDisk().temporary_url("a.txt", expires_in=60)


async def test_disk_is_the_shared_contract(local: LocalDisk):
    assert isinstance(local, Disk)
    assert isinstance(DictDisk(), Disk)


# ---------------------------------------------------------------------------
# Factory: fastplace.storage.disk() mirrors the cache() singleton style
# ---------------------------------------------------------------------------


async def test_disk_factory_returns_the_configured_local_disk():
    default = disk()
    assert isinstance(default, LocalDisk)
    assert default.root.name == "app"  # config/storage.py default root: storage/app


async def test_disk_factory_caches_per_name():
    assert disk() is disk()
    assert disk("local") is disk()


async def test_disk_factory_unknown_name_is_a_configuration_error():
    with pytest.raises(ConfigurationError):
        disk("nonexistent-disk")


async def test_storage_constructs_injected_disks_without_config(tmp_path: Path):
    storage = Storage(
        disks={"media": {"driver": "local", "root": str(tmp_path / "media")}},
        default="media",
    )
    media = storage.disk()
    assert isinstance(media, LocalDisk)
    assert media.root.name == "media"
    assert storage.disk("media") is media


async def test_storage_injection_unknown_driver_is_a_configuration_error(tmp_path: Path):
    storage = Storage(disks={"bad": {"driver": "gopher", "root": str(tmp_path)}}, default="bad")
    with pytest.raises(ConfigurationError):
        storage.disk()


async def test_storage_injection_local_disk_requires_a_root():
    storage = Storage(disks={"no-root": {"driver": "local"}}, default="no-root")
    with pytest.raises(ConfigurationError):
        storage.disk()


async def test_reset_storage_drops_cached_disks(tmp_path: Path):
    first = disk()
    reset_storage()
    # The rebuilt singleton is a fresh instance (same config, new object).
    assert disk() is not first


# ---------------------------------------------------------------------------
# Concurrency: parallel puts stay isolated (the live-probe shape, in-suite)
# ---------------------------------------------------------------------------


async def test_parallel_puts_and_gets_stay_isolated(local: LocalDisk):
    payloads = {f"blobs/{i}.bin": bytes([i % 256]) * (i + 1) for i in range(50)}
    await asyncio.gather(*(local.put(path, data) for path, data in payloads.items()))
    got = await asyncio.gather(*(local.get(path) for path in payloads))
    assert list(got) == list(payloads.values())
