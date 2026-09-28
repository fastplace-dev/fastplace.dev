"""Through-relationships and morph pivots (sweep-G7).

``has_many_through`` / ``has_one_through`` walk an intermediate table as a
viewonly relationship; ``morph_to_many`` joins a pivot that also carries the
owner's polymorphic type column (morph_map-aware primaryjoin).
"""

from __future__ import annotations

import pytest

from fastplace.db import db
from fastplace.orm import (
    Field,
    Model,
    belongs_to,
    has_many_through,
    has_one,
    has_one_through,
    morph_to_many,
    pivot_table,
    reset_morph_map,
)


@pytest.fixture(autouse=True)
def _clean_morph_map():
    reset_morph_map()
    yield
    reset_morph_map()


@pytest.fixture()
def blog(db_url):
    """Posts reach Tags through ``post_tags``; Projects reach Users through a
    morph pivot whose ``member_type`` column separates owners by type."""

    pivot_table("post_tags", "through_posts", "through_tags")

    class Post(Model):
        __tablename__ = "through_posts"

        id: int = Field(primary_key=True)
        title: str

        tags: list[Tag] = has_many_through("Tag", through="post_tags")

    class Tag(Model):
        __tablename__ = "through_tags"

        id: int = Field(primary_key=True)
        name: str

    class Membership(Model):
        __tablename__ = "through_memberships"

        id: int = Field(primary_key=True)
        user_id: int = Field(foreign_key="through_users.id")
        team_id: int = Field(foreign_key="through_teams.id")

        user: User = belongs_to("User", back_populates="membership")
        team: Team = belongs_to("Team")

    class User(Model):
        __tablename__ = "through_users"

        id: int = Field(primary_key=True)
        name: str

        membership: Membership = has_one("Membership", back_populates="user")
        # The team a user belongs to, via their single membership row.
        team: Team = has_one_through("Team", through="through_memberships")

    class Team(Model):
        __tablename__ = "through_teams"

        id: int = Field(primary_key=True)
        name: str

        members: list[User] = has_many_through("User", through="through_memberships")

    class Project(Model):
        __tablename__ = "through_projects2"

        id: int = Field(primary_key=True)
        name: str

        # type_name= pins the alias the pivot's type column carries — the
        # same string the owner side writes when it creates members.
        members: list[User] = morph_to_many(
            "User",
            through="through_member_pivots",
            type_field="member_type",
            id_field="project_id",
            foreign_field="member_id",
            type_name="proj_alias",
        )

    from sqlalchemy import Column, ForeignKey, Integer, String, Table

    from fastplace.orm.model import Model as _Model

    Table(
        "through_member_pivots",
        _Model.metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("through_projects2.id", ondelete="CASCADE")),
        Column("member_type", String, nullable=False),
        Column("member_id", Integer, ForeignKey("through_users.id", ondelete="CASCADE")),
    )

    return Post, Tag, User, Team, Membership, Project


@pytest.fixture()
async def seeded_through(blog):
    Post, Tag, User, Team, Membership, Project = blog
    await db.create_all()

    red = await Tag.create(name="red")
    warm = await Tag.create(name="warm")
    alpha = await Post.create(title="alpha")
    beta = await Post.create(title="beta")
    await Post.create(title="gamma")

    pivot = Model.metadata.tables["post_tags"]
    from fastplace.db import db as _db

    async with _db.manager.engine("default").begin() as conn:
        await conn.execute(
            pivot.insert(),
            [
                {"through_post_id": alpha.id, "through_tag_id": red.id},
                {"through_post_id": alpha.id, "through_tag_id": warm.id},
                {"through_post_id": beta.id, "through_tag_id": red.id},
            ],
        )

    lin = await User.create(name="lin")
    kim = await User.create(name="kim")
    team = await Team.create(name="core")
    await Membership.create(user_id=lin.id, team_id=team.id)

    apollo = await Project.create(name="apollo")
    await Project.create(name="helios")
    member_pivot = Model.metadata.tables["through_member_pivots"]
    async with _db.manager.engine("default").begin() as conn:
        await conn.execute(
            member_pivot.insert(),
            [
                # typed for the project alias — the project side must see it
                {"project_id": apollo.id, "member_type": "proj_alias", "member_id": lin.id},
                {"project_id": apollo.id, "member_type": "proj_alias", "member_id": kim.id},
                # typed for the team — invisible to the project side
                {"project_id": apollo.id, "member_type": "through_teams2", "member_id": lin.id},
            ],
        )
    return blog


