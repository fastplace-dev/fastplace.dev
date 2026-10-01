"""make:model multi-case naming — the module segment derives via _snake.

`fastplace make:model BlogPost` used to build a mixed-case module
(``app/modules/blogPost`` with table ``blogPosts``); every other maker
snake-cases, and make:module turns the same input into ``blog_post``. The
derived module must match that convention (case-insensitive filesystems
also collide blogPost/ blogpost).
"""

from __future__ import annotations

from typer.testing import CliRunner

from fastplace.cli import app as cli_app

# Autouse fixture: clean db/model/module state per test (see _isolation.py).
from tests.cli._isolation import isolate_project_state  # noqa: F401

runner = CliRunner()


def test_multicase_model_lands_in_snake_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:model", "BlogPost"])

    assert result.exit_code == 0, result.output
    model = tmp_path / "app" / "modules" / "blog_post" / "models" / "blog_post.py"
    assert model.is_file(), "expected snake_case module blog_post"
    source = model.read_text()
    assert "class BlogPost(Model):" in source
    assert '__tablename__ = "blog_posts"' in source
    # No mixed-case directory anywhere under app/modules.
    modules = tmp_path / "app" / "modules"
    assert [p.name for p in modules.iterdir() if p.is_dir()] == ["blog_post"]


def test_multicase_model_summary_and_companions_agree(tmp_path, monkeypatch):
    """The echoed module/table and companion paths all use the same stem."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:model", "BlogPost", "-s"])

    assert result.exit_code == 0, result.output
    assert "module blog_post" in result.output
    assert "table blog_posts" in result.output
    service = tmp_path / "app" / "modules" / "blog_post" / "services" / "blog_post_service.py"
    assert service.is_file()
    assert "class BlogPostService:" in service.read_text()


def test_make_model_and_make_module_agree_on_the_same_stem(tmp_path, monkeypatch):
    """`make:model BlogPost` and `make:module blog_post` describe one module."""
    monkeypatch.chdir(tmp_path)

    assert runner.invoke(cli_app, ["make:model", "BlogPost"]).exit_code == 0
    assert runner.invoke(cli_app, ["make:module", "blog_post", "--bare"]).exit_code == 0

    modules = tmp_path / "app" / "modules"
    assert [p.name for p in modules.iterdir() if p.is_dir()] == ["blog_post"]


def test_single_word_model_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_app, ["make:model", "Product"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "app" / "modules" / "product" / "models" / "product.py").is_file()
    assert "module product" in result.output
