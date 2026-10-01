"""The shared Alembic include-object hook — env.py and migrate:check agree.

One exclusion policy (framework bookkeeping tables + reflected ANN indexes)
backs both the scaffolded migration env and ``migrate:check``; these tests
pin the policy itself and the wiring of both call sites so the two paths
cannot drift apart again.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import fastplace.cli.db_ops as db_ops
import fastplace.orm.migrations as migrations
from fastplace.orm.migrations.include import include_object


def _reflected_index(using: str | None) -> SimpleNamespace:
    """Stand-in for a reflected index carrying postgres dialect options."""
    return SimpleNamespace(dialect_options={"postgresql": {"using": using}})


class TestPolicy:
    def test_reflected_hnsw_index_without_metadata_counterpart_is_excluded(self):
        assert (
            include_object(_reflected_index("hnsw"), "ix_tbl_vec_hnsw", "index", True, None)
            is False
        )

    def test_reflected_ivfflat_index_is_excluded(self):
        assert (
            include_object(_reflected_index("ivfflat"), "ix_tbl_vec_ivf", "index", True, None)
            is False
        )

    def test_declared_ann_index_still_diffs_normally(self):
        # compare_to is not None: the metadata declares this index, so
        # autogenerate must be allowed to diff it.
        assert (
            include_object(_reflected_index("hnsw"), "ix_tbl_vec_hnsw", "index", True, object())
            is True
        )

    def test_btree_reflected_index_is_kept(self):
        assert include_object(_reflected_index(None), "ix_users_email", "index", True, None) is True

    def test_index_without_postgres_dialect_options_is_kept(self):
        assert include_object(SimpleNamespace(), "ix_plain", "index", True, None) is True

    def test_framework_bookkeeping_tables_are_excluded(self):
        for name in ("fastplace_migrations", "alembic_version"):
            assert include_object(None, name, "table", True, None) is False

    def test_regular_tables_and_indexes_pass_through(self):
        assert include_object(None, "projects", "table", True, None) is True
        assert include_object(SimpleNamespace(), "ix_projects_name", "index", True, None) is True


class TestWiring:
    def test_migrate_check_uses_the_shared_hook(self):
        assert db_ops._include_object is include_object

    def test_env_template_imports_the_shared_hook(self):
        source = (Path(migrations.__file__).parent / "templates" / "env.py.tpl").read_text()
        assert "from fastplace.orm.migrations.include import include_object" in source
        # No local drift copy may come back alongside the shared import.
        assert "def _include_object" not in source
        # Both context.configure() calls (offline + online) must pass it.
        assert source.count("include_object=include_object") == 2
