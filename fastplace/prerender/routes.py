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

    Raises ``ValueError`` for anything that is not an absolute path, and for
    ``..`` or empty (double-slash) segments — the writer maps routes onto
    filesystem paths, so those are rejected here, at the boundary, before
    any request is captured. Without this, httpx dot-normalizes ``/../etc``
    into a real route that only fails later, at the writer, after the whole
    app has rendered it for nothing.
    """
    cleaned = route.strip()
    if not cleaned.startswith("/"):
        raise ValueError(
            f"prerender routes must be absolute paths starting with '/', got {route!r}"
        )
    # Query/fragment routes capture "fine" but the output can never be
    # served: PrerenderStaticFiles only answers query-less requests, which
    # map to a different directory than `pricing?utm=1/index.html`.
    if "?" in cleaned or "#" in cleaned:
        raise ValueError(f"prerender routes must be path-only, no query or fragment, got {route!r}")
    # Root stays "/" (rstrip would empty it); every other path loses its
    # trailing slash so "/docs" and "/docs/" map to one output directory.
    normalized = cleaned.rstrip("/") or "/"
    if normalized != "/" and any(segment in ("", "..") for segment in normalized.split("/")[1:]):
        raise ValueError(f"unsafe prerender route segment in {route!r}")
    return normalized


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
        attr = app_module.PRERENDER_ROUTES
        # [] disables prerendering (present-and-empty wins); anything else
        # non-list-like is a config mistake. A bare string is the classic
        # slip — list("/docs") would silently capture one-character routes,
        # and None would surface as a raw TypeError traceback.
        if not isinstance(attr, (list, tuple)):
            raise ValueError(
                "PRERENDER_ROUTES on the asgi module must be a list of route "
                f"strings (use [] to disable prerendering), got {type(attr).__name__}"
            )
        source = list(attr)
    else:
        env_routes = os.environ.get("PRERENDER_ROUTES", "")
        if env_routes.strip():
            source = [entry for entry in env_routes.split(",") if entry.strip()]
    if source is None:
        source = list(_DEFAULT_ROUTES)
    return list(dict.fromkeys(_normalize(route) for route in source))
