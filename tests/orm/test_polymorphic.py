"""Polymorphic relationships (blueprint §8) — morph_many / morph_one / morph_to.

One child table (comments) owned by several parent types through a
``{x}_type`` / ``{x}_id`` pair — conventional morphs riding SQLAlchemy's
primaryjoin machinery. Reads work from both sides: eager ``with_()`` and lazy
``relation()`` on the owner, ``await child.morph_to()`` on the child. Writes
set the type + id pair explicitly (the type string is part of the join, so a
relationship append cannot infer it).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastplace.db import db
from fastplace.orm import Field, Model, reset_morph_map


@pytest.fixture(autouse=True)
def _clean_morph_map():
    """The morph map is process-global; per-test model classes must not see
    the previous test's (disposed) registrations."""
    reset_morph_map()
    yield
    reset_morph_map()


@pytest.fixture()
def domain():
    from fastplace.orm import morph_many, morph_one, morph_to

    class Comment(Model):
        __tablename__ = "poly_comments"

        id: int = Field(primary_key=True)
        body: str
        commentable_type: str
        commentable_id: int

        parent = morph_to("commentable_type", "commentable_id")

    class Post(Model):
        __tablename__ = "poly_posts"

        id: int = Field(primary_key=True)
        title: str

        comments: list[Comment] = morph_many(
            "Comment", type_field="commentable_type", id_field="commentable_id"
        )

    class Video(Model):
        __tablename__ = "poly_videos"

        id: int = Field(primary_key=True)
        name: str

        comments: list[Comment] = morph_many(
            "Comment", type_field="commentable_type", id_field="commentable_id"
        )

    class Attachment(Model):
        __tablename__ = "poly_attachments"

        id: int = Field(primary_key=True)
        url: str
        attachable_type: str
        attachable_id: int

    class Article(Model):
        __tablename__ = "poly_articles"

        id: int = Field(primary_key=True)
        headline: str

        banner: Attachment = morph_one(
            "Attachment", type_field="attachable_type", id_field="attachable_id"
        )

    return SimpleNamespace(
        Comment=Comment, Post=Post, Video=Video, Attachment=Attachment, Article=Article
    )


@pytest.fixture()
async def seeded(domain, db_url):
    from fastplace.orm import morph_map

    morph_map({"poly_posts": domain.Post, "poly_videos": domain.Video})
    await db.create_all()

    post1 = await domain.Post.create(title="p1")
    post2 = await domain.Post.create(title="p2")
    video = await domain.Video.create(name="v1")
    c1 = await domain.Comment.create(
        body="on p1", commentable_type="poly_posts", commentable_id=post1.id
    )
    c2 = await domain.Comment.create(
        body="on p2", commentable_type="poly_posts", commentable_id=post2.id
    )
    c3 = await domain.Comment.create(
        body="on v1", commentable_type="poly_videos", commentable_id=video.id
    )
    return SimpleNamespace(post1=post1, post2=post2, video=video, c1=c1, c2=c2, c3=c3)


async def test_morph_many_returns_only_the_owning_parents_comments(seeded):
    from fastplace.orm import morph_map

    post1 = await morph_map()["poly_posts"].with_("comments").find(seeded.post1.id)
    assert [c.body for c in post1.comments] == ["on p1"]

    video = await morph_map()["poly_videos"].with_("comments").find(seeded.video.id)
    assert [c.body for c in video.comments] == ["on v1"]


async def test_morph_many_lazy_relation_access(seeded, domain):
    post2 = await domain.Post.find(seeded.post2.id)
    comments = await post2.relation("comments")
    assert [c.body for c in comments] == ["on p2"]


async def test_morph_to_resolves_each_parent_type(seeded, domain):
    assert (await seeded.c1.morph_to()).title == "p1"
    assert (await seeded.c3.morph_to()).name == "v1"


