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
    m2 = write_pages([CapturedPage("/p", 200, "text/html", body)], tmp_path)
    m3 = write_pages([CapturedPage("/p", 200, "text/html", b"<p>v2</p>")], tmp_path)
    assert m1.hashes == m2.hashes
    assert m1.hashes != m3.hashes


def test_clean_stale_removes_old_routes(tmp_path):
    write_pages([CapturedPage("/old", 200, "text/html", b"x")], tmp_path)
    assert (tmp_path / "old" / "index.html").exists()
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

    write_pages([CapturedPage("/new", 200, "text/html", b"y")], out)
    assert (sibling / "keep.txt").exists()


def test_write_pages_ignores_route_query_or_fragment_garbage(tmp_path):
    """Routes come from resolve_prerender_routes; a fragment-like segment is
    just a directory name, but an empty segment (double slash) is rejected."""
    with pytest.raises(ValueError):
        write_pages([CapturedPage("/a//b", 200, "text/html", b"x")], tmp_path)
