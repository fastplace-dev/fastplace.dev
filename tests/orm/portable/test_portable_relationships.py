"""Portable relationships — every relation flavor on every backend.

Covers the blueprint's compatibility-matrix "relationships" category:
has_many / has_one / belongs_to / many_to_many / self-referential, lazy
``relation()`` access, and eager ``with_()`` loading.
"""

from __future__ import annotations

import pytest

from fastplace.db import db
from fastplace.orm import (
    Field,
    Model,
    belongs_to,
    has_many,
    has_one,
    many_to_many,
    pivot_table,
)


@pytest.fixture()
def domain():
    """A small blog domain: Author → Stories/Bio, Story ↔ Tag, Node tree."""

    class Author(Model):
        __tablename__ = "port_authors"

        id: int = Field(primary_key=True)
        name: str

        stories: list[Story] = has_many("Story", back_populates="author")
        bio: Bio = has_one("Bio", back_populates="author")

    class Story(Model):
        __tablename__ = "port_stories"

        id: int = Field(primary_key=True)
        title: str
        author_id: int = Field(foreign_key="port_authors.id")

        author: Author = belongs_to("Author", back_populates="stories")
        tags: list[Tag] = many_to_many("Tag", secondary="port_story_tag", backref="stories")

    class Bio(Model):
        __tablename__ = "port_bios"

        id: int = Field(primary_key=True)
        text: str
        author_id: int = Field(foreign_key="port_authors.id", unique=True)

        author: Author = belongs_to("Author", back_populates="bio")

    class Tag(Model):
        __tablename__ = "port_tags"

        id: int = Field(primary_key=True)
        name: str

    class Node(Model):
        __tablename__ = "port_nodes"

        id: int = Field(primary_key=True)
        label: str = ""
        parent_id: int | None = Field(foreign_key="port_nodes.id", nullable=True)
        children: list[Node] = has_many("Node", backref="parent")

    pivot_table("port_story_tag", "port_stories", "port_tags")
    return type(
        "Domain",
        (),
        {"Author": Author, "Story": Story, "Bio": Bio, "Tag": Tag, "Node": Node},
    )


@pytest.fixture()
async def schema(domain, backend):
    await db.create_all()
    return domain


async def test_has_many_and_belongs_to_round_trip(schema):
    domain = schema
    author = await domain.Author.create(name="Firoz")
    await domain.Story.create(title="One", author_id=author.id)
    await domain.Story.create(title="Two", author_id=author.id)

    stories = await author.relation("stories")
    assert sorted(s.title for s in stories) == ["One", "Two"]

    story = await domain.Story.first()
    owner = await story.relation("author")
    assert owner.name == "Firoz"


async def test_has_one_returns_the_single_related_row(schema):
    domain = schema
    author = await domain.Author.create(name="Solo")
    await domain.Bio.create(text="writes frameworks", author_id=author.id)

    bio = await author.relation("bio")
    assert bio.text == "writes frameworks"


async def test_many_to_many_attaches_both_directions(schema):
    domain = schema
    author = await domain.Author.create(name="Tagger")
    story = await domain.Story.create(title="Tagged", author_id=author.id)
    hot = await domain.Tag.create(name="hot")
    fresh = await domain.Tag.create(name="fresh")

    await story.relation("tags")  # load the collection before mutating it
    story.tags.append(hot)
    story.tags.append(fresh)
    await story.save()

    reloaded = await domain.Story.with_("tags").find(story.id)
    assert sorted(t.name for t in reloaded.tags) == ["fresh", "hot"]

    tag_side = await domain.Tag.with_("stories").find(hot.id)
    assert [s.title for s in tag_side.stories] == ["Tagged"]


async def test_self_referential_tree(schema):
    domain = schema
    root = await domain.Node.create(label="root")
    child = await domain.Node.create(label="leaf", parent_id=root.id)

    kids = await root.relation("children")
    assert [k.label for k in kids] == ["leaf"]

    parent = await child.relation("parent")
    assert parent is not None and parent.label == "root"


async def test_eager_with_loads_relations_upfront(schema):
    """`with_()` loads relations in the initial query — after `.get()` the
    relationship is already present, so plain attribute access is a plain
    memory read on every backend (lazy='raise' stays armed otherwise)."""
    from sqlalchemy.exc import InvalidRequestError

    domain = schema
    author = await domain.Author.create(name="Eager")
    await domain.Story.create(title="E1", author_id=author.id)
    await domain.Story.create(title="E2", author_id=author.id)

    # Without with_(): unloaded attribute access raises (N+1 guard).
    plain = await domain.Author.find(author.id)
    with pytest.raises(InvalidRequestError):
        _ = plain.stories

    loaded = await domain.Author.query().with_("stories").where(domain.Author.id == author.id).get()
    assert len(loaded) == 1
    assert sorted(s.title for s in loaded[0].stories) == ["E1", "E2"]
