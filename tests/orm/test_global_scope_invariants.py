"""Global-scope invariants across EVERY public SQL-emitting path (G5).

A past review caught four methods forgetting the scope engine; the class of
the bug is wider than those four — this suite closes the class. A soft-delete
filter and a contextvar-backed tenant scope (the exact shape
fastplace-tenancy's ``CompanyScope`` has) are registered on the models, and
every read path a Model or QueryBuilder can emit SQL through is walked with
two invariants asserted on whatever comes back:

* soft delete — tombstoned rows are invisible by default;
* tenant scope — another tenant's rows are invisible.

Paths that intentionally BYPASS the scopes are pinned as opt-in by dedicated
tests at the bottom of this module (``with_deleted()``, ``only_deleted()``,
``without_global_scope()``, ``without_global_scopes()``, ``refresh()``, and
``upsert()``'s physical-key match). The write-side tenant STAMP is the
tenancy package's contract, covered end-to-end in
``tests/tenancy/test_write_path_isolation.py``.
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace

import pytest

from fastplace.db import db
from fastplace.orm import Field, GlobalScope, Model, belongs_to, has_one

#: The bound tenant for scope criteria — mirrors fastplace_tenancy's
#: company ContextVar without importing the package into the core suite.
_current_tenant: contextvars.ContextVar[int] = contextvars.ContextVar(
    "gsi_current_tenant", default=1
)


class _TenantScope(GlobalScope):
    """Generic stand-in for a contextvar-backed tenant global scope."""

    def criteria(self, model: type) -> list:
        return [model.tenant_id == _current_tenant.get()]


def _assert_scoped(rows: list) -> None:
    """The two invariants on every row any path hands back (tenant 1 bound).

    Each invariant applies to the models that carry the column: an unscoped
    model (Note) has no tenant boundary of its own, but soft delete holds on
    every model — including rows arriving through a scoped relationship hop.
    """
    for row in rows:
        model = type(row)
        if "tenant_id" in model.__table__.columns:
            assert row.tenant_id == 1, f"cross-tenant row leaked through: {row!r}"
        assert row.deleted_at is None, f"soft-deleted row leaked through: {row!r}"


@pytest.fixture()
def domain(db_url):
    """Member (tenant-scoped) → Profile (tenant-scoped); Note is unscoped.

    Note is deliberately scope-free: the eager-load paths must apply the
    TARGET's scopes (member, profile) even though the query root carries
    none — that is the exact shape of the multi-hop leak this suite pins.
    """

    class Member(Model):
        __tablename__ = "gsi_members"

        name: str
        handle: str = Field(unique=True)
        tenant_id: int
        profile: MemberProfile = has_one("MemberProfile", backref="member")

    class MemberProfile(Model):
        __tablename__ = "gsi_member_profiles"

        bio: str
        tenant_id: int
        member_id: int = Field(foreign_key="gsi_members.id", unique=True)

    class Note(Model):
        __tablename__ = "gsi_notes"

        body: str
        member_id: int = Field(foreign_key="gsi_members.id")
        member: Member = belongs_to("Member", backref="notes")

    for scoped in (Member, MemberProfile):
        scoped.add_global_scope("tenant", _TenantScope())
    return SimpleNamespace(Member=Member, MemberProfile=MemberProfile, Note=Note)


@pytest.fixture()
def as_tenant1():
    token = _current_tenant.set(1)
    yield
    _current_tenant.reset(token)


@pytest.fixture()
async def world(domain):
    """Tenant 1: alice (alive) + ghost (soft-deleted); tenant 2: mallory."""
    await db.create_all()
    Member, MemberProfile, Note = domain.Member, domain.MemberProfile, domain.Note

    alice = await Member.create(name="alice", handle="a", tenant_id=1)
    ghost = await Member.create(name="ghost", handle="g", tenant_id=1)
    await ghost.delete()
    mallory = await Member.create(name="mallory", handle="m", tenant_id=2)

    await MemberProfile.create(bio="alice-bio", tenant_id=1, member_id=alice.id)
    await MemberProfile.create(bio="mallory-bio", tenant_id=2, member_id=mallory.id)

    await Note.create(body="by-alice", member_id=alice.id)
    await Note.create(body="by-ghost", member_id=ghost.id)
    await Note.create(body="by-mallory", member_id=mallory.id)

    return SimpleNamespace(
        Member=Member,
        MemberProfile=MemberProfile,
        Note=Note,
        alice=alice,
        ghost=ghost,
        mallory=mallory,
    )


# ---------------------------------------------------------------------------
# the walk — one parameterized test per public read path
# ---------------------------------------------------------------------------
def _read_paths(world: SimpleNamespace) -> dict[str, object]:
    """name → async zero-arg callable returning the rows that path produced.

    Normalized to a list of model instances so every path asserts the same
    two invariants. Keys are pinned by test_walked_surface_is_complete.
    """
    Member, MemberProfile, Note = world.Member, world.MemberProfile, world.Note

    async def first() -> list:
        row = await Member.query().order_by(Member.id).first()
        return [row] if row is not None else []

    async def find_own() -> list:
        return [await Member.find_or_fail(world.alice.id)]

    async def paginate() -> list:
        page = await Member.query().order_by(Member.id).paginate(per_page=10)
        return list(page.items)

    async def cursor_paginate() -> list:
        page = await Member.query().order_by(Member.id).cursor_paginate(per_page=10)
        return list(page.items)

    async def chunk() -> list:
        return [row async for row in Member.query().order_by(Member.id).chunk(2)]

    async def chunk_by_id() -> list:
        return [row async for row in Member.query().chunk_by_id(2)]

    async def relation() -> list:
        rows = []
        for note in await Note.query().order_by(Note.id).get():
            loaded = await note.relation("member")
            if loaded is not None:
                rows.append(loaded)
        return rows

    async def eager_single_hop() -> list:
        notes = await Note.with_("member").order_by(Note.id).get()
        return [note.member for note in notes if note.member is not None]

    async def eager_multi_hop() -> list:
        notes = await Note.with_("member.profile").order_by(Note.id).get()
        return [note.member for note in notes if note.member is not None]

    async def eager_many_direction() -> list:
        profiles = await MemberProfile.with_("member.notes").order_by(MemberProfile.id).get()
        rows = []
        for profile in profiles:
            rows.append(profile.member)
            rows.extend(profile.member.notes)
        return rows

    async def classmethod_where() -> list:
        return await Member.where(Member.handle != "").order_by(Member.id).get()

    return {
        "all": lambda: Member.all(),
        "query_get": lambda: Member.query().get(),
        "query_where_chain": lambda: (
            Member.query()
            .where(Member.id > 0)
            .where(Member.handle != "zzz")
            .order_by(Member.id)
            .get()
        ),
        "first": first,
        "find_own": find_own,
        "paginate": paginate,
        "cursor_paginate": cursor_paginate,
        "chunk": chunk,
        "chunk_by_id": chunk_by_id,
        "relation": relation,
        "eager_single_hop": eager_single_hop,
        "eager_multi_hop": eager_multi_hop,
        "eager_many_direction": eager_many_direction,
        "classmethod_where": classmethod_where,
    }


#: The public read surface the walk must keep covering — a path removed from
#: _read_paths reddens the completeness test below, not silently the suite.
_EXPECTED_PATHS = frozenset(
    {
        "all",
        "query_get",
        "query_where_chain",
        "first",
        "find_own",
        "paginate",
        "cursor_paginate",
        "chunk",
        "chunk_by_id",
        "relation",
        "eager_single_hop",
        "eager_multi_hop",
        "eager_many_direction",
        "classmethod_where",
    }
)


def test_walked_surface_is_complete(world):
    """G5 guard: the walk table still covers the full documented surface."""
    assert set(_read_paths(world)) == _EXPECTED_PATHS


@pytest.mark.parametrize("path", sorted(_EXPECTED_PATHS))
async def test_every_read_path_holds_both_invariants(world, as_tenant1, path):
    rows = await _read_paths(world)[path]()
    _assert_scoped(rows)
    # tenant 1's alive member is reachable through every path (a path that
    # returned NOTHING would trivially satisfy the invariants above).
    assert [row.name for row in rows if type(row) is world.Member] == ["alice"]


async def test_find_by_foreign_pk_misses(world, as_tenant1):
    """find() by another tenant's primary key — the IDOR probe — misses."""
    assert await world.Member.find(world.mallory.id) is None
    with pytest.raises(Exception) as exc:
        await world.Member.find_or_fail(world.mallory.id)
    assert "not found" in str(exc.value)