async def test_has_many_through_walks_the_pivot(seeded_through):
    Post = seeded_through[0]
    loaded = await Post.query().with_("tags").get()
    by_title = {p.title: p for p in loaded}
    assert sorted(t.name for t in by_title["alpha"].tags) == ["red", "warm"]
    assert [t.name for t in by_title["beta"].tags] == ["red"]
    assert by_title["gamma"].tags == []


async def test_has_many_through_relation_loads_lazily(seeded_through):
    Post = seeded_through[0]
    post = await Post.query().where(Post.title == "alpha").first()
    tags = await post.relation("tags")
    assert sorted(t.name for t in tags) == ["red", "warm"]


async def test_has_one_through_loads_the_single_intermediate_row(seeded_through):
    User, Team = seeded_through[2], seeded_through[3]
    user = await User.query().with_("team").where(User.name == "lin").first()
    assert user.team is not None
    assert user.team.name == "core"
    # The far side walks back across the same intermediate table.
    team = await Team.query().with_("members").where(Team.name == "core").first()
    assert [u.name for u in team.members] == ["lin"]


async def test_morph_to_many_filters_by_the_owner_type(seeded_through):
    Project = seeded_through[5]
    loaded = await Project.query().with_("members").get()
    by_name = {p.name: p for p in loaded}
    # Only rows typed for the project alias load — the team-owned member row
    # must stay invisible to the project side.
    assert sorted(u.name for u in by_name["apollo"].members) == ["kim", "lin"]
    assert by_name["helios"].members == []


async def test_morph_to_many_relation_loads_lazily(seeded_through):
    Project = seeded_through[5]
    project = await Project.query().where(Project.name == "apollo").first()
    members = await project.relation("members")
    assert sorted(u.name for u in members) == ["kim", "lin"]


async def test_morph_to_many_resolves_a_non_id_target_pk(db_url):
    """The owner side joins on its machinery-resolved pk; the target side
    must too. A target declaring a non-id primary key used to hardcode
    ``.id`` into the secondaryjoin and break mapper configuration with an
    opaque SQLAlchemy AttributeError."""
    from sqlalchemy import Column, ForeignKey, Integer, String, Table

    from fastplace.orm.model import Model as _Model

    Table(
        "natural_taggables",
        _Model.metadata,
        Column("id", Integer, primary_key=True),
        Column("post_id", Integer, ForeignKey("natural_posts.id", ondelete="CASCADE")),
        Column("tag_type", String, nullable=False),
        Column("tag_code", String, ForeignKey("natural_tags.code", ondelete="CASCADE")),
    )

    class NaturalTag(Model):
        __tablename__ = "natural_tags"

        code: str = Field(primary_key=True)
        label: str

    class NaturalPost(Model):
        __tablename__ = "natural_posts"

        id: int = Field(primary_key=True)
        title: str

        tags: list[NaturalTag] = morph_to_many(
            "NaturalTag",
            through="natural_taggables",
            type_field="tag_type",
            id_field="post_id",
            foreign_field="tag_code",
            type_name="natural_posts",
        )

    await db.create_all()
    # natural-key pks are guarded from mass assignment — set them directly
    hot = NaturalTag(label="hot take")
    hot.code = "hot"
    await hot.save()
    cold = NaturalTag(label="cold take")
    cold.code = "cold"
    await cold.save()
    post = await NaturalPost.create(title="p1")
    pivot = Model.metadata.tables["natural_taggables"]
    async with db.manager.engine("default").begin() as conn:
        await conn.execute(
            pivot.insert(),
            [
                {"post_id": post.id, "tag_type": "natural_posts", "tag_code": "hot"},
                {"post_id": post.id, "tag_type": "natural_posts", "tag_code": "cold"},
            ],
        )
    loaded = await NaturalPost.query().with_("tags").first()
    assert sorted(t.code for t in loaded.tags) == ["cold", "hot"]
