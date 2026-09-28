"""FakeStorage — the dict-backed Disk plus its assertion surface."""

from __future__ import annotations

import pytest

from fastplace.errors import NotFoundError
from fastplace.testing import FakeStorage


@pytest.fixture
def disk() -> FakeStorage:
    return FakeStorage()


async def test_put_get_roundtrip(disk: FakeStorage):
    await disk.put("invoices/1.txt", "paid")
    assert await disk.get("invoices/1.txt") == b"paid"
    assert await disk.text("invoices/1.txt") == "paid"


async def test_put_accepts_bytes(disk: FakeStorage):
    await disk.put("blob.bin", b"\x00\x01")
    assert await disk.get("blob.bin") == b"\x00\x01"


async def test_get_missing_raises_not_found(disk: FakeStorage):
    with pytest.raises(NotFoundError):
        await disk.get("nope.txt")


async def test_exists_and_missing(disk: FakeStorage):
    assert await disk.missing("a.txt")
    await disk.put("a.txt", "x")
    assert await disk.exists("a.txt")
    assert not await disk.missing("a.txt")


async def test_delete_is_a_noop_on_absent_files(disk: FakeStorage):
    await disk.delete("never-there.txt")
    assert await disk.missing("never-there.txt")


async def test_size(disk: FakeStorage):
    await disk.put("a.txt", "hello")
    assert await disk.size("a.txt") == 5
    with pytest.raises(NotFoundError):
        await disk.size("absent.txt")


async def test_copy_overwrites_destination(disk: FakeStorage):
    await disk.put("src.txt", "one")
    await disk.put("dst.txt", "old")
    await disk.copy("src.txt", "dst.txt")
    assert await disk.text("dst.txt") == "one"
    assert await disk.text("src.txt") == "one"


async def test_move_removes_source_only_after_copy(disk: FakeStorage):
    await disk.put("src.txt", "one")
    await disk.move("src.txt", "dst.txt")
    assert await disk.missing("src.txt")
    assert await disk.text("dst.txt") == "one"


async def test_files_lists_under_a_directory(disk: FakeStorage):
    await disk.put("reports/2026/q1.txt", "x")
    await disk.put("reports/2026/q2.txt", "x")
    await disk.put("reports/readme.txt", "x")
    await disk.put("elsewhere.txt", "x")
    assert sorted(await disk.files("reports")) == ["reports/readme.txt"]
    assert sorted(await disk.files("reports", recursive=True)) == [
        "reports/2026/q1.txt",
        "reports/2026/q2.txt",
        "reports/readme.txt",
    ]


async def test_url_prefixes_the_path(disk: FakeStorage):
    await disk.put("a.txt", "x")
    assert await disk.url("a.txt") == "/storage/a.txt"


async def test_last_modified_tracks_writes(disk: FakeStorage):
    before = disk._mtimes.get("a.txt")
    assert before is None
    await disk.put("a.txt", "x")
    assert await disk.last_modified("a.txt") is not None


# -- assertions ---------------------------------------------------------------


async def test_assert_stored(disk: FakeStorage):
    with pytest.raises(AssertionError, match="a.txt"):
        disk.assert_stored("a.txt")
    await disk.put("a.txt", "content")
    disk.assert_stored("a.txt")
    with pytest.raises(AssertionError):
        disk.assert_stored("a.txt", content="other")
    disk.assert_stored("a.txt", content=b"content")
    disk.assert_stored("a.txt", content="content")


async def test_assert_missing(disk: FakeStorage):
    disk.assert_missing("a.txt")
    await disk.put("a.txt", "x")
    with pytest.raises(AssertionError, match="a.txt"):
        disk.assert_missing("a.txt")


async def test_assert_stored_count(disk: FakeStorage):
    disk.assert_nothing_stored()
    await disk.put("a.txt", "x")
    disk.assert_stored_count(1)
    with pytest.raises(AssertionError):
        disk.assert_stored_count(2)


def test_stored_lists_paths_sorted(disk: FakeStorage):
    import asyncio

    async def _fill() -> None:
        await disk.put("b.txt", "x")
        await disk.put("a.txt", "x")

    asyncio.run(_fill())
    assert disk.stored() == ["a.txt", "b.txt"]
