"""Terminology — the in-repo application is the "sample app", not "dogfood".

"Dogfooding" is engineering slang for using your own framework; it was the
internal working label for the app at the repo root and leaked into paths,
fixtures and docs. The framework's public-facing name for that app is
"sample app" (matching scripts/extract_sample.py and the roadmap's "sample
applications" commitment). This test pins the rename so the old term cannot
creep back in.

The only surviving occurrences are exact quote lines in the status
checklist that NAME historical commits — git history is immutable, and the
checklist rows reference those commits by their real subjects.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Exact lines in docs/status_checklist.md that quote immutable commit
#: subjects. Both directions are pinned: a dogfood line must be one of
#: these, and each of these must still exist (stale quotes fail too).
ALLOWED_QUOTE_LINES = {
    "| 7 — Production polish & dogfood | `5cfbe65` | observability, error pages, sample-app modules, 35 adversarial-review fixes |",
    "| Batch 2 — Dogfood depth | `44a709a` | typed DTO contracts on `/api/v1` (services own Pydantic result models, controllers annotate returns, frozen wire-shape tests), `app/ai/tools/search_docs` (labeled, bounded excerpts) wired into the assistant agent, Knowledge bridge page + Assistant chat page (`useAIStream`), persistent layouts (`applyLayouts`, `X.layout` static, `resources/js/layouts/AppLayout`), app-level middleware (`app/http/middleware/request_timing.py` in `config/app.py MIDDLEWARE`), hermetic E2E scratch DB; 5 adversarial-review findings fixed |",
    "2. ~~**Dogfood depth** (1–2 days)~~ — ✅ delivered (Batch 2): Assistant chat page · Knowledge bridge page · typed DTO contracts on `/api/v1` · `app/ai/tools/` example · layouts directory · app-level middleware",
}
QUOTE_FILE = "docs/status_checklist.md"

#: Files exempt from the content scan: this test (its allowlist and prose
#: name the banned term by necessity) and commit-message.txt (a rename
#: commit must be able to state what it renamed).
EXEMPT_FILES = {"tests/test_terminology.py", "commit-message.txt"}


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


def test_dogfood_survives_only_in_quoted_commit_subjects():
    """Every case-insensitive "dogfood" in tracked text files must be one of
    the pinned checklist quote lines — paths, fixtures, docstrings, docs and
    test data must all say "sample app"."""
    checklist = (ROOT / QUOTE_FILE).read_text()
    for line in ALLOWED_QUOTE_LINES:
        assert line in checklist, f"stale quote (checklist changed?): {line[:60]}…"

    offenders: list[str] = []
    for rel in _tracked_files():
        if rel in EXEMPT_FILES:
            continue
        path = ROOT / rel
        raw = path.read_bytes()
        if b"\0" in raw:  # binary
            continue
        for lineno, line in enumerate(raw.decode("utf-8", errors="ignore").splitlines(), 1):
            if "dogfood" in line.lower() and not (
                rel == QUOTE_FILE and line.strip() in ALLOWED_QUOTE_LINES
            ):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, "\n".join(offenders[:20])
