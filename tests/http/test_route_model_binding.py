"""Route model binding — annotated path params resolve to model instances.

The kernel resolves handler parameters type-annotated with a Model subclass
against the route's path params via ``find_or_fail`` BEFORE the middleware
chain and controller run: the controller (and every ``can:`` middleware that
names the param) receives the live instance, a missing row is a 404, and
unannotated params stay raw strings.
"""

import httpx
import pytest

from fastplace.db import db
from fastplace.http.kernel import get_app
from fastplace.http.middleware import Middleware
from fastplace.http.router import Router
from fastplace.orm import Field, Model


@pytest.fixture()
def posts(monkeypatch: pytest.MonkeyPatch):
    """One tiny domain table on an in-memory SQLite — the orm conftest's
    ``db_url`` shape, inlined (tests/http does not see that conftest)."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DATABASE_DRIVER", "sqlite")

    class Post(Model):
        __tablename__ = "posts"

        id: int = Field(primary_key=True)
        title: str

    class Holder:
        pass

    Holder.Post = Post

    from fastplace.db import reset_db

    reset_db()
    yield Holder
    reset_db()


@pytest.fixture()
def seeded(posts):
    async def _seed():
        await db.create_all()
        await posts.Post.create(title="first")
        await posts.Post.create(title="second")

    return _seed


def _client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_annotated_path_param_resolves_the_model_instance(posts, seeded):
    async def show(request, post: posts.Post):
        from fastplace.http.response import Json

        return Json({"title": post.title, "kind": type(post).__name__})

    router = Router()
    router.get("/posts/{post}", show)
    app = get_app(routes=router, config={"APP_ENV": "local"})
    await seeded()
    async with _client(app) as client:
        response = await client.get("/posts/2")
    assert response.status_code == 200
    assert response.json() == {"title": "second", "kind": "Post"}


async def test_unannotated_path_param_stays_a_raw_string(posts, seeded):
    async def show(request):
        from fastplace.http.response import Json

        return Json({"raw": request.path_params["post"]})

    router = Router()
    router.get("/posts/{post}", show)
    app = get_app(routes=router, config={"APP_ENV": "local"})
    await seeded()
    async with _client(app) as client:
        response = await client.get("/posts/2")
    assert response.status_code == 200
    assert response.json() == {"raw": "2"}


async def test_missing_record_is_a_404(posts, seeded):
    async def show(request, post: posts.Post):
        from fastplace.http.response import Json

        return Json({"title": post.title})

    router = Router()
    router.get("/posts/{post}", show)
    app = get_app(routes=router, config={"APP_ENV": "local"})
    await seeded()
    async with _client(app) as client:
        response = await client.get("/posts/999")
    assert response.status_code == 404


async def test_binding_runs_before_route_middleware_sees_the_param(posts, seeded):
    seen: list[object] = []

    class SpyMiddleware(Middleware):
        async def handle(self, request, call_next):
            seen.append(request.path_params.get("post"))
            return await call_next(request)

    async def show(request, post: posts.Post):
        from fastplace.http.response import Json

        return Json({"title": post.title})

    router = Router()
    router.get("/posts/{post}", show, middleware=["spy"])
    app = get_app(
        routes=router,
        config={"APP_ENV": "local"},
        route_middleware={"spy": SpyMiddleware()},
    )
    await seeded()
    async with _client(app) as client:
        response = await client.get("/posts/1")
    assert response.status_code == 200
    assert isinstance(seen[0], posts.Post)
    assert seen[0].title == "first"


async def test_only_annotated_params_are_bound(posts, seeded):
    async def show(request, post: posts.Post):
        from fastplace.http.response import Json

        return Json({"title": post.title, "kind_raw": request.path_params["kind"]})

    router = Router()
    router.get("/posts/{post}/{kind}", show)
    app = get_app(routes=router, config={"APP_ENV": "local"})
    await seeded()
    async with _client(app) as client:
        response = await client.get("/posts/2/draft")
    assert response.status_code == 200
    assert response.json() == {"title": "second", "kind_raw": "draft"}


# -- annotation resolution contract -----------------------------------------


def test_unresolvable_path_param_annotation_fails_registration_loudly():
    # from __future__ import annotations + a TYPE_CHECKING-only model import
    # leaves the path param a dead string: silent skip would 500 on every
    # request with a misleading TypeError. Registration must fail instead.
    from fastplace.errors import ConfigurationError
    from fastplace.http.router import route_bindings

    def show(request, project):
        raise NotImplementedError

    show.__annotations__ = {"project": "Project"}  # no such name in this module
    with pytest.raises(ConfigurationError, match="project"):
        route_bindings(show, "/projects/{project}")


def test_unresolvable_annotations_off_the_path_still_register_silently():
    # The loud failure is scoped to path params — a binding was intended
    # there. Annotations the route does not bind stay non-fatal.
    from fastplace.http.router import route_bindings

    def show(request, filters, project):
        raise NotImplementedError

    show.__annotations__ = {"filters": "MissingThing"}
    assert route_bindings(show, "/projects/{project}") == ()


def test_live_annotations_survive_a_failing_hint_resolution(posts):
    # The existing fallback stands: when get_type_hints dies, live
    # (already-evaluated) annotations still bind.
    from fastplace.http.router import route_bindings

    def show(request, project):
        raise NotImplementedError

    show.__annotations__ = {"project": posts.Post, "extra": "MissingThing"}
    assert route_bindings(show, "/projects/{project}") == (("project", posts.Post),)