async def test_count_and_exists_hold_the_scopes(world, as_tenant1):
    Member = world.Member
    assert await Member.count() == 1  # ghost tombstoned, mallory other-tenant
    assert await Member.query().where(Member.id == world.mallory.id).exists() is False
    assert await Member.query().where(Member.id == world.alice.id).exists() is True


# ---------------------------------------------------------------------------
# write paths — the locate side must ride the scopes
# ---------------------------------------------------------------------------
async def test_first_or_create_locates_inside_the_scope(world, as_tenant1):
    Member = world.Member
    # The locate must not see tenant 2's mallory nor tombstoned ghost.
    row = await Member.first_or_create(
        defaults={"tenant_id": 1, "handle": "m-clone"}, name="mallory"
    )
    assert row.id != world.mallory.id
    _assert_scoped([row])

    ghost2 = await Member.first_or_create(
        defaults={"tenant_id": 1, "handle": "g-clone"}, name="ghost"
    )
    assert ghost2.id != world.ghost.id
    _assert_scoped([ghost2])


async def test_update_or_create_locates_inside_the_scope(world, as_tenant1):
    Member = world.Member
    row = await Member.update_or_create(
        defaults={"tenant_id": 1, "handle": "m-clone"}, name="mallory"
    )
    assert row.id != world.mallory.id
    again = await Member.update_or_create(
        defaults={"tenant_id": 1, "handle": "m-clone"}, name="mallory"
    )
    assert again.id == row.id  # located its OWN row, never tenant 2's


