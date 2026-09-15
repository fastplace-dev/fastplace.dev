"""FOUC pre-paint lockstep — the appearance block must be identical in both shells.

The app paints from two HTML shells: index.html (dev) and _HTML_SHELL in
fastplace/http/render.py (production, a str.format() template). Both carry a
byte-identical pre-paint block, delimited by marker comments, that applies the
persisted appearance before the first paint. The Python copy doubles every
literal brace so .format() leaves it intact; these tests keep the two copies
from drifting apart.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from starlette.requests import Request as SRequest

from fastplace.http import render
from fastplace.http.request import Request as FpRequest

_REPO_ROOT = Path(__file__).resolve().parents[2]

_START = "<!-- fastplace-appearance-prepaint -->"
_END = "<!-- /fastplace-appearance-prepaint -->"


def _extract_block(text: str) -> str:
    """Raw text between the two marker comments (markers excluded)."""
    before, _, rest = text.partition(_START)
    assert before.count(_START) == 0, "start marker must appear exactly once"
    block, sep, _ = rest.partition(_END)
    assert sep == _END, "end marker must follow the start marker"
    return block


def _dev_block() -> str:
    return _extract_block((_REPO_ROOT / "index.html").read_text(encoding="utf-8"))


def _production_block() -> str:
    """The shell's block with str.format() brace doubling undone."""
    # fastplace.http re-exports the render() function, shadowing the module
    # name — fetch the module itself explicitly.
    render_module = importlib.import_module("fastplace.http.render")
    raw = _extract_block(render_module._HTML_SHELL)
    return raw.replace("{{", "{").replace("}}", "}")


def test_prepaint_block_is_identical_in_both_shells():
    """Dev and production shells must carry the same block, before any asset."""
    dev = _dev_block()
    prod = _production_block()

    assert dev.strip(), "index.html must carry a non-empty pre-paint block"
    assert prod.strip(), "_HTML_SHELL must carry a non-empty pre-paint block"
    assert dev == prod, "pre-paint block must be byte-identical in both shells"

    # The block must precede every script/style asset in both shells.
    dev_html = (_REPO_ROOT / "index.html").read_text(encoding="utf-8")
    assert dev_html.index(_START) < dev_html.index('<script type="module" src=')
    render_module = importlib.import_module("fastplace.http.render")
    assert render_module._HTML_SHELL.index(_START) < render_module._HTML_SHELL.index("{assets}")


def test_rendered_document_contains_single_brace_block_exactly_once():
    """.format() must emit the block once, with every doubled brace undone."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "query_string": b"",
    }
    resp = render(FpRequest(SRequest(scope)), component="Dashboard/Index", props={})
    document = resp.body.decode()

    assert document.count(_START) == 1
    assert document.count(_END) == 1

    block = _extract_block(document)
    # No str.format() placeholders or brace-doubling survivors inside the block.
    assert "{{" not in block, "doubled braces survived .format()"
    assert "}}" not in block, "doubled braces survived .format()"
    # And the emitted block matches the dev shell byte for byte.
    assert block == _dev_block()


def test_prepaint_background_pins_the_surface_palette():
    """The pre-paint html background must track app.css's --surface palette.

    A future palette edit in resources/css/app.css would otherwise leave the
    pre-paint background silently stale while both lockstep tests stay green.
    """
    import re

    css = (_REPO_ROOT / "resources" / "css" / "app.css").read_text(encoding="utf-8")
    surfaces = re.findall(r"--surface:\s*(oklch\([^)]*\))", css)
    assert len(surfaces) >= 2, "app.css must define --surface for light and dark"
    block = _dev_block()
    assert surfaces[0] in block, "light --surface must be the pre-paint background"
    assert surfaces[-1] in block, "dark --surface must be the pre-paint background"
