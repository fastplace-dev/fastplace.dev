"""Relationship tests — has_many, belongs_to, has_one, many_to_many, eager loading."""

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
def blog(db_url):
    """A small blog domain: User → Posts, Profile, Comments; Article ↔ Tag."""

    class User(Model):
        __tablename__ = "users"

        id: int = Field(primary_key=True)
        name: str

        posts: list[Post] = has_many("Post", back_populates="author")
        profile: Profile = has_one("Profile", back_populates="user")
        comments: list[Comment] = has_many("Comment", backref="author")

    class Post(Model):
        __tablename__ = "posts"

        id: int = Field(primary_key=True)
        title: str
        user_id: int = Field(foreign_key="users.id")

        author: User = belongs_to("User", back_populates="posts")

    class Profile(Model):
        __tablename__ = "profiles"

        id: int = Field(primary_key=True)
        bio: str
        user_id: int = Field(foreign_key="users.id", unique=True)

        user: User = belongs_to("User", back_populates="profile")

    class Comment(Model):
        __tablename__ = "comments"

        id: int = Field(primary_key=True)
        body: str
        user_id: int = Field(foreign_key="users.id")

    class Article(Model):
        __tablename__ = "articles"

        id: int = Field(primary_key=True)
        title: str
        tags: list[Tag] = many_to_many("Tag", secondary="article_tag", backref="articles")

    class Tag(Model):
        __tablename__ = "tags"

        id: int = Field(primary_key=True)
        name: str

    pivot_table("article_tag", "articles", "tags")
    return type(
        "Blog",
        (),
        {
            "User": User,
            "Post": Post,
            "Profile": Profile,
            "Comment": Comment,
            "Article": Article,
            "Tag": Tag,
        },
    )


@pytest.fixture()
async def schema(blog):
    await db.create_all()
    return blog


async def test_has_many_lazy_load(schema):
    blog = schema
    user = await blog.User.create(name="Firoz")
    await blog.Post.create(title="Post 1", user_id=user.id)
    await blog.Post.create(title="Post 2", user_id=user.id)

    posts = await user.relation("posts")
    assert len(posts) == 2


async def test_belongs_to(schema):
    blog = schema
    user = await blog.User.create(name="Firoz")
    post = await blog.Post.create(title="P", user_id=user.id)

    author = await post.relation("author")
    assert author.name == "Firoz"


async def test_backref_relationship(schema):
    blog = schema
    user = await blog.User.create(name="Firoz")
    await blog.Comment.create(body="nice", user_id=user.id)

    loaded = await blog.User.with_("comments").find(user.id)
    assert [c.body for c in loaded.comments] == ["nice"]
    # backref side is also a working relationship:
    comment = await blog.Comment.query().where(blog.Comment.body == "nice").first()
    author = await comment.relation("author")
    assert author.name == "Firoz"


async def test_eager_loading_with_(schema):
    blog = schema
    user = await blog.User.create(name="Firoz")
    await blog.Post.create(title="Post 1", user_id=user.id)
    await blog.Post.create(title="Post 2", user_id=user.id)

    loaded = await blog.User.with_("posts").find(user.id)
    # Already loaded — plain attribute access, no lazy IO:
    assert len(loaded.posts) == 2


async def test_has_one(schema):
    blog = schema
    user = await blog.User.create(name="Firoz")
    await blog.Profile.create(bio="dev", user_id=user.id)

    profile = await user.relation("profile")
    assert profile.bio == "dev"


async def test_many_to_many(schema):
    blog = schema
    article = await blog.Article.create(title="tagged")
    tag = await blog.Tag.create(name="orm")
    await article.relation("tags")  # load collection first

    article.tags.append(tag)
    await article.save()

    fresh = await blog.Article.with_("tags").find(article.id)
    assert [t.name for t in fresh.tags] == ["orm"]

    tag_side = await blog.Tag.with_("articles").find(tag.id)
    assert [a.title for a in tag_side.articles] == ["tagged"]


async def test_nested_eager_loading(schema):
    blog = schema
    user = await blog.User.create(name="Firoz")
    post = await blog.Post.create(title="Post", user_id=user.id)
    await blog.Profile.create(bio="b", user_id=user.id)

    loaded = await blog.Post.with_("author.profile").find(post.id)
    assert loaded.author.profile.bio == "b"


async def test_unloaded_relationship_attribute_raises_instead_of_hidden_io(schema):
    """lazy='raise' is the N+1 guard: plain attribute access on an unloaded
    relationship must fail loudly, never silently run synchronous IO."""
    from sqlalchemy.exc import InvalidRequestError

    blog = schema
    user = await blog.User.create(name="Firoz")
    await blog.Post.create(title="Post 1", user_id=user.id)

    fresh = await blog.User.find(user.id)  # loaded WITHOUT .with_("posts")
    with pytest.raises(InvalidRequestError):
        _ = fresh.posts

    # The sanctioned lazy path still works after the raise.
    posts = await fresh.relation("posts")
    assert [p.title for p in posts] == ["Post 1"]


async def test_eager_loaded_relationship_attribute_does_not_raise(schema):
    """The other half of the contract: with_() loads the relationship, so
    plain attribute access is a plain memory read."""
    blog = schema
    user = await blog.User.create(name="Firoz")
    await blog.Post.create(title="Post 1", user_id=user.id)

    loaded = await blog.User.with_("posts").find(user.id)
    assert [p.title for p in loaded.posts] == ["Post 1"]
