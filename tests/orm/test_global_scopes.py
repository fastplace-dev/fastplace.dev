"""Global-scope engine — soft delete is one scope in an extensible registry.

The blueprint (§8 Query Scopes table) lists ``soft-delete`` as a *global —
automatic (core)* scope and reserves the same extension point for packages
(``company`` via fastplace-tenancy). The engine here is what a package plugs
into: criteria that apply to every query on a model unless explicitly removed.
"""

from __future__ import annotations

import pytest

from fastplace.db import db
from fastplace.orm import Field, Model


class ArchivedScope:
    """A minimal third-party-style global scope: hide archived rows."""

    def criteria(self, model):  # noqa: ANN001 — matches GlobalScope signature
        return [model.archived.is_(False)]


@pytest.fixture()
def Note():
    class Note(Model):
        __tablename__ = "scope_notes"

        id: int = Field(primary_key=True)
        title: str
        archived: bool = False

    return Note


@pytest.fixture()
async def notes(Note, db_url):
    await db.create_all()
    a = await Note.create(title="fresh")
    b = await Note.create(title="old", archived=True)
    return Note, a, b


async def test_soft_delete_is_a_registered_global_scope():
    """The core soft-delete filter must ride the same engine as any package
    scope — one registry, not a hard-wired branch."""
    assert "soft_delete" in Model._resolved_global_scopes()


async def test_custom_global_scope_filters_every_query(notes):
    Note, fresh, archived = notes
    Note.add_global_scope("archived", ArchivedScope())

    titles = [n.title for n in await Note.all()]
    assert titles == ["fresh"]
    assert await Note.count() == 1
    assert (await Note.where(Note.title == "old").first()) is None

    # Scope criteria apply on every entry point — first/find too.
    assert (await Note.find(archived.id)) is None
    assert (await Note.find(fresh.id)) is not None


async def test_without_global_scope_escapes_exactly_one_scope(notes):
    Note, fresh, archived = notes
    Note.add_global_scope("archived", ArchivedScope())

    got = await Note.without_global_scope("archived").order_by(Note.id).get()
    assert [n.title for n in got] == ["fresh", "old"]

    # Removing one scope leaves the others (soft delete) active.
    await fresh.delete()
    got = await Note.without_global_scope("archived").order_by(Note.id).get()
    assert [n.title for n in got] == ["old"]


async def test_without_global_scopes_drops_every_scope(notes):
    Note, fresh, archived = notes
    Note.add_global_scope("archived", ArchivedScope())
    await fresh.delete()

    everything = await Note.without_global_scopes().order_by(Note.id).get()
    assert [n.title for n in everything] == ["fresh", "old"]


async def test_added_scope_does_not_leak_across_models(notes, db_url):
    Note, *_ = notes
    Note.add_global_scope("archived", ArchivedScope())

    class Memo(Model):
        __tablename__ = "scope_memos"

        id: int = Field(primary_key=True)
        title: str
        archived: bool = False

    await db.create_all()
    await Memo.create(title="m", archived=True)

    # The sibling model never sees Note's scope; soft delete stays for both.
    assert "archived" not in Memo._resolved_global_scopes()
    assert await Memo.count() == 1


async def test_scope_registered_on_a_shared_base_covers_subclasses(notes):
    """App-shared bases in app/models are the natural place for a scope."""
    Note, *_ = notes

    class SharedBase(Model):
        __abstract__ = True
        __global_scopes__ = {"archived": ArchivedScope()}

    class Task(SharedBase):
        __tablename__ = "scope_tasks"

        id: int = Field(primary_key=True)
        name: str
        archived: bool = False

    await db.create_all()
    await Task.create(name="live")
    await Task.create(name="done", archived=True)

    assert [t.name for t in await Task.all()] == ["live"]
    # The abstract base itself is unaffected; Model is unaffected.
    assert "archived" not in Model._resolved_global_scopes()


def test_remove_global_scope(notes):
    Note, *_ = notes
    Note.add_global_scope("archived", ArchivedScope())
    Note.remove_global_scope("archived")
    assert "archived" not in Note._resolved_global_scopes()


