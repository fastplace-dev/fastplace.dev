"""Writer — files land in a tree, manifest hashes are content-stable."""

from __future__ import annotations

import pytest

from fastplace.prerender.capture import CapturedPage
from fastplace.prerender.writer import write_pages


def test_writes_nested_index_files(tmp_path):
    pages = [
        CapturedPage(
            route="/", status=200, content_type="text/html; charset=utf-8", body=b"<h1>home</h1>"
        ),
        CapturedPage(route="/docs/x", status=200, content_type="text/html", body=b"<h1>x</h1>"),
    ]
    manifest = write_pages(pages, tmp_path)
    assert (tmp_path / "index.html").read_bytes() == b"<h1>home</h1>"
    assert (tmp_path / "docs" / "x" / "index.html").exists()
    assert manifest.routes == ["/", "/docs/x"]


def test_traversal_segment_raises(tmp_path):
    bad = [CapturedPage(route="/../etc", status=200, content_type="text/html", body=b"x")]
    with pytest.raises(ValueError):
        write_pages(bad, tmp_path)


def test_non_html_and_non_200_skipped(tmp_path):
    pages = [
        CapturedPage(route="/json", status=200, content_type="application/json", body=b"{}"),
        CapturedPage(route="/missing", status=404, content_type="text/html", body=b"nope"),
        CapturedPage(route="/ok", status=200, content_type="text/html", body=b"ok"),
    ]
    manifest = write_pages(pages, tmp_path)
    assert manifest.routes == ["/ok"]
    assert manifest.skipped == ["/json", "/missing"]


def test_hash_stable_and_content_sensitive(tmp_path):
    body = b"<p>v1</p>"
    m1 = write_pages([CapturedPage("/p", 200, "text/html", body)], tmp_path)
    # Repeats hash-only mechanics; force bypasses the non-empty-tree guard.
    m2 = write_pages([CapturedPage("/p", 200, "text/html", body)], tmp_path, force=True)
    m3 = write_pages([CapturedPage("/p", 200, "text/html", b"<p>v2</p>")], tmp_path, force=True)
    assert m1.hashes == m2.hashes
    assert m1.hashes != m3.hashes


def test_clean_stale_removes_old_routes(tmp_path):
    first = write_pages([CapturedPage("/old", 200, "text/html", b"x")], tmp_path)
    assert (tmp_path / "old" / "index.html").exists()
    first.write(tmp_path)  # stamp, exactly as a real CLI run does
    write_pages([CapturedPage("/new", 200, "text/html", b"y")], tmp_path)
    assert not (tmp_path / "old" / "index.html").exists()
    assert (tmp_path / "new" / "index.html").exists()


def test_manifest_written_and_parses(tmp_path):
    from fastplace import __version__

    manifest = write_pages([CapturedPage("/", 200, "text/html", b"<h1>hi</h1>")], tmp_path)
    manifest.write(tmp_path)
    import json

    data = json.loads((tmp_path / "prerender-manifest.json").read_text())
    assert data["routes"] == ["/"]
    assert data["hashes"]["/"] == manifest.hashes["/"]
    assert data["skipped"] == []
    assert data["generated_with"] == __version__


def test_hash_is_sha256_hex_of_body(tmp_path):
    import hashlib

    manifest = write_pages([CapturedPage("/p", 200, "text/html", b"exact")], tmp_path)
    assert manifest.hashes["/p"] == hashlib.sha256(b"exact").hexdigest()


def test_clean_stale_never_escapes_out_dir(tmp_path):
    """The writer may only delete inside the prerender tree it owns."""
    import os

    sibling = tmp_path.parent / "prerender-sibling-target"
    sibling.mkdir(exist_ok=True)
    (sibling / "keep.txt").write_text("keep")
    out = tmp_path / "prerender"
    out.mkdir()
    # A symlinked route dir pointing outside the tree must not be followed
    # into by the clean step: it is removed as a link, not descended.
    os.symlink(sibling, out / "evil")
    from fastplace.prerender.writer import write_pages

    # force=True: this test drives the clean step's link handling, not the
    # non-empty-tree guard (the planted symlink makes the dir non-empty).
    write_pages([CapturedPage("/new", 200, "text/html", b"y")], out, force=True)
    assert (sibling / "keep.txt").exists()


def test_write_pages_ignores_route_query_or_fragment_garbage(tmp_path):
    """Routes come from resolve_prerender_routes; a fragment-like segment is
    just a directory name, but an empty segment (double slash) is rejected."""
    with pytest.raises(ValueError):
        write_pages([CapturedPage("/a//b", 200, "text/html", b"x")], tmp_path)


