"""Terminology — the in-repo application is the "sample app", not "dogfood".

"Dogfooding" is engineering slang for using your own framework; it was the
internal working label for the app at the repo root and leaked into paths,
fixtures and docs. The framework's public-facing name for that app is
"sample app" (matching scripts/extract_sample.py). This test pins the rename
so the old term cannot creep back into any tracked file.

Internal tracking documents (gitignored, never published) may quote the
historical commits by their real subjects; they are outside `git ls-files`
and therefore outside this scan.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Files exempt from the content scan: this test (its prose names the
#: banned term by necessity).
EXEMPT_FILES = {"tests/test_terminology.py"}


def _tracked_files() -> list[str]:
    if not (ROOT / ".git").exists():
        pytest.skip("requires a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout
    return [f for f in out.split("\0") if f]


def test_no_tracked_path_is_named_dogfood():
    offenders = [f for f in _tracked_files() if "dogfood" in f.lower()]
    assert not offenders, offenders


def test_no_tracked_text_mentions_dogfood():
    """No tracked text file may contain "dogfood" — paths, fixtures,
    docstrings, docs and test data must all say "sample app"."""
    offenders: list[str] = []
    for rel in _tracked_files():
        if rel in EXEMPT_FILES:
            continue
        path = ROOT / rel
        raw = path.read_bytes()
        if b"\0" in raw:  # binary
            continue
        for lineno, line in enumerate(raw.decode("utf-8", errors="ignore").splitlines(), 1):
            if "dogfood" in line.lower():
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, "\n".join(offenders[:20])