async def test_delete_hides_the_row_from_default_reads(world, as_tenant1):
    alice = await world.Member.find_or_fail(world.alice.id)
    await alice.delete()
    assert await world.Member.find(alice.id) is None
    assert [m.name for m in await world.Member.only_deleted().get()] == ["alice", "ghost"]


async def test_search_paths_ride_the_scope_engine(world, as_tenant1):
    """vector_search()/full_text_search() filter through _search_scope_criteria —
    the criteria must be the same clauses the query engine applies."""
    Member = world.Member
    scoped = " ".join(str(c) for c in Member.query()._global_scope_criteria())
    search = " ".join(str(c) for c in Member._search_scope_criteria())
    assert search == scoped
    assert "deleted_at" in search
    assert "tenant_id" in search


# ---------------------------------------------------------------------------
# intentional bypasses — each must stay OPT-IN
# ---------------------------------------------------------------------------
async def test_with_deleted_is_opt_in_and_keeps_the_tenant_scope(world, as_tenant1):
    rows = await world.Member.with_deleted().order_by(world.Member.id).get()
    assert sorted(m.name for m in rows) == ["alice", "ghost"]  # no mallory ever


async def test_only_deleted_is_opt_in_and_keeps_the_tenant_scope(world, as_tenant1):
    rows = await world.Member.only_deleted().get()
    assert [m.name for m in rows] == ["ghost"]


async def test_without_global_scope_is_opt_in_per_name(world, as_tenant1):
    rows = await world.Member.query().without_global_scope("tenant").order_by(world.Member.id).get()
    # tenant scope dropped explicitly — soft delete still applies (no ghost),
    # and every tenant's rows become visible (mallory included).
    assert sorted(m.name for m in rows) == ["alice", "mallory"]


async def test_without_global_scopes_is_opt_in_for_everything(world, as_tenant1):
    rows = await world.Member.query().without_global_scopes().order_by(world.Member.id).get()
    assert sorted(m.name for m in rows) == ["alice", "ghost", "mallory"]


async def test_refresh_bypasses_scopes_by_design(world, as_tenant1):
    """refresh() reloads a row already in hand — its docstring pins that
    scopes must not turn a reload into NotFoundError (opt-in by holding
    the instance, not by query shape)."""
    mallory = (
        await world.Member.query()
        .without_global_scopes()
        .where(world.Member.id == world.mallory.id)
        .first()
    )
    refreshed = await mallory.refresh()
    assert refreshed.name == "mallory"


async def test_upsert_matches_on_physical_keys_documented_bypass(world, as_tenant1):
    """upsert() is a batch write, not a scoped read: its UPDATE matches by
    physical unique keys, so global scopes neither hide a row from the match
    nor constrain what it writes (core contract, Model.upsert docstring).
    The tenancy package closes this door for CompanyScopedModel — see
    tests/tenancy/test_write_path_isolation.py."""
    Member = world.Member
    written = await Member.upsert(
        [{"handle": "m", "name": "mallory-renamed", "tenant_id": 2}],
        unique_by=["handle"],
    )
    assert written == 1
    # The physical-key match updated tenant 2's row — and no default read
    # under tenant 1 can see it either before or after.
    assert await world.Member.find(world.mallory.id) is None
    unscoped = await Member.query().without_global_scopes().where(Member.handle == "m").first()
    assert unscoped is not None and unscoped.name == "mallory-renamed"