def test_add_global_scope_rejects_reserved_soft_delete(notes):
    """Packages must not silently replace the core soft-delete scope — the
    accessors with_deleted()/only_deleted() are wired to that exact scope."""
    Note, *_ = notes
    with pytest.raises(ValueError, match="reserved"):
        Note.add_global_scope("soft_delete", ArchivedScope())


def test_remove_global_scope_rejects_reserved_soft_delete(notes):
    """Symmetric with add: the core scope is structural, not removable —
    per-query escape (with_deleted()) is the documented path."""
    Note, *_ = notes
    with pytest.raises(ValueError, match="soft_delete"):
        Note.remove_global_scope("soft_delete")


async def test_remove_global_scope_on_an_inherited_scope(notes):
    """A subclass must be able to drop a scope declared on a shared base —
    removal writes a tombstone that shadows the base entry in the MRO merge
    (a plain pop is resurrected by the base's own dict on the next resolve)."""
    Note, fresh, archived = notes

    class SharedBase(Model):
        __abstract__ = True
        __global_scopes__ = {"archived": ArchivedScope()}

    class Doc(SharedBase):
        __tablename__ = "scope_docs"

        id: int = Field(primary_key=True)
        name: str
        archived: bool = False

    await db.create_all()
    await Doc.create(name="live")
    await Doc.create(name="gone", archived=True)
    assert [d.name for d in await Doc.all()] == ["live"]

    Doc.remove_global_scope("archived")
    assert "archived" not in Doc._resolved_global_scopes()
    assert [d.name for d in await Doc.query().order_by(Doc.id).get()] == ["live", "gone"]
    # The base and Model keep the scope.
    assert "archived" in SharedBase._resolved_global_scopes()


async def test_base_scope_removal_reaches_subclasses(notes):
    """Removal (or replacement) on the base must reach subclasses that have
    mutated their own registry — mutations write only own entries, so the
    MRO merge recomputes inheritance at read time instead of freezing it."""
    Note, *_ = notes

    class SharedBase(Model):
        __abstract__ = True
        __global_scopes__ = {"archived": ArchivedScope()}

    class Doc(SharedBase):
        __tablename__ = "scope_docs2"

        id: int = Field(primary_key=True)
        name: str
        archived: bool = False

    # Snapshot moment: Doc mutates its own registry…
    Doc.add_global_scope("custom", ArchivedScope())
    assert set(Doc._resolved_global_scopes()) >= {"archived", "custom"}

    # …then the base drops its scope. The change must propagate to Doc.
    SharedBase.remove_global_scope("archived")
    assert "archived" not in SharedBase._resolved_global_scopes()
    assert "archived" not in Doc._resolved_global_scopes()
    assert "custom" in Doc._resolved_global_scopes()


async def test_only_deleted_mode_wins_over_soft_delete_escape(notes):
    """only_deleted() is an explicit mode choice — pairing it with the
    soft-delete escape must not silently widen to every row."""
    Note, fresh, _archived = notes
    await fresh.delete()

    rows = await Note.only_deleted().without_global_scope("soft_delete").get()
    assert [n.title for n in rows] == ["fresh"]


async def test_refresh_bypasses_custom_global_scopes(notes):
    """refresh() re-fetches a row already in hand by pk — custom scopes (a
    row can be archived mid-request) must not turn that into NotFoundError."""
    Note, fresh, _archived = notes
    Note.add_global_scope("archived", ArchivedScope())

    in_hand = await Note.without_global_scope("archived").find(fresh.id)
    await fresh.update(archived=True)  # the scope now hides this row

    await in_hand.refresh()
    assert in_hand.archived is True


def test_search_statements_apply_global_scope_criteria(notes):
    """vector_search()/full_text_search() must ride the same scope engine as
    every other read — a tenancy scope that search bypasses is data leakage."""
    Note, *_ = notes
    Note.add_global_scope("archived", ArchivedScope())

    criteria = Note._search_scope_criteria()
    compiled = [str(c) for c in criteria]
    assert any("archived" in c for c in compiled)
    # Soft delete rides along too.
    assert any("deleted_at" in c for c in compiled)