def test_symlinked_out_dir_refused(tmp_path):
    """The writer never operates through a symlinked output directory.

    mkdir/iterdir/unlink through the link would clear or write the target
    tree — refuse the run with the target untouched instead.
    """
    target = tmp_path / "real-tree"
    target.mkdir()
    (target / "keep.txt").write_text("precious")
    out_dir = tmp_path / "prerender"
    out_dir.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        write_pages([CapturedPage("/", 200, "text/html", b"<h1>x</h1>")], out_dir)
    assert (target / "keep.txt").read_text() == "precious"


def test_intermediate_symlink_escape_refused(tmp_path):
    """A symlinked route directory inside the tree is never written through.

    Writing ``docs/x/index.html`` through ``docs -> evil`` would plant the
    page in the evil tree; containment is verified on the resolved path.
    """
    out_dir = tmp_path / "prerender"
    out_dir.mkdir()
    evil = tmp_path / "evil"
    evil.mkdir()
    (out_dir / "docs").symlink_to(evil)

    with pytest.raises(ValueError, match="outside"):
        # force=True so the planted-symlink dir reaches the containment
        # check instead of tripping the non-empty-tree guard first.
        write_pages([CapturedPage("/docs/x", 200, "text/html", b"<h1>x</h1>")], out_dir, force=True)
    assert list(evil.iterdir()) == []  # nothing leaked through the link


def test_refuses_non_empty_dir_without_manifest(tmp_path):
    """A fat-fingered --out must not be cleared.

    Every run clears the tree wholesale, so the writer only operates on a
    directory it recognizes: empty (a fresh output dir) or stamped by a
    previous run's manifest. Anything else — a sources dir, a home dir —
    is refused with its contents untouched.
    """
    out = tmp_path / "elsewhere"
    out.mkdir()
    (out / "precious.txt").write_text("keep me")

    with pytest.raises(ValueError, match="prerender-manifest"):
        write_pages([CapturedPage("/", 200, "text/html", b"<h1>x</h1>")], out)
    assert (out / "precious.txt").read_text() == "keep me"


def test_manifest_stamp_allows_rerun_into_same_tree(tmp_path):
    """A stamped tree is recognized as the run's own output and refreshed."""
    first = write_pages([CapturedPage("/old", 200, "text/html", b"x")], tmp_path)
    first.write(tmp_path)  # a real CLI run always stamps the tree
    write_pages([CapturedPage("/new", 200, "text/html", b"y")], tmp_path)
    assert not (tmp_path / "old").exists()
    assert (tmp_path / "new" / "index.html").exists()


def test_force_overrides_the_guard(tmp_path):
    """force=True accepts a foreign-but-intended tree, clearing it as usual."""
    out = tmp_path / "elsewhere"
    out.mkdir()
    (out / "stale.txt").write_text("from an aborted setup")
    write_pages([CapturedPage("/", 200, "text/html", b"<h1>x</h1>")], out, force=True)
    assert not (out / "stale.txt").exists()
    assert (out / "index.html").read_bytes() == b"<h1>x</h1>"


def test_all_skipped_run_preserves_previous_tree(tmp_path):
    """A rerun where every route skipped must not wipe the last-good output.

    Transient 500s at deploy time are facts, not a reason to delete the
    pages the previous deploy is still serving. With nothing writable
    there is nothing to refresh: the tree and its manifest stay exactly
    as the last successful run left them.
    """
    from fastplace.prerender.writer import MANIFEST_NAME

    first = write_pages([CapturedPage("/", 200, "text/html", b"<h1>good</h1>")], tmp_path)
    first.write(tmp_path)
    before = (tmp_path / MANIFEST_NAME).read_text()

    rerun = write_pages([CapturedPage("/", 500, "text/html", b"boom")], tmp_path)

    assert rerun.routes == []
    assert rerun.skipped == ["/"]
    assert (tmp_path / "index.html").read_bytes() == b"<h1>good</h1>"
    assert (tmp_path / MANIFEST_NAME).read_text() == before


def test_out_dir_that_is_a_file_is_refused(tmp_path):
    """--out pointing at a regular file is a clean refusal, not iterdir crash."""
    target = tmp_path / "not-a-dir.txt"
    target.write_text("file")

    with pytest.raises(ValueError, match="not a directory"):
        write_pages([CapturedPage("/", 200, "text/html", b"x")], target)
    assert target.read_text() == "file"
