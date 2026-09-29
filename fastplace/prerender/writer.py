"""Prerender output writer — ``index.html`` trees + reproducible manifest.

The writer owns the directory it is pointed at: prerender output is derived
state, so every run may clear it before writing. Route strings become
filesystem paths here, which makes this module the boundary where traversal
is rejected for good.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from fastplace import __version__
from fastplace.prerender.capture import CapturedPage

logger = logging.getLogger("fastplace.prerender")

MANIFEST_NAME = "prerender-manifest.json"


@dataclass
class PrerenderManifest:
    """What one prerender run produced — routes, hashes, skips, version."""

    routes: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    generated_with: str = __version__

    def to_json(self) -> str:
        """Stable JSON: sorted keys, no timestamps — byte-identical for
        byte-identical input so builds are reproducible and diffable."""
        return (
            json.dumps(
                {
                    "routes": self.routes,
                    "hashes": self.hashes,
                    "skipped": self.skipped,
                    "generated_with": self.generated_with,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    def write(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / MANIFEST_NAME).write_text(self.to_json(), encoding="utf-8")


def write_pages(pages: list[CapturedPage], out_dir: Path, force: bool = False) -> PrerenderManifest:
    """Write capturable pages under ``out_dir`` and return the manifest.

    Only 200 responses with an HTML content type become files; everything
    else is recorded as skipped. Stale output from earlier runs is cleared
    first — the tree is fully derived from this run's captures.

    Because the clear is wholesale, a non-empty ``out_dir`` the run does
    not recognize (no ``prerender-manifest.json`` from a previous run) is
    refused unless ``force`` is set — the difference between refreshing
    derived output and deleting a directory someone cares about.
    """
    # The tree is cleared and rewritten wholesale below; a symlinked out_dir
    # would rmtree/write the *target* tree instead. Refuse before anything
    # touches the filesystem.
    if out_dir.is_symlink():
        raise ValueError(
            f"prerender output directory {out_dir} is a symlink — refusing to write through it"
        )
    if (
        not force
        and out_dir.exists()
        and any(out_dir.iterdir())
        and not (out_dir / MANIFEST_NAME).is_file()
    ):
        raise ValueError(
            f"{out_dir} is not empty and has no {MANIFEST_NAME} — refusing to "
            "clear a directory that is not a prerender output tree "
            "(pass --force to write there anyway)"
        )
    writable: list[CapturedPage] = []
    skipped: list[str] = []
    for page in pages:
        is_html = page.content_type.partition(";")[0].strip().lower() == "text/html"
        if page.status == 200 and is_html:
            # Validate every route BEFORE any file or deletion happens: a
            # rejected route must fail the run with the old tree untouched.
            _route_dir(page.route, out_dir)
            writable.append(page)
        else:
            skipped.append(page.route)

    manifest = PrerenderManifest(
        routes=[page.route for page in writable],
        hashes={page.route: _hash(page.body) for page in writable},
        skipped=skipped,
    )
    clean_stale(out_dir, manifest)
    for page in writable:
        page_dir = _route_dir(page.route, out_dir)
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_bytes(page.body)
    return manifest


def clean_stale(out_dir: Path, manifest: PrerenderManifest) -> None:
    """Clear the prerender tree ``out_dir`` ahead of a rewrite.

    ``manifest`` describes the run about to land; it sizes the clear-all
    into a logged line today and anchors finer-grained (per-route) cleaning
    later. Removal never follows symlinks out of the tree — a link is
    unlinked, its target untouched.
    """
    if not out_dir.exists():
        return
    logger.debug("cleaning prerender tree %s (%d route(s) to write)", out_dir, len(manifest.routes))
    for entry in out_dir.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink()
        else:
            shutil.rmtree(entry)


def _route_dir(route: str, out_dir: Path) -> Path:
    """Map a route to its output directory, rejecting unsafe segments.

    ``/`` maps to ``out_dir`` itself; every other route maps to its segment
    path. An empty segment (``/a//b``) or ``..`` would resolve outside the
    intended subtree shape and is rejected before any path is built on.
    A symlinked directory inside the tree is caught the same way: the
    candidate is resolved (collapsing link chains) and must stay inside
    ``out_dir`` resolved — both sides, since macOS maps ``/tmp`` onto
    ``/private/tmp`` and an unresolved comparison would misfire there.
    """
    if route == "/":
        directory = out_dir
    else:
        segments = route.strip("/").split("/")
        for segment in segments:
            if segment in ("", ".."):
                raise ValueError(f"unsafe prerender route segment in {route!r}")
        directory = out_dir.joinpath(*segments)
    if not directory.resolve().is_relative_to(out_dir.resolve()):
        raise ValueError(f"prerender route {route!r} resolves outside the output tree")
    return directory


def _hash(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()
