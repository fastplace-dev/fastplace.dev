"""record-rule storage: slug routing, frontmatter, index regeneration."""

from __future__ import annotations

from pathlib import Path

from fastplace.mcp.rules import RuleRepository


def _repo(tmp_path: Path) -> RuleRepository:
    return RuleRepository(tmp_path / ".fastplace" / "rules", base_path=tmp_path)


def test_write_creates_area_file_with_frontmatter(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    location = repo.write(
        "app/http/controllers/**", "Extend BaseController", "Use it for tenant scoping."
    )

    assert location.read_text().startswith("---\npaths:\n- app/http/controllers/**\n---")
    assert "# Controllers" in location.read_text()
    assert "## Extend BaseController" in location.read_text()
    assert "Use it for tenant scoping." in location.read_text()


def test_same_area_routes_to_same_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = repo.write("app/http/controllers/**", "First", "one")
    second = repo.write("app/http/controllers/**", "Second", "two")

    assert first == second
    body = first.read_text()
    assert "## First" in body and "## Second" in body


def test_each_area_file_keeps_its_own_globs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    controllers = repo.write("app/http/controllers/**", "Rule A", "a")
    middleware = repo.write("app/http/middleware/**", "Rule B", "b")

    assert "- app/http/controllers/**" in controllers.read_text()
    assert "- app/http/middleware/**" in middleware.read_text()
    assert "- app/http/middleware/**" not in controllers.read_text()


def test_new_file_per_distinct_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    controllers = repo.write("app/http/controllers/**", "Rule A", "a")
    middleware = repo.write("app/http/middleware/**", "Rule B", "b")

    assert controllers != middleware
    assert controllers.name != middleware.name


def test_index_lists_rule_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.write("app/http/controllers/**", "Rule A", "a")
    repo.write("resources/js/pages/**", "Rule B", "b")

    index = repo.index_path.read_text()

    assert "# Project Rules Index" in index
    matching = [line for line in index.splitlines() if "controllers" in line]
    assert matching and "app/http/controllers/**" in matching[0]


def test_slug_collision_gets_suffix(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".fastplace" / "rules"
    rules_dir.mkdir(parents=True)
    # A stray file already occupies the natural slug (no parseable frontmatter);
    # the next candidate adds the parent directory for context.
    (rules_dir / "models.md").write_text("leftover notes")

    repo = _repo(tmp_path)
    location = repo.write("app/models/*.py", "Rule", "note")

    assert location == rules_dir / "app-models.md"


def test_empty_fields_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    import pytest

    with pytest.raises(ValueError):
        repo.write("  ", "Title", "note")
    with pytest.raises(ValueError):
        repo.write("app/**", "", "note")
    with pytest.raises(ValueError):
        repo.write("app/**", "Title", "   ")


def test_title_newlines_flattened(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    location = repo.write("app/**", "Multi\nline\r\ntitle", "note")

    assert "## Multi line title" in location.read_text()


def test_glob_normalized_relative(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    location = repo.write("/app/http/controllers/**", "Rule", "note")

    assert "- app/http/controllers/**" in location.read_text()
