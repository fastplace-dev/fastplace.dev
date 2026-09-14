"""File isolation — company prefixes under ``storage/`` with traversal guards.

One physical tree, one directory per company::

    storage/app/company_7/invoices/q1.pdf

:func:`company_storage_path` builds (and validates) such paths;
:func:`ensure_company_path` validates paths that arrive from outside
(uploads, user-supplied names) — a name carrying ``..`` never escapes its
company's subtree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_DEFAULT_ROOT = Path("storage") / "app"


def _validated_company_id(company_id: Any) -> int:
    """Only a positive int reaches the path — the id is interpolated into a
    filesystem path, so a str like ``"../etc"`` (or 0/-1) would make the
    isolation guard itself the traversal."""
    if isinstance(company_id, bool) or not isinstance(company_id, int) or company_id <= 0:
        raise ValueError(f"company id must be a positive integer, got {company_id!r}")
    return company_id


def company_root(company_id: Any, *, root: Path | None = None) -> Path:
    """The company's subtree root: ``<root>/company_<id>`` (created lazily elsewhere)."""
    base = Path(root) if root is not None else _DEFAULT_ROOT
    return base / f"company_{_validated_company_id(company_id)}"


def company_storage_path(company_id: Any, *parts: str, root: Path | None = None) -> Path:
    """A path inside the company's subtree; raises on traversal attempts.

    Every incoming segment is checked after resolution — ``..`` (or any
    segment resolving outside the subtree) is rejected, not normalized away.
    """
    base = company_root(company_id, root=root).resolve()
    path = base.joinpath(*parts).resolve()
    ensure_company_path(path, company_id, root=root)
    return path


def ensure_company_path(path: Path | str, company_id: Any, *, root: Path | None = None) -> Path:
    """Assert ``path`` stays inside the company's subtree (IDOR guard for files)."""
    base = company_root(company_id, root=root).resolve()
    resolved = Path(path).resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(
            f"path {resolved} escapes company {company_id!r} storage "
            f"({base}) — traversal is not permitted"
        )
    return resolved
