"""Prerender route resolution — precedence, normalization, validation.

The app owns the route list (no discovery): this module only decides where
the list comes from and makes every entry safe to turn into an output path.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType

_DEFAULT_ROUTES = ["/"]


def _normalize(route: str) -> str:
    """One route -> ``/clean/path`` form: trimmed, absolute, no trailing slash.

    Raises ``ValueError`` for anything that is not an absolute path — the
    writer maps routes onto filesystem paths, so a relative or empty entry
    is rejected here, at the boundary, before any file is opened.
    """
    cleaned = route.strip()
    if not cleaned.startswith("/"):
        raise ValueError(
            f"prerender routes must be absolute paths starting with '/', got {route!r}"
        )
    # Root stays "/" (rstrip would empty it); every other path loses its
    # trailing slash so "/docs" and "/docs/" map to one output directory.
    return cleaned.rstrip("/") or "/"


def resolve_prerender_routes(
    app_module: ModuleType | None, explicit: list[str], root: Path
) -> list[str]:
    """Resolve the routes `fastplace prerender` will capture.

    Precedence: CLI ``--route`` flags, then the ``PRERENDER_ROUTES`` list
    on the app's ``asgi`` module, then the ``PRERENDER_ROUTES`` environment
    variable (comma-separated), then the ``["/"]`` default. A source that
    is present wins even when empty — ``PRERENDER_ROUTES = []`` on the asgi
    module is the app disabling prerendering, and resolves to zero routes
    rather than falling through. Entries are normalized (absolute, trailing
    slash stripped) and deduplicated preserving first-occurrence order;
    blank env entries are dropped as comma noise.

    ``root`` is accepted for a future routes-file convention and ignored —
    no file-based config ships in this wave; the signature stays stable so
    callers do not churn when one does.
    """
    source: list[str] | None = None
    if explicit:
        source = list(explicit)
    elif app_module is not None and hasattr(app_module, "PRERENDER_ROUTES"):
        source = list(app_module.PRERENDER_ROUTES)
    else:
        env_routes = os.environ.get("PRERENDER_ROUTES", "")
        if env_routes.strip():
            source = [entry for entry in env_routes.split(",") if entry.strip()]
    if source is None:
        source = list(_DEFAULT_ROUTES)
    return list(dict.fromkeys(_normalize(route) for route in source))