async def test_morph_one_attachment_per_owner(domain, db_url):
    await db.create_all()
    article = await domain.Article.create(headline="a1")
    await domain.Attachment.create(
        url="cover.png", attachable_type="poly_articles", attachable_id=article.id
    )
    # A second attachment of the same shape exists for another article.
    other = await domain.Article.create(headline="a2")
    await domain.Attachment.create(
        url="other.png", attachable_type="poly_articles", attachable_id=other.id
    )

    loaded = await domain.Article.with_("banner").find(article.id)
    assert loaded.banner.url == "cover.png"


async def test_morph_to_without_a_marker_explains(seeded, db_url):
    class Bare(Model):
        __tablename__ = "poly_bare"

        id: int = Field(primary_key=True)

    await db.create_all()
    row = await Bare.create()
    with pytest.raises(AttributeError, match="morph_to"):
        await row.morph_to()


async def test_morph_to_unknown_type_names_the_value(seeded, domain):
    ghost = await domain.Comment.create(
        body="orphan", commentable_type="poly_ghosts", commentable_id=1
    )
    with pytest.raises(ValueError, match="poly_ghosts"):
        await ghost.morph_to()


async def test_explicit_type_name_alias_builds(db_url):
    """``type_name=`` pins the morph type string (an alias table name); the
    leftover template extras must not leak into the relationship kwargs."""
    from fastplace.orm import morph_many

    class Shout(Model):
        __tablename__ = "alias_shouts"

        id: int = Field(primary_key=True)
        body: str
        shoutable_type: str
        shoutable_id: int

    class Channel(Model):
        __tablename__ = "alias_channels"

        id: int = Field(primary_key=True)
        name: str

        shouts: list[Shout] = morph_many(
            "Shout", type_field="shoutable_type", id_field="shoutable_id", type_name="channel"
        )

    await db.create_all()
    ch = await Channel.create(name="c1")
    other = await Channel.create(name="c2")
    await Shout.create(body="hey", shoutable_type="channel", shoutable_id=ch.id)

    loaded = await Channel.with_("shouts").find(ch.id)
    assert [s.body for s in loaded.shouts] == ["hey"]
    empty = await Channel.with_("shouts").find(other.id)
    assert empty.shouts == []


async def test_morph_owner_with_a_custom_primary_key(db_url):
    """Owners are not guaranteed an ``id`` pk — the join must use the owner's
    declared primary key, not a hard-coded column name."""
    from fastplace.orm import morph_many

    class Tagging(Model):
        __tablename__ = "cpk_taggings"

        id: int = Field(primary_key=True)
        label: str
        taggable_type: str
        taggable_ref: str

    class Item(Model):
        __tablename__ = "cpk_items"

        # A natural key opts into mass assignment explicitly (create()).
        __fillable__ = ("ref", "name")

        ref: str = Field(primary_key=True)
        name: str

        taggings: list[Tagging] = morph_many(
            "Tagging", type_field="taggable_type", id_field="taggable_ref"
        )

    await db.create_all()
    item = await Item.create(ref="itm-1", name="thing")
    await Tagging.create(label="new", taggable_type="cpk_items", taggable_ref=item.ref)

    loaded = await Item.with_("taggings").find("itm-1")
    assert [t.label for t in loaded.taggings] == ["new"]


async def test_morph_to_returns_none_for_an_untyped_row(db_url):
    """A child saved without its parent pair yet (both sides nullable in
    practice) resolves to None — not a ValueError about an unknown type."""
    from fastplace.orm import morph_many, morph_to

    class Ping(Model):
        __tablename__ = "nullty_pings"

        id: int = Field(primary_key=True)
        note: str
        pingable_type: str | None = None
        pingable_id: int | None = None

        subject = morph_to("pingable_type", "pingable_id")

    class Host(Model):
        __tablename__ = "nullty_hosts"

        id: int = Field(primary_key=True)
        name: str

        pings: list[Ping] = morph_many("Ping", type_field="pingable_type", id_field="pingable_id")

    await db.create_all()
    orphan = await Ping.create(note="no parent yet")
    assert await orphan.morph_to() is None
