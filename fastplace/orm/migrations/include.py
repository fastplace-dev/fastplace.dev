"""The one Alembic ``include_object`` hook — shared by env.py and migrate:check.

The scaffolded migration environment (``env.py.tpl``) and
``fastplace migrate:check`` must filter the live schema identically, or the
check reports drift ``make:migration`` would never generate (and vice
versa). Both import this hook, so parity cannot drift.
"""

from __future__ import annotations

#: Framework bookkeeping tables that live beside the schema but are not part
#: of it (alembic_version is also auto-excluded by Alembic itself; kept for
#: an explicit single source of truth).
FRAMEWORK_TABLES = frozenset({"fastplace_migrations", "alembic_version"})


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ARG001 — alembic hook
    if type_ == "table" and name in FRAMEWORK_TABLES:
        return False
    # Reflected ANN indexes (pgvector HNSW/IVF) with no metadata counterpart
    # must not be dropped: Alembic cannot order/compare their opclass and
    # build options, so autogenerate reads them as unknown and would emit a
    # destructive drop on every diff. Indexes the metadata DOES declare diff
    # normally (compare_to is not None).
    if type_ == "index" and reflected and compare_to is None:
        try:
            using = obj.dialect_options["postgresql"].get("using")
        except Exception:
            using = None
        if using in ("hnsw", "ivfflat"):
            return False
    return True
