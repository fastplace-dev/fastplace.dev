"""Phase 2 adversarial-review fixes — regression tests for confirmed findings.

Each test maps to a CONFIRMED finding from the Phase 2 review workflow:
commit semantics, cross-task ambient sessions, raw writes, named connections,
plain-classmethod scopes, core scopes, abstract-base columns, refresh on
soft-deleted rows, mass-assignment guarding, escape-hatch PKs, unannotated
relationship markers, self-referential relationships, memory-pool concurrency,
and MySQL capability honesty.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Mapped, mapped_column

from fastplace.db import db, reset_db
from fastplace.errors import MassAssignmentError, NotFoundError
from fastplace.orm import Field, Model, belongs_to, has_many
from fastplace.orm.capabilities import Capabilities


@pytest.fixture()
def User(db_url):  # noqa: N802
    class User(Model):
        __tablename__ = "review_users"

        id: int = Field(primary_key=True)
        name: str
        active: bool = True
        role: str = "user"

    return User


@pytest.fixture()
async def schema(User):
    await db.create_all()
    return User


# ---------------------------------------------------------------------------
# 1. Commit semantics: writes inside db.connection() / session_scope() persist
# ---------------------------------------------------------------------------


async def test_writes_inside_db_connection_persist(schema):
    User = schema
    async with db.connection():
        await User.create(name="kept")
    assert await User.count() == 1


async def test_writes_inside_session_scope_persist(schema):
    User = schema
    from fastplace.orm.session import session_scope

    async with session_scope():
        await User.create(name="kept")
    assert await User.count() == 1


async def test_connection_persists_despite_later_error(schema):
    """Unit-of-work semantics: each ORM write commits; later exceptions do not
    undo already-committed writes (use db.transaction() for atomicity)."""
    User = schema
    with pytest.raises(RuntimeError):
        async with db.connection():
            await User.create(name="kept")
            raise RuntimeError("boom")
    assert await User.count() == 1


async def test_transaction_inside_connection_commits_via_outer_scope(schema):
    User = schema
    async with db.connection():
        async with db.transaction():
            await User.create(name="nested")
    assert await User.count() == 1


# ---------------------------------------------------------------------------
# 2. Ambient session must not leak into child tasks
# ---------------------------------------------------------------------------


async def test_ambient_session_rejects_cross_task_use(schema):
    User = schema
    with pytest.raises(RuntimeError, match="explicit scope"):
        async with db.transaction():
            await User.create(name="parent-side")

            async def child():
                # Child task inherits the context var but not the owner task.
                await User.create(name="child-side")

            await asyncio.gather(child())
    # The cross-task error aborted the whole transaction scope — rolled back.
    assert await User.count() == 0


# ---------------------------------------------------------------------------
# 3. db.raw() write statements
# ---------------------------------------------------------------------------


async def test_raw_update_persists_standalone(schema):
    User = schema
    u = await User.create(name="ann")
    await db.raw("UPDATE review_users SET name = 'ANN' WHERE id = :id", {"id": u.id})
    fresh = await User.find(u.id)
    assert fresh is not None and fresh.name == "ANN"


async def test_raw_update_inside_transaction(schema):
    User = schema
    u = await User.create(name="ann")
    async with db.transaction():
        await db.raw("UPDATE review_users SET name = 'ANN' WHERE id = :id", {"id": u.id})
    fresh = await User.find(u.id)
    assert fresh is not None and fresh.name == "ANN"


async def test_raw_delete_returns_empty_list(schema):
    User = schema
    u = await User.create(name="ann")
    rows = await db.raw("DELETE FROM review_users WHERE id = :id", {"id": u.id})
    assert rows == []
    assert await User.count() == 0


# ---------------------------------------------------------------------------
# 4. Named connections (blueprint: db.connection("analytics"))
# ---------------------------------------------------------------------------


@pytest.fixture()
def two_connections(db_url, tmp_path, monkeypatch):
    analytics = f"sqlite+aiosqlite:///{tmp_path}/analytics.sqlite3"
    from fastplace.orm import manager as manager_mod

    monkeypatch.setattr(
        manager_mod,
        "_connections_from_config",
        lambda: {
            "default": {"driver": "sqlite", "url": db_url},
            "analytics": {"driver": "sqlite", "url": analytics},
        },
        raising=True,
    )
    return analytics


async def test_named_connection_session_and_capabilities(two_connections, User):
    analytics_url = two_connections
    assert db.manager.capabilities("analytics").driver == "sqlite"
    await db.create_all("analytics")
    async with db.connection("analytics") as session:
        await User.create(name="on-analytics")
        await session.flush()
    rows = await db.raw("SELECT name FROM review_users", name="analytics")
    assert rows == [{"name": "on-analytics"}]
    assert "analytics" in analytics_url  # sanity: fixture wiring


async def test_named_connection_transaction_is_independent(two_connections, User):
    await db.create_all()
    await db.create_all("analytics")
    async with db.transaction():
        await User.create(name="on-default")
        async with db.transaction(name="analytics"):
            await User.create(name="on-analytics")
    default_rows = await db.raw("SELECT name FROM review_users")
    analytics_rows = await db.raw("SELECT name FROM review_users", name="analytics")
    assert default_rows == [{"name": "on-default"}]
    assert analytics_rows == [{"name": "on-analytics"}]


async def test_raw_joins_ambient_named_connection(two_connections, User):
    """raw() joins whatever scope is ambient — same rule as ORM operations."""
    await db.create_all()
    await db.create_all("analytics")
    async with db.connection("analytics"):
        await User.create(name="on-analytics")
    await User.create(name="origin")

    async with db.transaction(name="analytics"):
        # Targets the analytics row — only visible if raw() joined the ambient
        # analytics session instead of quietly running on the default one.
        await db.raw("UPDATE review_users SET name = 'moved' WHERE name = 'on-analytics'", {})

    analytics_rows = await db.raw("SELECT name FROM review_users", name="analytics")
    default_rows = await db.raw("SELECT name FROM review_users")
    assert analytics_rows == [{"name": "moved"}]
    assert default_rows == [{"name": "origin"}]


async def test_named_connection_from_config_dict(db_url, tmp_path, monkeypatch):
    """DATABASE_CONNECTIONS in config/database.py registers named connections."""

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "database.py").write_text(
        "DATABASE_CONNECTIONS = {\n    'analytics': {'url': 'sqlite+aiosqlite:///:memory:'},\n}\n"
    )
    from fastplace.config import reset_config

    reset_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    from fastplace.orm.manager import _connections_from_config

    connections = _connections_from_config()
    assert "analytics" in connections
    assert connections["analytics"]["driver"] == "sqlite"
    reset_config()


# ---------------------------------------------------------------------------
# 5. Blueprint scope declaration form: plain @classmethod (cls, query)
# ---------------------------------------------------------------------------


async def test_plain_classmethod_scope_form(db_url):
    class Project(Model):
        __tablename__ = "review_projects"

        id: int = Field(primary_key=True)
        status: str = "in_progress"

        @classmethod
        def in_progress(cls, query):
            return query.where(cls.status == "in_progress")

    await db.create_all()
    await Project.create(status="in_progress")
    await Project.create(status="done")
    projects = await Project.query().in_progress().get()
    assert len(projects) == 1 and projects[0].status == "in_progress"


# ---------------------------------------------------------------------------
# 6. Core conventional scopes (blueprint Query Scopes table)
# ---------------------------------------------------------------------------


async def test_core_is_active_scope_filters_inactive_rows(schema):
    User = schema
    await User.create(name="on", active=True)
    await User.create(name="off", active=False)

    active = await User.query().is_active().get()
    assert [u.name for u in active] == ["on"]

    # Class-level form from the blueprint: User.is_active()
    active_cls = await User.is_active().get()
    assert [u.name for u in active_cls] == ["on"]


async def test_core_is_active_scope_requires_active_column(db_url):
    class Task(Model):
        __tablename__ = "review_tasks_no_active"

        id: int = Field(primary_key=True)
        title: str

    await db.create_all()
    with pytest.raises(AttributeError, match="active"):
        await Task.query().is_active().get()


async def test_core_authorization_scope_filters_ownership(db_url):
    class Invoice(Model):
        __tablename__ = "review_invoices"

        id: int = Field(primary_key=True)
        user_id: int

    await db.create_all()
    mine = await Invoice.create(user_id=7)
    await Invoice.create(user_id=8)

    scoped = await Invoice.query().authorization(7).get()
    assert [inv.id for inv in scoped] == [mine.id]


# ---------------------------------------------------------------------------
# 7. Columns declared on __abstract__ base models
# ---------------------------------------------------------------------------


async def test_abstract_base_columns_inherited(db_url):
    class OwnedModel(Model):
        __abstract__ = True

        org_id: int = Field(index=True)

    class Document(OwnedModel):
        __tablename__ = "review_documents"

        id: int = Field(primary_key=True)
        title: str

    await db.create_all()
    columns = set(Document.__table__.columns.keys())
    assert {"id", "title", "org_id", "created_at", "updated_at", "deleted_at"} <= columns
    doc = await Document.create(title="d1", org_id=5)
    assert doc.org_id == 5


# ---------------------------------------------------------------------------
# 8. refresh() on soft-deleted rows + loud failure after hard delete
# ---------------------------------------------------------------------------


async def test_refresh_on_soft_deleted_instance(schema):
    User = schema
    u = await User.create(name="original")
    await u.delete()

    tombstoned = await User.with_deleted().first()
    assert tombstoned is not None
    tombstoned.name = "local-edit"
    await tombstoned.refresh()
    assert tombstoned.name == "original"


async def test_refresh_raises_after_force_delete(schema):
    User = schema
    u = await User.create(name="gone")
    pk = u.id
    await u.force_delete()

    ghost = User(id=pk, name="ghost")
    with pytest.raises(NotFoundError):
        await ghost.refresh()


# ---------------------------------------------------------------------------
# 9. Mass-assignment guard (OWASP — pk + audit columns never mass-assignable)
# ---------------------------------------------------------------------------


async def test_mass_assignment_blocks_pk_and_audit_columns(schema):
    User = schema
    import datetime

    with pytest.raises(MassAssignmentError, match="id"):
        await User.create(name="a", id=999)
    with pytest.raises(MassAssignmentError):
        await User.create(name="a", deleted_at=datetime.datetime.now(datetime.UTC))
    with pytest.raises(MassAssignmentError):
        await User.create(name="a", created_at=datetime.datetime.now(datetime.UTC))


async def test_mass_assignment_update_blocks_tombstone_clear(schema):
    User = schema
    u = await User.create(name="a")
    await u.delete()
    loaded = await User.with_deleted().first()
    with pytest.raises(MassAssignmentError):
        await loaded.update(deleted_at=None)


async def test_mass_assignment_fillable_allowlist(db_url):
    class Profile(Model):
        __tablename__ = "review_profiles"

        id: int = Field(primary_key=True)
        bio: str = ""
        role: str = "user"

        __fillable__ = ["bio"]

    await db.create_all()
    p = await Profile.create(bio="hi", role="admin")  # role silently ignored
    assert p.role == "user" and p.bio == "hi"


async def test_hidden_columns_excluded_from_to_dict(db_url):
    class Secret(Model):
        __tablename__ = "review_secrets"

        id: int = Field(primary_key=True)
        token: str = "t0k3n"
        label: str = "public"

        __hidden__ = ["token"]

    await db.create_all()
    s = await Secret.create()
    data = s.to_dict()
    assert "token" not in data
    assert data["label"] == "public"


# ---------------------------------------------------------------------------
# 10. Explicit Mapped[...] primary-key escape hatch
# ---------------------------------------------------------------------------


async def test_mapped_escape_hatch_primary_key_not_duplicated(db_url):
    class NaturalKey(Model):
        __tablename__ = "review_natural"

        # Natural keys opt into mass assignment explicitly.
        __fillable__ = ["code"]
        code: Mapped[str] = mapped_column(primary_key=True)
        label: str = ""

    await db.create_all()
    pk = NaturalKey.__table__.primary_key.columns
    assert [c.name for c in pk] == ["code"]
    assert "id" not in NaturalKey.__table__.columns
    row = await NaturalKey.create(code="A1", label="first")
    assert row.code == "A1"


# ---------------------------------------------------------------------------
# 11. Unannotated relationship markers are built, not silently dropped
# ---------------------------------------------------------------------------


async def test_unannotated_relationship_marker(db_url):
    class Customer(Model):
        __tablename__ = "review_customers"

        id: int = Field(primary_key=True)
        name: str

    class Invoice(Model):
        __tablename__ = "review_invoices_unannotated"

        id: int = Field(primary_key=True)
        amount: int = 0
        customer_id: int = Field(foreign_key="review_customers.id")
        customer = belongs_to("Customer", backref="invoices")

    await db.create_all()
    c = await Customer.create(name="acme")
    inv = await Invoice.create(amount=5, customer_id=c.id)
    loaded = await inv.relation("customer")
    assert loaded is not None and loaded.name == "acme"
    invoices = await c.relation("invoices")
    assert [i.amount for i in invoices] == [5]


# ---------------------------------------------------------------------------
# 12. Self-referential relationships (adjacency list)
# ---------------------------------------------------------------------------


async def test_self_referential_has_many_with_backref(db_url):
    class Node(Model):
        __tablename__ = "review_nodes"

        id: int = Field(primary_key=True)
        label: str = ""
        parent_id: int | None = Field(foreign_key="review_nodes.id", nullable=True)
        children: list[Node] = has_many("Node", backref="parent")

    await db.create_all()
    root = await Node.create(label="root")
    child = await Node.create(label="child", parent_id=root.id)

    kids = await root.relation("children")
    assert [k.label for k in kids] == ["child"]
    parent = await child.relation("parent")
    assert parent is not None and parent.label == "root"


# ---------------------------------------------------------------------------
# 13. :memory: pool concurrency — serialized, not corrupted
# ---------------------------------------------------------------------------


async def test_memory_pool_concurrent_transactions_isolated(schema):
    User = schema

    async def failing_tx():
        async with db.transaction():
            await User.create(name="rolled-back")
            await asyncio.sleep(0.05)
            raise RuntimeError("fail")

    results = await asyncio.gather(
        failing_tx(), User.create(name="committed"), return_exceptions=True
    )
    assert isinstance(results[0], RuntimeError)
    names = [u.name for u in await User.all()]
    assert names == ["committed"]


# ---------------------------------------------------------------------------
# 14. Capability honesty: MySQL has no PostgreSQL-style FTS path
# ---------------------------------------------------------------------------


def test_mysql_capabilities_do_not_claim_full_text():
    assert Capabilities("mysql").supports_full_text is False
    assert Capabilities("postgresql").supports_full_text is True


# ---------------------------------------------------------------------------
# 15. reset_manager disposes cached engines
# ---------------------------------------------------------------------------


async def test_reset_manager_disposes_engines(schema):
    User = schema
    await User.count()  # force engine creation
    manager = db.manager
    assert manager._engines  # noqa: SLF001 — asserting internals of the fixture
    from fastplace.orm.manager import get_manager, reset_manager

    reset_manager()
    assert not manager._engines  # noqa: SLF001
    assert get_manager() is not manager
    reset_db()
